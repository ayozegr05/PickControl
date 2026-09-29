"""Procesamiento de mensajes de Telegram recibidos por Telethon.

Guarda el mensaje crudo y, si hay clave de OpenAI, intenta extraer un pick
mediante el extractor híbrido (reglas + LLM).
"""

import asyncio
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
from app.services.notifications.push import notify_new_pick
from app.services.pick_service import get_or_create_informante, to_naive_utc
from app.services.results.base import fold_name
from app.services.telegram.pick_extractor import (
    ExtractedPick,
    _is_settled_ticket,
    _looks_like_bet,
    extract_pick,
)

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


def _fit(value: str | None, limit: int) -> str | None:
    """Trunca un campo a su límite de columna en `parsed_picks`.

    El extractor a veces copia la prosa del análisis entera en
    `seleccion`/`apuesta` (varchar 255/500 en la migración
    cd2dfbe83713): sin el corte el INSERT entero falla y el pick se
    pierde. El raw queda guardado igualmente — es la auditoría real.
    """
    if value is not None and len(value) > limit:
        return value[:limit]
    return value


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


# Ventana para emparejar la foto del boleto con el texto del pick: los
# tipsters publican el slip (rival + cuota en el OCR) y a los pocos
# minutos el texto (stake + título limpio). 10 min cubre los ~5 min
# observados sin juntar picks distintos.
_PAIR_WINDOW = timedelta(minutes=10)

# Marcadores de que el OCR es un boleto/carta de apuestas (y no una
# captura de chat ni una promo de casa). Basta uno: no aparecen en otro
# tipo de imagen.
_SLIP_MARKER = re.compile(
    r"sencillas?|\bsimple\b|\bimp(?:orte)?:|ganar[áa]\s+el\s+encuentro|"
    r"ganador\s+del\s+encuentro|crear\s+apuesta|hoja\s+de\s+apuestas|"
    r"ganancias|cerrar\s+apuesta|añadir\s+selecci[oó]n|boleto|"
    r"m[aá]s/menos|total\s+de\s+(?:goles|juegos)|"
    # Boleto EN VIVO estilo bet365 ("Como 0 0 Parma / Estadísticas de
    # jugador / Total - Córners"): no lleva "Sencillas" ni importe,
    # pero sí la cabecera de stats en directo o la línea de mercado
    # "Mercado - Submercado".
    r"estad[íi]sticas\s+de\s+jugador|l[ií]nea\s+de\s+tiempo|"
    r"(?:total|encuentro|1[ªa°]\s*parte|ambos\s+equipos)\s*-\s*[a-záéíóúñü]",
    re.IGNORECASE,
)


def _looks_like_slip(ocr_text: str | None) -> bool:
    """True si el OCR tiene pinta de boleto de apuestas ABIERTO.

    Un slip liquidado (sello "GANADOR" reposteado como prueba) no es
    pareja válida: su rival/cuota pertenecen al pick de ayer.
    """
    if not ocr_text:
        return False
    return bool(_SLIP_MARKER.search(ocr_text)) and not _is_settled_ticket(ocr_text)


async def _find_pair_slip(
    session: AsyncSession,
    channel_id: int,
    when: datetime | None,
    message_id: int,
) -> TelegramRawMessage | None:
    """La foto-boleto del mismo canal más cercana al texto en el tiempo.

    Cubre los dos órdenes que usan los tipsters: foto → texto (el caso
    habitual, ~2-5 min) y texto → foto (publican el pick y luego el
    boleto que lo respalda). En vivo el mensaje futuro aún no existe,
    así que solo la dirección hacia atrás aplica; en backfill ambas.
    """
    if when is None:
        return None
    query = (
        select(TelegramRawMessage)
        .where(TelegramRawMessage.channel_id == channel_id)
        .where(TelegramRawMessage.media_path.isnot(None))  # type: ignore[union-attr]
        .where(TelegramRawMessage.extracted_text.isnot(None))  # type: ignore[union-attr]
        .where(TelegramRawMessage.message_id != message_id)
        .where(TelegramRawMessage.received_at >= when - _PAIR_WINDOW)
        .where(TelegramRawMessage.received_at <= when + _PAIR_WINDOW)
    )
    candidates = [
        c
        for c in (await session.exec(query)).all()
        if _looks_like_slip(c.extracted_text)
    ]
    if not candidates:
        return None
    # El más cercano en el tiempo, no el más reciente: un slip de otro
    # pick publicado después no debe ganarle al que acompaña al texto.
    return min(candidates, key=lambda c: abs(c.received_at - when))


