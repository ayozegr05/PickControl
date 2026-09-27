"""Ciclo de vida de servicios en segundo plano de la aplicación.

Arranca y detiene el listener de Telegram (Telethon) de forma
concurrente con el servidor FastAPI, usando el patrón `lifespan`
(ver `app/main.py`) en vez de los eventos `on_event` (deprecados).
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.maintenance.rescue import run_rescue_cycle
from app.services.odds.historical_backfill import count_pending_backfill, run
from app.services.odds.snapshotter import run_odds_snapshot_cycle
from app.services.results.base import loop_due, mark_loop_ran
from app.services.results.verifier import verify_pending_picks
from app.services.telegram.catchup import run_catchup
from app.services.telegram.channels import (
    refresh_channel_cache,
    seed_channels_from_env,
)
from app.services.telegram.client import get_telegram_client, reset_telegram_client
from app.services.telegram.handlers import register_handlers

logger = get_logger("app.lifecycle")


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
        ):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                logger.info("[LIFECYCLE] %s detenido.", name)
