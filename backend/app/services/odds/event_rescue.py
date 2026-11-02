"""Rescate del campo `evento` cuando no parece un partido.

El extractor a veces vuelca en `evento` la apuesta ("9,5 córners en
total") o basura OCR en lugar del cruce "A vs B". Sin un partido
reconocible el backfill de cuotas solo puede quemar búsquedas
imposibles, así que antes de rendirse se intenta recuperar el evento
del material ya guardado, de menos a más caro:

1. Reglas sobre `raw.text` y `raw.extracted_text` (el OCR ya hecho) —
   `_extract_eventos` + `_fixture_from_slip_ocr` del extractor.
2. Mini-LLM sobre ese mismo texto (gpt-4o-mini): solo se pide el
   campo evento, no se reprocesa el pick.
3. Re-OCR dirigido sobre la IMAGEN del raw — o la del mensaje
   emparejado del par foto-boleto (±`_PAIR_WINDOW`): el primer OCR
   pudo leer mal el boleto, y una pregunta concreta sobre la imagen
   ("¿qué partido muestra este slip?") es más fiable.
4. Nada -> el dato no existe en lo que publicó el tipster: el pick
   queda como está (es una apuesta real) y quien llama marca el
   intento de la fuente como agotado.

El rescate actualiza `pick.evento` IN SITU — misma fila, sin crear
picks nuevos ni tocar `es_apuesta`.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.logging import get_logger
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram.ocr import _encode_image
from app.services.telegram.openai_retry import call_with_retry
from app.services.telegram.pick_extractor import (
    _extract_eventos,
    _fixture_from_slip_ocr,
)
from app.services.telegram.processor import _PAIR_WINDOW

logger = get_logger("app.odds.event_rescue")


def looks_like_match_hint(hint: str) -> bool:
    """¿La pista tiene forma de cruce "A vs B"? Una apuesta o basura
    OCR ("9,5 córners en total") no la supera — la búsqueda sería
    imposible."""
    return bool(_extract_eventos([hint]))


def _candidate_from_text(text: Optional[str]) -> Optional[str]:
    """Primer cruce "A vs B" del texto por reglas (sin LLM)."""
    if not text:
        return None
    eventos = _extract_eventos(text.splitlines())
    if eventos:
        return eventos[0]
    return _fixture_from_slip_ocr(text)


async def _evento_via_llm(text: str, api_key: str) -> Optional[str]:
    """Mini-extracción del SOLO campo evento sobre el texto guardado."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    response = await call_with_retry(
        lambda: client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Texto de un mensaje de un canal de apuestas "
                        "deportivas:\n\n"
                        f"{text[:4000]}\n\n"
                        "¿A qué partido o enfrentamiento se refiere la "
                        "apuesta? Responde SOLO con JSON válido:\n"
                        '{"evento": "EquipoA vs EquipoB"} — o '
                        '{"evento": null} si el texto no menciona '
                        "ningún partido."
                    ),
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=150,
        ),
        "rescate evento (texto)",
    )
    content = response.choices[0].message.content
    if not content:
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    value = str(data.get("evento") or "").strip()
    # El modelo propone, lo determinista valida: solo se acepta si
    # tiene forma de cruce — un "Italia - Serie A" se descarta igual
    # que en el extractor.
    if not value or not _extract_eventos([value]):
        return None
    return value


async def _evento_via_imagen(path: str, api_key: str) -> Optional[str]:
    """Re-OCR dirigido: pregunta a la imagen solo por el partido.

    El OCR general vuelca todo el boleto (cuotas, promos, chrome) y a
    veces pierde el cruce; una pregunta concreta sobre la imagen es
    más fiable para ese único campo."""
    if not path or not os.path.exists(path):
        return None
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    b64_image = _encode_image(path)
    response = await call_with_retry(
        lambda: client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Esta imagen es un boleto/mensaje de "
                                "apuestas deportivas. ¿Qué partido o "
                                "enfrentamiento muestra? Responde SOLO "
                                "con el cruce 'EquipoA vs EquipoB' con "
                                "los nombres tal cual aparecen en la "
                                "imagen, o 'null' si no se ve ningún "
                                "partido."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                        },
                    ],
                }
            ],
            max_tokens=60,
        ),
        f"rescate evento (imagen {path})",
    )
    content = (response.choices[0].message.content or "").strip()
    if not content or content.lower() == "null":
        return None
    found = _extract_eventos([content])
    return found[0] if found else None


async def _paired_raw(
    session: AsyncSession, raw: TelegramRawMessage
) -> Optional[TelegramRawMessage]:
    """El mensaje emparejado del par foto-boleto: otro raw del mismo
    canal dentro de ±`_PAIR_WINDOW` (preferimos el que trae imagen, que
    es donde suele vivir el slip)."""
    if raw.received_at is None:
        return None
    rows = (
        await session.exec(
            select(TelegramRawMessage)
            .where(TelegramRawMessage.channel_id == raw.channel_id)
            .where(TelegramRawMessage.id != raw.id)
            .where(TelegramRawMessage.received_at >= raw.received_at - _PAIR_WINDOW)
            .where(TelegramRawMessage.received_at <= raw.received_at + _PAIR_WINDOW)
        )
    ).all()
    if not rows:
        return None
    with_media = [r for r in rows if r.media_path]
    pool = with_media or rows
    return min(pool, key=lambda r: abs(r.received_at - raw.received_at))


async def rescue_evento(
    session: AsyncSession, pick: ParsedPick, settings
) -> Optional[str]:
    """Intenta recuperar `pick.evento` del material guardado.

    Devuelve el cruce encontrado o None. Quien llama decide si
    actualiza el pick (modo apply) — la función no escribe en BD.
    """
    if pick.raw_message_id is None:
        return None
    raw = await session.get(TelegramRawMessage, pick.raw_message_id)
    if raw is None:
        return None
    pair = await _paired_raw(session, raw)

    # 1) Reglas sobre el texto ya guardado (propio + del par).
    for text in (
        raw.text,
        raw.extracted_text,
        pair.text if pair else None,
        pair.extracted_text if pair else None,
    ):
        found = _candidate_from_text(text)
        if found:
            logger.info("[RESCATE] pick %s: evento por reglas -> %s", pick.id, found)
            return found

    api_key = getattr(settings, "openai_api_key", None)
    if not api_key:
        return None

    # 2) Mini-LLM sobre el mismo texto (solo el campo evento).
    joined = "\n".join(t for t in (raw.text, raw.extracted_text) if t)
    if joined.strip():
        try:
            found = await _evento_via_llm(joined, api_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RESCATE] pick %s: LLM texto falló: %s", pick.id, exc)
            found = None
        if found:
            logger.info("[RESCATE] pick %s: evento vía LLM -> %s", pick.id, found)
            return found

    # 3) Re-OCR dirigido: la imagen del raw y, si no, la del par
    #    foto-boleto (el slip suele vivir en el mensaje vecino).
    for path in (
        raw.media_path,
        pair.media_path if pair else None,
    ):
        if not path:
            continue
        try:
            found = await _evento_via_imagen(path, api_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RESCATE] pick %s: OCR dirigido falló: %s", pick.id, exc)
            found = None
        if found:
            logger.info(
                "[RESCATE] pick %s: evento vía imagen %s -> %s", pick.id, path, found
            )
            return found

    return None
