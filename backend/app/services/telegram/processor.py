"""Stub de procesamiento de mensajes de Telegram.

Placeholder temporal: por ahora solo estructura y registra el mensaje
recibido, sin ninguna lógica de extracción de picks todavía. La
interpretación real del texto (vía LLM) se implementará en una fase
posterior; este módulo existe para fijar ya el contrato de datos
(`IncomingTelegramMessage`) y el punto único de entrada
(`process_incoming_message`) que usará esa fase, sin acoplar
`handlers.py` a los detalles de esa futura implementación.
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger("app.telegram")


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
