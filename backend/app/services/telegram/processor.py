"""Procesamiento de mensajes de Telegram recibidos por Telethon.

Guarda el mensaje crudo y, si hay clave de OpenAI, intenta extraer un pick
mediante el extractor híbrido (reglas + LLM).
"""

from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram.pick_extractor import extract_pick

logger = get_logger("app.telegram")


@dataclass
class IncomingTelegramMessage:
    """Mensaje entrante ya normalizado, listo para su futuro procesado."""

    channel: str
    channel_id: int
    message_id: int
    text: str


async def process_incoming_message(
    *,
    channel: str,
    channel_id: int,
    message_id: int,
    text: str,
    media_path: str | None = None,
    extracted_text: str | None = None,
) -> None:
    """Punto de entrada único para procesar un mensaje entrante de Telegram.

    TODO(fase-llm): sustituir este stub por la extracción real de picks
    (parseo/LLM) y el guardado en base de datos vía `pick_service`.
    """
    message = IncomingTelegramMessage(
        channel=channel, channel_id=channel_id, message_id=message_id, text=text
    )
    logger.info("[TELEGRAM_PROCESSOR] Mensaje listo para procesar: %s", message)

    source_text = (extracted_text or text or "").strip()

    settings = get_settings()
    pick = None
    if settings.openai_api_key and source_text:
        pick = await extract_pick(
            source_text, settings.openai_api_key, informante=channel
        )
        if pick:
            logger.info(
                "[TELEGRAM_PROCESSOR] Pick extraído (método=%s, confianza=%s): %s",
                pick.metodo,
                pick.confianza,
                pick.model_dump(exclude_none=True),
            )

    async with AsyncSessionLocal() as session:
        raw = TelegramRawMessage(
            channel_id=channel_id,
            message_id=message_id,
            channel_name=channel,
            text=text or "",
            media_path=media_path,
            extracted_text=extracted_text,
            processed=True if pick else False,
        )
        session.add(raw)
        await session.flush()

        if pick:
            parsed = ParsedPick(
                raw_message_id=raw.id,
                es_apuesta=pick.es_apuesta,
                apuesta=pick.seleccion,
                deporte=pick.deporte,
                evento=pick.evento,
                mercado=pick.mercado,
                seleccion=pick.seleccion,
                cuota=pick.cuota,
                stake=pick.stake,
                casa=pick.casa,
                informante=pick.informante or channel,
                explicacion=pick.explicacion,
                metodo=pick.metodo,
                confianza=pick.confianza,
            )
            session.add(parsed)

        await session.commit()

    logger.info(
        "[TELEGRAM_PROCESSOR] Mensaje %s del canal %s guardado en BD",
        message_id,
        channel,
    )
