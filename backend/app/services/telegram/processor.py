"""Procesamiento de mensajes de Telegram recibidos por Telethon.

Por ahora la "procesión" es mínima: normaliza el mensaje y lo guarda en
la tabla `telegram_raw_messages`. Más adelante este mismo punto de entrada
se extenderá con un LLM para extraer picks y almacenarlos en `picks`.
"""

from dataclasses import dataclass

from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.telegram_raw_message import TelegramRawMessage

logger = get_logger("app.telegram")


@dataclass
class IncomingTelegramMessage:
    """Mensaje entrante ya normalizado, listo para su futuro procesado."""

    channel: str
    channel_id: int
    message_id: int
    text: str


async def process_incoming_message(
    *, channel: str, channel_id: int, message_id: int, text: str
) -> None:
    """Punto de entrada único para procesar un mensaje entrante de Telegram.

    TODO(fase-llm): sustituir este stub por la extracción real de picks
    (parseo/LLM) y el guardado en base de datos vía `pick_service`.
    """
    message = IncomingTelegramMessage(
        channel=channel, channel_id=channel_id, message_id=message_id, text=text
    )
    logger.info("[TELEGRAM_PROCESSOR] Mensaje listo para procesar: %s", message)

    async with AsyncSessionLocal() as session:
        raw = TelegramRawMessage(
            channel_id=channel_id,
            message_id=message_id,
            channel_name=channel,
            text=text or "",
        )
        session.add(raw)
        await session.commit()

    logger.info(
        "[TELEGRAM_PROCESSOR] Mensaje %s del canal %s guardado en BD",
        message_id,
        channel,
    )
