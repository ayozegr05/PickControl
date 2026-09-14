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
from app.services.results.verifier import verify_pending_picks
from app.services.telegram.client import get_telegram_client, reset_telegram_client
from app.services.telegram.handlers import register_handlers

logger = get_logger("app.lifecycle")


async def _run_telegram_listener() -> None:
    settings = get_settings()
    client = get_telegram_client()
    register_handlers(client)

    await client.start(phone=settings.telegram_phone)
    logger.info("[TELEGRAM_LISTENER] Cliente de Telegram conectado y escuchando.")
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
            await verify_pending_picks()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[RESULTS_VERIFIER] Error verificando picks pendientes: %s", exc
            )
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
