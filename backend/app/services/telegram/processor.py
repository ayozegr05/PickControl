"""Procesamiento de mensajes de Telegram recibidos por Telethon.

Guarda el mensaje crudo y, si hay clave de OpenAI, intenta extraer un pick
mediante el extractor híbrido (reglas + LLM).
"""

import re
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
from app.services.pick_service import get_or_create_informante, to_naive_utc
from app.services.telegram.pick_extractor import ExtractedPick, extract_pick

logger = get_logger("app.telegram")

# Ventana de tiempo en la que consideramos que dos picks del mismo canal
# pueden ser el mismo pronóstico repetido (p. ej. foto + texto explicativo
# enviados por separado por el tipster).
_DUPLICATE_WINDOW = timedelta(hours=6)
_DUPLICATE_TEXT_SIMILARITY = 0.8
_DUPLICATE_TEXT_SIMILARITY_WITH_MATCHING_CUOTA = 0.6
# Similitud por conjunto de palabras (ignora el orden). Cubre casos como
# "Real Madrid gana" (texto) vs "GANA REAL MADRID" (OCR de la misma
# promo), donde el orden de las palabras cambia entre imagen y texto.
_DUPLICATE_WORD_SET_SIMILARITY = 0.85


def _text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _word_set_similarity(a: str, b: str) -> float:
    """Similitud por conjunto de palabras (índice de Jaccard), sin orden."""
    words_a = set(re.findall(r"\w+", a.lower()))
    words_b = set(re.findall(r"\w+", b.lower()))
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


# Palabras vacías que no aportan identidad al pick: el texto del tipster
# dice "Más 3 tarjetas en el partido" y el OCR "Más de 3 tarjetas" — sin
# filtrar stopwords ninguno es subconjunto del otro.
_STOPWORDS = {
    "a",
    "al",
    "de",
    "del",
    "el",
    "en",
    "la",
    "las",
    "los",
    "por",
    "para",
    "y",
    "o",
    "u",
    "con",
    "sin",
    "que",
    "se",
    "su",
    "sus",
    "un",
    "una",
    "es",
    "the",
}


def _word_subset(a: str, b: str) -> bool:
    """True si todas las palabras significativas de `a` están en `b`.

    Cubre el nombre parcial: el texto del tipster dice "Tom gana" y el
    OCR del boleto dice "Tom Gentzsch gana" — es el mismo pick aunque la
    similitud global sea baja. Se ignoran stopwords ("de", "en", "el"...)
    para que "Más de 3 tarjetas" ⊂ "Más 3 tarjetas en el partido". Exigimos
    mínimo 2 palabras significativas para que una selección genérica de
    una sola palabra ("Gana") no haga match con cualquier cosa del canal.
    """
    words_a = {w for w in re.findall(r"\w+", a.lower()) if w not in _STOPWORDS}
    words_b = {w for w in re.findall(r"\w+", b.lower()) if w not in _STOPWORDS}
    return len(words_a) >= 2 and words_a <= words_b


# Campos que el mensaje duplicado puede aportar al pick original cuando
# este no los tiene: la foto del boleto suele traer la cuota y el texto
# del tipster el stake (o la fecha del evento).
_MERGEABLE_FIELDS = ("fecha_evento", "cuota", "stake", "linea", "casa", "mercado")


def _merge_pick_data(target: ParsedPick, source: ExtractedPick) -> bool:
    """Copia a `target` los campos que le falten y `source` sí tenga."""
    merged = False
    for field in _MERGEABLE_FIELDS:
        if getattr(target, field) is None and getattr(source, field) is not None:
            setattr(target, field, getattr(source, field))
            merged = True
    return merged