async def _find_pick_of_raw(session: AsyncSession, raw_id: int) -> ParsedPick | None:
    """El pick simple (no combinada, no reto, no pata) de un raw."""
    query = (
        select(ParsedPick)
        .where(ParsedPick.raw_message_id == raw_id)
        .where(ParsedPick.es_apuesta == True)  # noqa: E712
        .where(ParsedPick.es_combinada == False)  # noqa: E712
        .where(ParsedPick.es_reto == False)  # noqa: E712
        .where(ParsedPick.combinada_id == None)  # noqa: E711
    )
    return (await session.exec(query)).first()


async def _find_pair_text_pick(
    session: AsyncSession,
    channel_id: int,
    when: datetime | None,
    message_id: int,
) -> tuple[ParsedPick, TelegramRawMessage] | None:
    """Pick ya creado por el TEXTO posterior a la foto.

    El catch-up procesa los mensajes de nuevo a viejo: cuando llega la
    foto, el texto de su pareja ya puede estar guardado con un pick
    incompleto (sin rival ni cuota). En ese caso la foto lo enriquece.
    """
    if when is None:
        return None
    query = (
        select(ParsedPick, TelegramRawMessage)
        .join(
            TelegramRawMessage,
            TelegramRawMessage.id == ParsedPick.raw_message_id,  # type: ignore[arg-type]
        )
        .where(TelegramRawMessage.channel_id == channel_id)
        .where(TelegramRawMessage.media_path.is_(None))  # type: ignore[union-attr]
        .where(TelegramRawMessage.message_id > message_id)
        .where(TelegramRawMessage.received_at >= when)
        .where(TelegramRawMessage.received_at <= when + _PAIR_WINDOW)
        .where(ParsedPick.es_apuesta == True)  # noqa: E712
        .where(ParsedPick.es_combinada == False)  # noqa: E712
        .where(ParsedPick.es_reto == False)  # noqa: E712
        .where(ParsedPick.combinada_id == None)  # noqa: E711
        .order_by(TelegramRawMessage.received_at.asc())  # type: ignore[arg-type]
    )
    row = (await session.exec(query)).first()
    return (row[0], row[1]) if row else None


def _enrich_paired_pick(target: ParsedPick, pick: ExtractedPick) -> None:
    """Sobreescribe los campos del pick con la extracción combinada
    (OCR del boleto + texto del tipster): ve el rival en la foto y el
    stake en el texto, así que es estrictamente mejor que cada fuente
    por separado. Conserva el valor previo cuando el nuevo es None."""
    target.apuesta = _fit(pick.seleccion, 500) or target.apuesta
    target.seleccion = _fit(pick.seleccion, 255) or target.seleccion
    target.deporte = _fit(pick.deporte, 100) or target.deporte
    target.evento = _fit(pick.evento, 255) or target.evento
    target.mercado = _fit(pick.mercado, 100) or target.mercado
    target.casa = _fit(pick.casa, 100) or target.casa
    target.explicacion = pick.explicacion or target.explicacion
    if pick.cuota is not None:
        target.cuota = pick.cuota
    if pick.stake is not None:
        target.stake = pick.stake
    if pick.linea is not None:
        target.linea = pick.linea
    target.fecha_evento = pick.fecha_evento or target.fecha_evento
    target.metodo = f"{pick.metodo or 'llm'}+par"
    if pick.confianza is not None:
        target.confianza = pick.confianza


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
        # Las patas de una combinada nunca son candidatas a dedup: un
        # pick simple futuro con el mismo texto no debe fusionarse con
        # una selección que forma parte de un boleto.
        .where(ParsedPick.combinada_id == None)  # noqa: E711
        # Y un simple solo dedup contra simples: sin este filtro, la
        # selección "Real Madrid gana" se fusionaba con el padre
        # "Real Madrid gana + Barça gana" por subset de palabras —
        # son apuestas distintas (una es un boleto de N patas).
        .where(
            ParsedPick.es_combinada == bool(pick.es_apuesta and len(pick.patas) >= 2)
        )
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


