"""Ciclo de vida de servicios en segundo plano de la aplicación.

Arranca y detiene el listener de Telegram (Telethon) de forma
concurrente con el servidor FastAPI, usando el patrón `lifespan`
(ver `app/main.py`) en vez de los eventos `on_event` (deprecados).
"""

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.maintenance.backlog import run_backlog_verification
from app.services.maintenance.rescue import run_rescue_cycle
from app.services.odds import static_pages
from app.services.odds.historical_backfill import (
    count_pending_backfill,
    run,
    run_gemini_odds_pass,
)
from app.services.odds.snapshotter import run_odds_snapshot_cycle
from app.services.results import compat_llm
from app.services.results.base import loop_due, mark_loop_ran
from app.services.results.verifier import (
    recheck_suspicious_voids,
    verify_pending_picks,
)
from app.services.telegram.catchup import run_catchup
from app.services.telegram.channels import (
    refresh_channel_cache,
    seed_channels_from_env,
)
from app.services.telegram.client import get_telegram_client, reset_telegram_client
from app.services.telegram.handlers import register_handlers

logger = get_logger("app.lifecycle")

# El barrido correctivo de anuladas corre una vez al día dentro del
# loop del verifier (usa los mismos providers y misma puerta de keys).
_VOID_RECHECK_INTERVAL_SECONDS = 24 * 3600
# El barrido de pendientes fuera de ventana (>14 días, patas de
# combinadas viejas sobre todo) también una vez al día — antes era
# solo script manual y los padres se quedaban abiertos para siempre.
_BACKLOG_INTERVAL_SECONDS = 24 * 3600
# Goteo de cuotas históricas: esperar al backfill diario dejaba el
# grifo cerrado todo el día. Sin candidatos o en cooldown la pasada
# sale gratis (early-exit, ni una llamada al modelo).
# Con un extractor compat vivo (Groq/Cerebras/OpenRouter) el goteo
# corre cada 3 min — sus cooldowns duran segundos-minutos. Cuando
# solo queda Gemini, la cadencia vuelve a ~20 min: su free tier no
# da para más y así no se machaca el fallback.
_GEMINI_ODDS_INTERVAL_SECONDS = 3 * 60
_GEMINI_ONLY_INTERVAL_SECONDS = 20 * 60


async def _run_telegram_listener() -> None:
    settings = get_settings()
    client = get_telegram_client()
    await seed_channels_from_env()
    register_handlers(client)

    await client.start(phone=settings.telegram_phone)
    logger.info("[TELEGRAM_LISTENER] Cliente de Telegram conectado y escuchando.")
    await refresh_channel_cache(client)
    await run_catchup(client)
    await client.run_until_disconnected()


async def _run_results_verifier_loop() -> None:
    """Revisa periódicamente picks pendientes de verificar resultados.

    Corre en segundo plano mientras el backend esté arriba; no bloquea
    el arranque ni el listener de Telegram.
    """
    settings = get_settings()
    interval_seconds = settings.results_verification_interval_hours * 3600

    while True:
        try:
            # loop_due evita una pasada extra en cada restart/redeploy:
            # solo corre si la última pasada real ya venció el intervalo.
            if loop_due("verifier", interval_seconds):
                await verify_pending_picks()
                mark_loop_ran("verifier")
            # Barrido correctivo de anuladas sospechosas: una pasada al
            # día (no en cada ciclo), calendario D+1/D+3/D+6 por pick.
            if loop_due("void_recheck", _VOID_RECHECK_INTERVAL_SECONDS):
                await recheck_suspicious_voids()
                mark_loop_ran("void_recheck")
            # Backlog: pendientes fuera de la ventana de 14 días que el
            # ciclo normal ya no reintenta (patas de combinadas viejas).
            if loop_due("verify_backlog", _BACKLOG_INTERVAL_SECONDS):
                stats = await run_backlog_verification(
                    apply=True, delay=2.0, log=logger.info
                )
                mark_loop_ran("verify_backlog")
                if stats["intentados"]:
                    logger.info(
                        "[VERIFY_BACKLOG] %s intentados, %s resueltos, "
                        "%s padres liquidados",
                        stats["intentados"],
                        stats["resueltos"],
                        stats["padres_liquidados"],
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[RESULTS_VERIFIER] Error verificando picks pendientes: %s", exc
            )
        await asyncio.sleep(interval_seconds)