async def _find_duplicate_pick(
    session: AsyncSession,
    channel: str,
    pick: ExtractedPick,
    message_date: datetime | None,
) -> ParsedPick | None:
    """Busca un ParsedPick muy similar ya guardado para el mismo canal.

    Sirve para evitar duplicar el mismo pronóstico cuando el tipster lo
    envía primero como imagen (OCR) y luego lo repite como texto (o al
    revés). La ventana se mide sobre la FECHA DEL MENSAJE
    (`received_at`), no sobre `created_at`: en una importación masiva
    todos los picks tienen created_at=hoy y selecciones genéricas como
    "Más de 1.5 goles" fusionarían partidos de días distintos.
    """
    if not pick.seleccion:
        return None

    query = (
        select(ParsedPick)
        .join(
            TelegramRawMessage,
            TelegramRawMessage.id == ParsedPick.raw_message_id,  # type: ignore[arg-type]
        )
        .where(ParsedPick.informante == channel)
        .where(ParsedPick.es_apuesta == True)  # noqa: E712
    )
    if message_date is not None:
        query = query.where(
            TelegramRawMessage.received_at >= message_date - _DUPLICATE_WINDOW,
            TelegramRawMessage.received_at <= message_date + _DUPLICATE_WINDOW,
        )
    else:
        cutoff = utc_now() - _DUPLICATE_WINDOW
        query = query.where(ParsedPick.created_at >= cutoff)
    result = await session.exec(query)
    for candidate in result.all():
        if not candidate.seleccion:
            continue

        # La línea distingue apuestas con texto casi idéntico: "Más de
        # 7.0 córners" y "Más de 8.0 córners" son picks distintos y no
        # deben fusionarse.
        if (
            pick.linea is not None
            and candidate.linea is not None
            and abs(candidate.linea - pick.linea) > 0.001
        ):
            continue

        similarity = _text_similarity(candidate.seleccion, pick.seleccion)
        if similarity >= _DUPLICATE_TEXT_SIMILARITY:
            return candidate

        if _word_set_similarity(candidate.seleccion, pick.seleccion) >= (
            _DUPLICATE_WORD_SET_SIMILARITY
        ):
            return candidate

        # Nombre parcial: "Tom gana" ⊂ "Tom Gentzsch gana".
        if _word_subset(candidate.seleccion, pick.seleccion) or _word_subset(
            pick.seleccion, candidate.seleccion
        ):
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
    session: AsyncSession | None = None,
) -> None:
    """Punto de entrada único para procesar un mensaje entrante de Telegram.

    `message_date` es la fecha/hora real del mensaje de Telegram (no la
    de procesado). Se usa como aproximación de la fecha del evento
    cuando el texto no la menciona explícitamente (la mayoría de
    tipsters publican el pick el mismo día del partido).

    Si `session` se proporciona, se usa en lugar de abrir una nueva
    sesión. Útil en tests.
    """
    message = IncomingTelegramMessage(
        channel=channel, channel_id=channel_id, message_id=message_id, text=text
    )
    logger.info("[TELEGRAM_PROCESSOR] Mensaje listo para procesar: %s", message)

    source_text = (extracted_text or text or "").strip()
    naive_message_date = to_naive_utc(message_date) if message_date else None

    async def _persist(db_session: AsyncSession) -> None:
        raw = TelegramRawMessage(
            channel_id=channel_id,
            message_id=message_id,
            channel_name=channel,
            text=text or "",
            media_path=media_path,
            extracted_text=extracted_text,
            processed=False,
            received_at=naive_message_date or utc_now(),
        )
        db_session.add(raw)
        await db_session.flush()

        settings = get_settings()
        pick = None
        if settings.openai_api_key and source_text:
            try:
                pick = await extract_pick(
                    source_text,
                    settings.openai_api_key,
                    informante=channel,
                    fecha_referencia=naive_message_date,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[TELEGRAM_PROCESSOR] Error extrayendo pick del mensaje %s "
                    "del canal %s; se conserva el mensaje crudo para auditoría.",
                    message_id,
                    channel,
                )
                pick = None

        if pick is not None:
            # Auditoría de fechas: permite cotejar la fecha real del
            # mensaje con la que el extractor asignó al evento.
            logger.info(
                "[TELEGRAM_PROCESSOR] msg=%s canal=%s fecha_referencia=%s "
                "-> fecha_evento_extraida=%s (metodo=%s)",
                message_id,
                channel,
                naive_message_date,
                pick.fecha_evento,
                pick.metodo,
            )
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
            raw.processed = True
            db_session.add(raw)

            duplicate = None
            if pick.es_apuesta:
                duplicate = await _find_duplicate_pick(
                    db_session, channel, pick, naive_message_date
                )

            if duplicate:
                logger.info(
                    "[TELEGRAM_PROCESSOR] Pick duplicado en canal %s (ya existe "
                    "ParsedPick id=%s): '%s' ~ '%s'. No se crea de nuevo.",
                    channel,
                    duplicate.id,
                    pick.seleccion,
                    duplicate.seleccion,
                )
                # El duplicado puede traer datos que el original no
                # tenía (la foto trae la cuota, el texto el stake...).
                if _merge_pick_data(duplicate, pick):
                    db_session.add(duplicate)
                    logger.info(
                        "[TELEGRAM_PROCESSOR] Datos del duplicado fusionados "
                        "en pick id=%s.",
                        duplicate.id,
                    )
            else:
                informante = await get_or_create_informante(
                    db_session, channel, es_canal_telegram=True
                )
                # Los "retos" del tipster ("RETO X3 GRATIS"...) van a su
                # propia sección, fuera de las apuestas diarias. El texto
                # del reto suele venir en la caption o en el cuerpo del
                # mensaje, así que se mira ambos (texto + OCR).
                es_reto = bool(
                    re.search(
                        r"\breto\b",
                        f"{text or ''} {extracted_text or ''}",
                        re.IGNORECASE,
                    )
                )
                parsed = ParsedPick(
                    raw_message_id=raw.id,
                    informante_id=informante.id,
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
                    linea=pick.linea,
                    metodo=pick.metodo,
                    confianza=pick.confianza,
                    es_reto=es_reto,
                )
                db_session.add(parsed)
        elif not source_text:
            # Sin texto (ni extraído ni crudo), no hay nada que procesar.
            raw.processed = True
            db_session.add(raw)

        await db_session.commit()

    if session is not None:
        await _persist(session)
    else:
        async with AsyncSessionLocal() as session:
            await _persist(session)

    logger.info(
        "[TELEGRAM_PROCESSOR] Mensaje %s del canal %s guardado en BD",
        message_id,
        channel,
    )
