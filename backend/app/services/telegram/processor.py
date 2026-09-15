"""Procesamiento de mensajes de Telegram recibidos por Telethon.

Guarda el mensaje crudo y, si hay clave de OpenAI, intenta extraer un pick
mediante el extractor híbrido (reglas + LLM).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.pick_service import to_naive_utc
from app.services.telegram.pick_extractor import ExtractedPick, extract_pick

logger = get_logger("app.telegram")

# Ventana de tiempo en la que consideramos que dos picks del mismo canal
# pueden ser el mismo pronóstico repetido (p. ej. foto + texto explicativo
# enviados por separado por el tipster).
_DUPLICATE_WINDOW = timedelta(hours=6)
_DUPLICATE_TEXT_SIMILARITY = 0.8
_DUPLICATE_TEXT_SIMILARITY_WITH_MATCHING_CUOTA = 0.6


def _text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


async def _find_duplicate_pick(
    session: AsyncSession, channel: str, pick: ExtractedPick
) -> ParsedPick | None:
    """Busca un ParsedPick muy similar ya guardado para el mismo canal.

    Sirve para evitar duplicar el mismo pronóstico cuando el tipster lo
    envía primero como imagen (OCR) y luego lo repite como texto (o al
    revés).
    """
    if not pick.seleccion:
        return None

    cutoff = utc_now() - _DUPLICATE_WINDOW
    result = await session.exec(
        select(ParsedPick)
        .where(ParsedPick.informante == channel)
        .where(ParsedPick.es_apuesta == True)  # noqa: E712
        .where(ParsedPick.created_at >= cutoff)
    )
    for candidate in result.all():
        if not candidate.seleccion:
            continue

        similarity = _text_similarity(candidate.seleccion, pick.seleccion)
        if similarity >= _DUPLICATE_TEXT_SIMILARITY:
            return candidate

        cuotas_coinciden = (
            pick.cuota is not None
            and candidate.cuota is not None
            and abs(candidate.cuota - pick.cuota) < 0.01
        )
        if (
            cuotas_coinciden
            and similarity >= _DUPLICATE_TEXT_SIMILARITY_WITH_MATCHING_CUOTA
        ):
            return candidate

    return None


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
    message_date: datetime | None = None,
) -> None:
    """Punto de entrada único para procesar un mensaje entrante de Telegram.

    `message_date` es la fecha/hora real del mensaje de Telegram (no la
    de procesado). Se usa como aproximación de la fecha del evento
    cuando el texto no la menciona explícitamente (la mayoría de
    tipsters publican el pick el mismo día del partido).
    """
    message = IncomingTelegramMessage(
        channel=channel, channel_id=channel_id, message_id=message_id, text=text
    )
    logger.info("[TELEGRAM_PROCESSOR] Mensaje listo para procesar: %s", message)

    source_text = (extracted_text or text or "").strip()
    naive_message_date = to_naive_utc(message_date) if message_date else None

    settings = get_settings()
    pick = None
    if settings.openai_api_key and source_text:
        pick = await extract_pick(
            source_text, settings.openai_api_key, informante=channel
        )
        if pick:
            if pick.fecha_evento is None and naive_message_date is not None:
                # Aproximación: sin fecha explícita en el texto, asumimos
                # que el pick se publicó el mismo día del partido.
                pick.fecha_evento = naive_message_date
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
            received_at=naive_message_date or utc_now(),
        )
        session.add(raw)
        await session.flush()

        if pick:
            duplicate = None
            if pick.es_apuesta:
                duplicate = await _find_duplicate_pick(session, channel, pick)

            if duplicate:
                logger.info(
                    "[TELEGRAM_PROCESSOR] Pick duplicado en canal %s (ya existe "
                    "ParsedPick id=%s): '%s' ~ '%s'. No se crea de nuevo.",
                    channel,
                    duplicate.id,
                    pick.seleccion,
                    duplicate.seleccion,
                )
                if duplicate.fecha_evento is None and pick.fecha_evento is not None:
                    # El mensaje duplicado puede traer información (p. ej.
                    # la fecha del evento en la foto del boleto) que el
                    # original no tenía. La fusionamos en vez de perderla.
                    duplicate.fecha_evento = pick.fecha_evento
                    session.add(duplicate)
                    logger.info(
                        "[TELEGRAM_PROCESSOR] Fecha de evento completada en "
                        "pick id=%s a partir del duplicado.",
                        duplicate.id,
                    )
            else:
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
                    informante=channel,
                    explicacion=pick.explicacion,
                    fecha_evento=pick.fecha_evento,
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