async def _run_odds_snapshotter_loop() -> None:
    """Captura periódica de cuotas de mercado para los picks.

    Mismo patrón que el verificador: loop en segundo plano que no
    bloquea el arranque; un fallo de ciclo no tumba la tarea.
    """
    settings = get_settings()
    interval_seconds = settings.odds_snapshot_interval_minutes * 60

    while True:
        try:
            if loop_due("odds_snapshotter", interval_seconds):
                await run_odds_snapshot_cycle()
                mark_loop_ran("odds_snapshotter")
        except Exception as exc:  # noqa: BLE001
            logger.error("[ODDS_SNAPSHOTTER] Error en el ciclo de snapshots: %s", exc)
        await asyncio.sleep(interval_seconds)


async def _run_rescue_loop() -> None:
    """Rescate periódico: OCR de fotos pendientes + reproceso de raws
    + reparación de extracciones rotas (autorregulación).

    Solo consume OpenAI cuando hay trabajo pendiente; en reposo son
    unas consultas a la BD y nada más.
    """
    from app.services.maintenance.repair import run_repair_cycle

    settings = get_settings()
    interval_seconds = settings.rescue_interval_hours * 3600

    while True:
        try:
            if loop_due("rescue", interval_seconds):
                await run_rescue_cycle()
                # Tras el rescue: re-extrae las filas pendientes con
                # diagnóstico de extracción rota (combinadas con patas
                # de ruido, evento = torneo, 1X como hándicap...).
                await run_repair_cycle()
                mark_loop_ran("rescue")
        except Exception as exc:  # noqa: BLE001
            logger.error("[RESCUE] Error en el ciclo de rescate: %s", exc)
        await asyncio.sleep(interval_seconds)


async def _run_odds_backfill_loop() -> None:
    """Backfill diario de cuotas históricas (OddsPapi).

    Early-exit: si no hay backlog pendiente no se llama a la API — la
    tarea es finita y se auto-apaga cuando el histórico esté completo.
    """
    from app.db.postgres import AsyncSessionLocal

    settings = get_settings()
    interval_seconds = settings.odds_backfill_interval_hours * 3600

    while True:
        try:
            if loop_due("odds_backfill", interval_seconds):
                # Canario de los listados estáticos: descarga HOY y
                # cuenta filas parseables; avisa por push si el
                # layout se rompió. Va antes del early-exit — corre
                # aunque no haya backlog (no gasta cuota de nada).
                try:
                    async with httpx.AsyncClient(timeout=60) as _http:
                        await static_pages.canary(_http)
                except Exception as cexc:  # noqa: BLE001
                    logger.warning("[ODDS_BACKFILL] canario estático: %s", cexc)
                async with AsyncSessionLocal() as session:
                    pending = await count_pending_backfill(session)
                if pending:
                    report = await run(apply=True, limit=None)
                    logger.info(
                        "[ODDS_BACKFILL] Pasada diaria: %s pendientes, "
                        "%s filas insertadas.",
                        pending,
                        report.filas,
                    )
                mark_loop_ran("odds_backfill")
        except Exception as exc:  # noqa: BLE001
            logger.error("[ODDS_BACKFILL] Error en el ciclo de backfill: %s", exc)
        await asyncio.sleep(interval_seconds)


