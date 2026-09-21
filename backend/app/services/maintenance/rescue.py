"""Rescate de mensajes que quedaron a medias por errores transitorios.

Dos piezas encadenadas (una alimenta a la otra):

- `retry_pending_ocr`: reintenta el OCR de fotos sin `extracted_text`
  (los 429 de OpenAI dejaban el raw vacío). Las fotos cuyo fichero ya
  no existe en disco se saltan sin marcar — si reaparecen dentro de la
  ventana de rescate, se procesan entonces.
- `reprocess_pending_raws`: repasa raws `processed=False` (incluidos
  los que el OCR acaba de rescatar) e intenta extraer pick de nuevo,
  respetando la dedup por canal y rellenando `fecha_evento` desde
  `received_at` cuando el texto no la traía.

Ambas piezas están acotadas por `rescue_max_age_days`: un raw más viejo
que la ventana se da por imposible (`processed=True` en el reproceso;
excluido de la query en el OCR) para que la cola de pendientes no
crezca indefinidamente. Los raws nunca se borran — la fila queda en la
BD como evidencia de auditoría. Los scripts aceptan `--max-age-days 0`
para reintentar sin límite de antigüedad si hiciera falta.

El loop de `lifecycle.py` las corre cada `rescue_interval_hours`;
los scripts `retry_ocr.py`/`reprocess_raw.py` son wrappers del mismo
código para uso manual.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Optional

from openai import RateLimitError
from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.pick_service import get_or_create_informante
from app.services.telegram.ocr import extract_text_from_image
from app.services.telegram.pick_extractor import extract_pick
from app.services.telegram.processor import _find_duplicate_pick

logger = get_logger("app.maintenance.rescue")


def _media_path(media_path: str) -> str:
    """`media_path` se guarda relativo con '/' y '\\' mezclados."""
    return os.path.normpath(os.path.join(os.getcwd(), media_path))


def _rescue_cutoff(settings, max_age_days: Optional[float]):
    """Límite de antigüedad del rescate. `max_age_days <= 0` = sin límite."""
    days = settings.rescue_max_age_days if max_age_days is None else max_age_days
    return None if days <= 0 else utc_now() - timedelta(days=days)


async def _ocr_candidates(session, cutoff):
    """Raws con media sin OCR dentro de la ventana, separados en dos
    grupos: con fichero presente en disco y sin fichero."""
    stmt = (
        select(TelegramRawMessage)
        .where(TelegramRawMessage.media_path != None)  # noqa: E711
        .where(
            or_(
                TelegramRawMessage.extracted_text.is_(None),
                func.trim(TelegramRawMessage.extracted_text) == "",
            )
        )
        .order_by(TelegramRawMessage.received_at.desc())  # type: ignore[arg-type]
    )
    if cutoff is not None:
        stmt = stmt.where(TelegramRawMessage.received_at >= cutoff)
    raws = list((await session.exec(stmt)).scalars().all())
    present = [
        r for r in raws if r.media_path and os.path.exists(_media_path(r.media_path))
    ]
    return raws, present


async def retry_pending_ocr(
    limit: Optional[int] = None, max_age_days: Optional[float] = None
) -> dict[str, int]:
    """OCR de fotos sin texto extraído.

    Devuelve {pendientes, sin_fichero, hechos, fallos}. Solo se intentan
    raws dentro de `rescue_max_age_days` cuyo fichero exista en disco.
    """
    settings = get_settings()
    empty = {"pendientes": 0, "sin_fichero": 0, "hechos": 0, "fallos": 0}
    if not settings.openai_api_key:
        return empty

    cutoff = _rescue_cutoff(settings, max_age_days)
    async with AsyncSessionLocal() as session:
        raws, pendientes = await _ocr_candidates(session, cutoff)
        if limit:
            pendientes = pendientes[:limit]

        hechos = fallos = 0
        for raw in pendientes:
            try:
                texto = await extract_text_from_image(
                    _media_path(raw.media_path), settings.openai_api_key
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[RESCUE] OCR msg %s falló: %s", raw.message_id, exc)
                fallos += 1
                continue
            if not (texto or "").strip():
                fallos += 1
                continue

            raw.extracted_text = texto
            # Si el raw nunca llegó a generar pick, queda pendiente para
            # que el reproceso lo extraiga con el OCR nuevo.
            tiene_pick = (
                await session.exec(
                    select(ParsedPick.id).where(ParsedPick.raw_message_id == raw.id)
                )
            ).first()
            if not tiene_pick:
                raw.processed = False
            session.add(raw)
            await session.commit()
            hechos += 1

    return {
        "pendientes": len(pendientes),
        "sin_fichero": len(raws) - len(pendientes),
        "hechos": hechos,
        "fallos": fallos,
    }


async def reprocess_pending_raws(
    max_age_days: Optional[float] = None,
) -> dict[str, int]:
    """Re-extrae picks de raws `processed=False` (dedup por canal incluida).

    Cierra como `processed=True` los que el extractor resuelve como "no
    es pick" (si no, se reenviarían al LLM en cada ciclo para siempre) y
    los que superan `rescue_max_age_days` — la fila queda en BD pero ya
    no se reintenta ni catchup la vuelve a descargar.
    """
    settings = get_settings()
    empty = {
        "pendientes": 0,
        "procesados": 0,
        "picks": 0,
        "duplicados": 0,
        "sin_pick": 0,
        "antiguos": 0,
    }
    if not settings.openai_api_key:
        return empty

    cutoff = _rescue_cutoff(settings, max_age_days)
    procesados = picks_creados = duplicados = sin_pick = 0
    async with AsyncSessionLocal() as session:
        antiguos = 0
        if cutoff is not None:
            viejos = (
                (
                    await session.exec(
                        select(TelegramRawMessage)
                        .where(TelegramRawMessage.processed == False)  # noqa: E712
                        .where(TelegramRawMessage.received_at < cutoff)
                    )
                )
                .scalars()
                .all()
            )
            for raw in viejos:
                raw.processed = True
                session.add(raw)
            antiguos = len(viejos)

        stmt = (
            select(TelegramRawMessage)
            .where(TelegramRawMessage.processed == False)  # noqa: E712
            .order_by(TelegramRawMessage.received_at.desc())
        )
        if cutoff is not None:
            stmt = stmt.where(TelegramRawMessage.received_at >= cutoff)
        raw_messages = list((await session.exec(stmt)).scalars().all())

        for raw in raw_messages:
            existing = await session.exec(
                select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
            )
            if existing.first():
                continue

            source_text = (raw.extracted_text or raw.text or "").strip()
            if not source_text:
                if not raw.media_path:
                    # Ni texto ni media referenciada: el raw está vacío
                    # de nacimiento y nunca producirá nada — se cierra ya
                    # en vez de esperar a que venza la ventana.
                    raw.processed = True
                    session.add(raw)
                    sin_pick += 1
                continue

            try:
                pick = await extract_pick(
                    source_text,
                    settings.openai_api_key,
                    informante=raw.channel_name,
                    fecha_referencia=raw.received_at,
                )
            except RateLimitError:
                # Sin cuota de OpenAI: se deja processed=False para la
                # siguiente pasada.
                continue
            if not pick:
                # El extractor resolvió "no es pick": se cierra para no
                # reenviarlo al LLM en cada ciclo indefinidamente.
                raw.processed = True
                session.add(raw)
                sin_pick += 1
                continue

            if pick.fecha_evento is None and raw.received_at is not None:
                # Sin fecha en el texto: asumimos que el pick se publicó
                # el mismo día del partido.
                pick.fecha_evento = raw.received_at

            duplicate = None
            if pick.es_apuesta:
                duplicate = await _find_duplicate_pick(
                    session, raw.channel_name, pick, raw.received_at
                )

            if duplicate:
                duplicados += 1
                if duplicate.fecha_evento is None and pick.fecha_evento is not None:
                    duplicate.fecha_evento = pick.fecha_evento
                    session.add(duplicate)
                    await session.flush()
            else:
                informante = await get_or_create_informante(session, raw.channel_name)
                session.add(
                    ParsedPick(
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
                        informante=raw.channel_name,
                        explicacion=pick.explicacion,
                        fecha_evento=pick.fecha_evento,
                        linea=pick.linea,
                        metodo=pick.metodo,
                        confianza=pick.confianza,
                    )
                )
                await session.flush()
                picks_creados += 1

            raw.processed = True
            session.add(raw)
            procesados += 1

        await session.commit()

    return {
        "pendientes": len(raw_messages),
        "procesados": procesados,
        "picks": picks_creados,
        "duplicados": duplicados,
        "sin_pick": sin_pick,
        "antiguos": antiguos,
    }


async def run_rescue_cycle() -> dict[str, dict[str, int]]:
    """Una pasada completa de rescate: primero OCR, luego reproceso
    (así los raws recién rescatados se extraen en el mismo ciclo)."""
    ocr = await retry_pending_ocr()
    reproc = await reprocess_pending_raws()
    if ocr["hechos"] or reproc["picks"] or reproc["antiguos"]:
        logger.info(
            "[RESCUE] Ciclo: OCR %s ok/%s fallos de %s (%s sin fichero); "
            "reproceso %s/%s (+%s picks, %s dups, %s sin pick, %s antiguos)",
            ocr["hechos"],
            ocr["fallos"],
            ocr["pendientes"],
            ocr["sin_fichero"],
            reproc["procesados"],
            reproc["pendientes"],
            reproc["picks"],
            reproc["duplicados"],
            reproc["sin_pick"],
            reproc["antiguos"],
        )
    return {"ocr": ocr, "reprocess": reproc}
