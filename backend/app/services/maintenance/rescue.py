"""Rescate de mensajes que quedaron a medias por errores transitorios.

Dos piezas encadenadas (una alimenta a la otra):

- `retry_pending_ocr`: reintenta el OCR de fotos sin `extracted_text`
  (los 429 de OpenAI dejaban el raw vacío). Las fotos cuyo fichero ya
  no existe en disco se saltan sin marcar — si reaparecen en una
  pasada futura, se procesan entonces.
- `reprocess_pending_raws`: repasa raws `processed=False` (incluidos
  los que el OCR acaba de rescatar) e intenta extraer pick de nuevo,
  respetando la dedup por canal y rellenando `fecha_evento` desde
  `received_at` cuando el texto no la traía.

El loop de `lifecycle.py` las corre cada `rescue_interval_hours`;
los scripts `retry_ocr.py`/`reprocess_raw.py` son wrappers del mismo
código para uso manual.
"""

from __future__ import annotations

import os
from typing import Optional

from openai import RateLimitError
from sqlalchemy import select

from app.core.config import get_settings
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


async def retry_pending_ocr(limit: Optional[int] = None) -> dict[str, int]:
    """OCR de fotos sin texto extraído. Devuelve {hechos, fallos, pendientes}."""
    settings = get_settings()
    if not settings.openai_api_key:
        return {"pendientes": 0, "hechos": 0, "fallos": 0}

    async with AsyncSessionLocal() as session:
        raws = (
            (
                await session.exec(
                    select(TelegramRawMessage)
                    .where(TelegramRawMessage.media_path != None)  # noqa: E711
                    .order_by(TelegramRawMessage.received_at.desc())  # type: ignore[arg-type]
                )
            )
            .scalars()
            .all()
        )
        pendientes = [
            r
            for r in raws
            if not (r.extracted_text or "").strip()
            and r.media_path
            and os.path.exists(_media_path(r.media_path))
        ]
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

    return {"pendientes": len(pendientes), "hechos": hechos, "fallos": fallos}


async def reprocess_pending_raws() -> dict[str, int]:
    """Re-extrae picks de raws `processed=False` (dedup por canal incluida)."""
    settings = get_settings()
    if not settings.openai_api_key:
        return {"pendientes": 0, "procesados": 0, "picks": 0, "duplicados": 0}

    procesados = picks_creados = duplicados = 0
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(TelegramRawMessage)
            .where(TelegramRawMessage.processed == False)  # noqa: E712
            .order_by(TelegramRawMessage.received_at.desc())
        )
        raw_messages = list(result.scalars().all())

        for raw in raw_messages:
            existing = await session.exec(
                select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
            )
            if existing.first():
                continue

            source_text = (raw.extracted_text or raw.text or "").strip()
            if not source_text:
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
    }


async def run_rescue_cycle() -> dict[str, dict[str, int]]:
    """Una pasada completa de rescate: primero OCR, luego reproceso
    (así los raws recién rescatados se extraen en el mismo ciclo)."""
    ocr = await retry_pending_ocr()
    reproc = await reprocess_pending_raws()
    if ocr["hechos"] or reproc["picks"]:
        logger.info(
            "[RESCUE] Ciclo: OCR %s ok/%s fallos de %s; reproceso %s/%s (+%s picks, %s dups)",
            ocr["hechos"],
            ocr["fallos"],
            ocr["pendientes"],
            reproc["procesados"],
            reproc["pendientes"],
            reproc["picks"],
            reproc["duplicados"],
        )
    return {"ocr": ocr, "reprocess": reproc}