def _leg_signature(leg) -> tuple:
    """Identidad normalizada de una pata: selección + mercado + línea.

    Las palabras van plegadas (sin acentos, minúsculas) y ordenadas para
    que reordenaciones del tipster den la misma firma; la cuota por pata
    queda fuera (el OCR no siempre la extrae igual).
    """
    sel = " ".join(sorted(re.findall(r"\w+", fold_name(leg.seleccion or ""))))
    mercado = " ".join(sorted(re.findall(r"\w+", fold_name(leg.mercado or ""))))
    linea = round(leg.linea, 2) if leg.linea is not None else None
    return (sel, mercado, linea)


def combinada_signature(patas) -> tuple:
    """Firma canónica de un boleto: las patas normalizadas, sin orden."""
    return tuple(sorted(_leg_signature(p) for p in patas))


async def _find_duplicate_combinada(
    session: AsyncSession, channel: str, pick: ExtractedPick
) -> ParsedPick | None:
    """Boleto idéntico ya guardado en el mismo canal, sin ventana.

    La dedup normal solo ve ±6 h alrededor del mensaje: un tipster que
    republica el mismo slip días después creaba otra combinada. Aquí se
    casa por firma (mismas patas) y, si ambos traen cuota total, cuota
    coincidente — dos boletos con las mismas patas y cuota distinta son
    apuestas distintas.
    """
    target = combinada_signature(pick.patas)
    if len(target) < 2:
        return None
    parents = (
        await session.exec(
            select(ParsedPick)
            .where(ParsedPick.informante == channel)
            .where(ParsedPick.es_apuesta == True)  # noqa: E712
            .where(ParsedPick.es_combinada == True)  # noqa: E712
            .where(ParsedPick.combinada_id == None)  # noqa: E711
        )
    ).all()
    for parent in parents:
        legs = (
            await session.exec(
                select(ParsedPick)
                .where(ParsedPick.combinada_id == parent.id)
                .where(ParsedPick.es_apuesta == True)  # noqa: E712
            )
        ).all()
        if combinada_signature(legs) != target:
            continue
        if (
            pick.cuota is not None
            and parent.cuota is not None
            and abs(parent.cuota - pick.cuota) >= 0.01
        ):
            continue
        return parent
    return None