async def _run_gemini_odds_loop() -> None:
    """Drenaje a goteo de la cola de cuotas históricas vía Gemini.

    Corre independiente del backfill diario: procesa hasta
    `_GEMINI_MAX_EVENTS_PER_RUN` eventos por pasada y se corta solo
    cuando toda la cascada entra en cooldown. El intervalo es
    dinámico: 3 min con extractor compat vivo, ~20 si solo queda
    Gemini — su cuota mínima manda el ritmo del fallback.
    """
    while True:
        try:
            interval = (
                _GEMINI_ODDS_INTERVAL_SECONDS
                if compat_llm.has_live_endpoint()
                else _GEMINI_ONLY_INTERVAL_SECONDS
            )
            if loop_due("llm_odds", interval):
                report = await run_gemini_odds_pass(apply=True)
                if report is not None:
                    mark_loop_ran("llm_odds")
                    logger.info(
                        "[GEMINI_ODDS] Goteo: %s mapeados, %s filas.",
                        report.mapeados,
                        report.filas,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error("[GEMINI_ODDS] Error en el goteo de cuotas: %s", exc)
        await asyncio.sleep(_GEMINI_ODDS_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown de FastAPI: arranca el listener de Telegram como
    una tarea concurrente y la cancela limpiamente al apagar el server.
    """
    settings = get_settings()
    telegram_task: asyncio.Task | None = None

    if settings.telegram_api_id and settings.telegram_api_hash:
        telegram_task = asyncio.create_task(_run_telegram_listener())
        logger.info("[LIFECYCLE] Listener de Telegram iniciado en segundo plano.")
    else:
        logger.warning(
            "[LIFECYCLE] TELEGRAM_API_ID/TELEGRAM_API_HASH no configurados; "
            "el listener de Telegram no se iniciará."
        )

    results_task: asyncio.Task | None = None
    if settings.football_data_api_key or settings.api_football_key:
        results_task = asyncio.create_task(_run_results_verifier_loop())
        logger.info(
            "[LIFECYCLE] Verificador de resultados iniciado en segundo plano "
            "(cada %sh).",
            settings.results_verification_interval_hours,
        )
    else:
        logger.warning(
            "[LIFECYCLE] FOOTBALL_DATA_API_KEY/API_FOOTBALL_KEY no configurados; "
            "la verificación automática de resultados no se ejecutará."
        )

    odds_task: asyncio.Task | None = None
    if settings.rapidapi_tennis_key:
        odds_task = asyncio.create_task(_run_odds_snapshotter_loop())
        logger.info(
            "[LIFECYCLE] Snapshotter de odds iniciado en segundo plano "
            "(cada %s min).",
            settings.odds_snapshot_interval_minutes,
        )

    rescue_task: asyncio.Task | None = None
    if settings.openai_api_key:
        rescue_task = asyncio.create_task(_run_rescue_loop())
        logger.info(
            "[LIFECYCLE] Rescate OCR/reproceso iniciado en segundo plano "
            "(cada %sh).",
            settings.rescue_interval_hours,
        )

    backfill_task: asyncio.Task | None = None
    if settings.rapidapi_tennis_key:
        backfill_task = asyncio.create_task(_run_odds_backfill_loop())
        logger.info(
            "[LIFECYCLE] Backfill de cuotas históricas iniciado en segundo "
            "plano (cada %sh).",
            settings.odds_backfill_interval_hours,
        )

    gemini_odds_task: asyncio.Task | None = None
    if settings.google_api_key or compat_llm.configured(settings):
        gemini_odds_task = asyncio.create_task(_run_gemini_odds_loop())
        logger.info(
            "[LIFECYCLE] Goteo de cuotas Gemini iniciado en segundo plano "
            "(cada %s min con extractor vivo; %s si solo queda Gemini).",
            _GEMINI_ODDS_INTERVAL_SECONDS // 60,
            _GEMINI_ONLY_INTERVAL_SECONDS // 60,
        )

    try:
        yield
    finally:
        if telegram_task is not None:
            client = get_telegram_client()
            if client.is_connected():
                await client.disconnect()

            telegram_task.cancel()
            try:
                await telegram_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

            reset_telegram_client()
            logger.info("[LIFECYCLE] Listener de Telegram detenido.")

        if results_task is not None:
            results_task.cancel()
            try:
                await results_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            logger.info("[LIFECYCLE] Verificador de resultados detenido.")

        if odds_task is not None:
            odds_task.cancel()
            try:
                await odds_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            logger.info("[LIFECYCLE] Snapshotter de odds detenido.")

        for task, name in (
            (rescue_task, "Rescate OCR/reproceso"),
            (backfill_task, "Backfill de cuotas"),
            (gemini_odds_task, "Goteo de cuotas Gemini"),
        ):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                logger.info("[LIFECYCLE] %s detenido.", name)