async def _persist_patas(
    db_session: AsyncSession,
    parent: ParsedPick,
    pick: ExtractedPick,
    informante_id: int,
    channel: str,
    es_reto: bool,
    *,
    es_duplicada: bool = False,
) -> None:
    """Una fila por pata (self-FK): cada una se verifica por separado
    con el verificador normal y el padre se liquida en conjunto
    (ver verifier.settle_combinada). Las patas de una combinada
    duplicada nacen ya anuladas — quedan de auditoría pero nunca
    entran a la cola de verificación ni a las stats."""
    for orden, pata in enumerate(pick.patas):
        leg = ParsedPick(
            raw_message_id=parent.raw_message_id,
            informante_id=informante_id,
            combinada_id=parent.id,
            orden=orden,
            es_apuesta=True,
            apuesta=_fit(pata.seleccion, 500),
            deporte=_fit(pata.deporte, 100),
            evento=_fit(pata.evento, 255),
            mercado=_fit(pata.mercado, 100),
            seleccion=_fit(pata.seleccion, 255),
            cuota=pata.cuota,
            casa=_fit(pick.casa, 100),
            informante=_fit(channel, 255),
            # El LLM suele dejar la fecha de la pata vacía: se hereda
            # la del padre (fecha del boleto) para que el verifier la
            # pueda intentar — con NULL queda invisible en su query.
            fecha_evento=pata.fecha_evento or parent.fecha_evento,
            linea=pata.linea,
            metodo=pick.metodo,
            confianza=pick.confianza,
            es_reto=es_reto,
            anulada=es_duplicada,
            motivo_anulada="duplicado" if es_duplicada else None,
            verificado_por="auto" if es_duplicada else None,
            verificado_at=utc_now() if es_duplicada else None,
        )
        db_session.add(leg)


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

    # Una foto con caption ya es un par foto+texto en un solo mensaje:
    # el OCR trae rival/cuota y el caption puede traer el stake.
    source_text = "\n\n".join(filter(None, [extracted_text, text])).strip()
    naive_message_date = to_naive_utc(message_date) if message_date else None
    # Picks nuevos creados en este mensaje — se notifican por push tras
    # el commit (la notificación abre su propia sesión: antes del commit
    # la fila todavía no existiría para ella). Solo el pick "principal":
    # las patas de una combinada no notifican (las crea _persist_patas),
    # y los duplicados/enriquecidos tampoco (no son picks nuevos).
    created_pick_ids: list[int] = []

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
        # Emparejado foto-boleto + texto del pick (ver _PAIR_WINDOW):
        # `pair_pick` es el pick que la foto previa ya creó (caso en
        # vivo: foto primero); `forward_pick` es el pick que el texto
        # posterior ya creó (caso catch-up, que procesa nuevo→viejo).
        pair_pick = None
        forward_pick = None
        extraction_text = source_text
        if settings.openai_api_key and source_text:
            if not media_path and text and _looks_like_bet(text):
                slip_raw = await _find_pair_slip(
                    db_session, channel_id, naive_message_date, message_id
                )
                if slip_raw is not None:
                    pair_pick = await _find_pick_of_raw(db_session, slip_raw.id)
                    extraction_text = f"{text}\n\n{slip_raw.extracted_text}"
                    logger.info(
                        "[TELEGRAM_PROCESSOR] msg %s emparejado con boleto %s "
                        "del canal %s.",
                        message_id,
                        slip_raw.message_id,
                        channel,
                    )
            elif media_path and extracted_text and _looks_like_slip(extracted_text):
                forward = await _find_pair_text_pick(
                    db_session, channel_id, naive_message_date, message_id
                )
                if forward is not None:
                    forward_pick, forward_raw = forward
                    # El texto del pick primero (título limpio) y el OCR
                    # del boleto después (rival/cuota como contexto).
                    extraction_text = f"{forward_raw.text}\n\n{source_text}"
                    logger.info(
                        "[TELEGRAM_PROCESSOR] boleto %s emparejado con "
                        "texto-pick del msg %s del canal %s.",
                        message_id,
                        forward_raw.message_id,
                        channel,
                    )
            try:
                pick = await extract_pick(
                    extraction_text,
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

            # Si la extracción combinada no vio apuesta, la pareja era
            # falsa: reintenta con el mensaje solo.
            if extraction_text != source_text and (pick is None or not pick.es_apuesta):
                pair_pick = None
                forward_pick = None
                try:
                    pick = await extract_pick(
                        source_text,
                        settings.openai_api_key,
                        informante=channel,
                        fecha_referencia=naive_message_date,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[TELEGRAM_PROCESSOR] Error extrayendo pick del "
                        "mensaje %s del canal %s.",
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

            if pick.es_apuesta and (pair_pick is not None or forward_pick is not None):
                # La pareja foto+texto ya tiene su pick: la extracción
                # combinada lo enriquece (rival/cuota del boleto, stake
                # del texto) en lugar de crear un duplicado.
                target = pair_pick or forward_pick
                assert target is not None
                _enrich_paired_pick(target, pick)
                if len(pick.patas) >= 2 and not target.es_combinada:
                    target.es_combinada = True
                    await db_session.flush()
                    await _persist_patas(
                        db_session,
                        target,
                        pick,
                        target.informante_id,
                        channel,
                        target.es_reto,
                    )
                db_session.add(target)
                logger.info(
                    "[TELEGRAM_PROCESSOR] Pick id=%s enriquecido con la "
                    "pareja foto+texto (msg %s del canal %s).",
                    target.id,
                    message_id,
                    channel,
                )
            else:
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
                    es_combinada = pick.es_apuesta and len(pick.patas) >= 2
                    # Slip republicado días después (la dedup normal solo
                    # ve ±6 h): misma firma de patas + cuota en el canal
                    # -> la copia nace ya anulada·duplicado, queda en la
                    # lista para auditoría pero fuera de stats y de la
                    # cola de verificación. La original no se toca.
                    combinada_dup = (
                        await _find_duplicate_combinada(db_session, channel, pick)
                        if es_combinada
                        else None
                    )
                    if combinada_dup is not None:
                        logger.info(
                            "[TELEGRAM_PROCESSOR] Combinada duplicada en "
                            "canal %s (canónica ParsedPick id=%s): se crea "
                            "como anulada·duplicado.",
                            channel,
                            combinada_dup.id,
                        )
                        # El repost puede aportar cuota/stake al canónico.
                        if _merge_pick_data(combinada_dup, pick):
                            db_session.add(combinada_dup)
                    parsed = ParsedPick(
                        raw_message_id=raw.id,
                        informante_id=informante.id,
                        es_apuesta=pick.es_apuesta,
                        apuesta=_fit(pick.seleccion, 500),
                        deporte=_fit(pick.deporte, 100),
                        evento=_fit(pick.evento, 255),
                        mercado=_fit(pick.mercado, 100),
                        seleccion=_fit(pick.seleccion, 255),
                        cuota=pick.cuota,
                        stake=pick.stake,
                        casa=_fit(pick.casa, 100),
                        informante=_fit(channel, 255),
                        explicacion=pick.explicacion,
                        fecha_evento=pick.fecha_evento,
                        linea=pick.linea,
                        metodo=pick.metodo,
                        confianza=pick.confianza,
                        es_reto=es_reto,
                        es_combinada=es_combinada,
                        anulada=combinada_dup is not None,
                        motivo_anulada=(
                            "duplicado" if combinada_dup is not None else None
                        ),
                        verificado_por=("auto" if combinada_dup is not None else None),
                        verificado_at=(
                            utc_now() if combinada_dup is not None else None
                        ),
                    )
                    db_session.add(parsed)
                    if pick.es_apuesta:
                        # flush para tener parsed.id antes del commit.
                        await db_session.flush()
                        if combinada_dup is None:
                            # Las copias no notifican: no son picks nuevos.
                            created_pick_ids.append(parsed.id)

                    if es_combinada:
                        # Una fila por pata (self-FK): cada una se verifica
                        # por separado con el verificador normal y el padre
                        # se liquida en conjunto (ver verifier.settle_combinada).
                        await db_session.flush()
                        await _persist_patas(
                            db_session,
                            parsed,
                            pick,
                            informante.id,
                            channel,
                            es_reto,
                            es_duplicada=combinada_dup is not None,
                        )
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

    # Push "nuevo pick" — fire-and-forget: una notificación nunca debe
    # bloquear ni tumbar la ingesta. Tras el commit la fila ya existe.
    for pick_id in created_pick_ids:
        try:
            asyncio.create_task(notify_new_pick(pick_id))
        except RuntimeError:
            logger.debug(
                "[TELEGRAM_PROCESSOR] Push omitida para pick %s (sin loop).",
                pick_id,
            )

    logger.info(
        "[TELEGRAM_PROCESSOR] Mensaje %s del canal %s guardado en BD",
        message_id,
        channel,
    )
