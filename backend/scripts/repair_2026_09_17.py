# ruff: noqa: E402
"""Reparación one-off de los problemas detectados el 17/09/2026.

1. Reprocesa raw=1328 (pick "Menos de 11.0 corners" del 16/09 en Dm7
   Allsports): la extracción falló por un error transitorio de OpenAI y
   el raw quedó processed=False sin pick.
2. Marca es_apuesta=False los picks fantasma generados por marketing del
   tipster: 271 (anuncio "El Suvidón"), 820 (celebración de acierto) y
   157 (slip cobrado reposteado, Genoa-Como).
3. Corrige fecha_evento = received_at del mensaje en los picks con fecha
   imposible (año/mes mal extraídos): 138, 142, 207, 667, 716.

Uso:
    .venv\\Scripts\\python.exe scripts\\repair_2026_09_17.py
"""

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.pick_service import get_or_create_informante
from app.services.telegram.pick_extractor import extract_pick
from app.services.telegram.processor import _find_duplicate_pick

REPROCESS_RAW_ID = 1328
PHANTOM_PICK_IDS = [271, 820, 157]
BAD_DATE_PICK_IDS = [138, 142, 207, 667, 716]


async def reprocess_raw(session, raw: TelegramRawMessage) -> None:
    settings = get_settings()
    source_text = (raw.extracted_text or raw.text or "").strip()
    if not source_text:
        print(f"  raw={raw.id}: sin texto, no se puede reprocesar.")
        return

    existing = await session.exec(
        select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
    )
    if existing.first():
        print(f"  raw={raw.id}: ya tiene pick, salto.")
        return

    pick = await extract_pick(
        source_text,
        settings.openai_api_key,
        informante=raw.channel_name,
        fecha_referencia=raw.received_at,
    )
    if pick is None:
        print(f"  raw={raw.id}: el extractor devolvió None.")
        return

    if pick.fecha_evento is None and raw.received_at is not None:
        pick.fecha_evento = raw.received_at

    duplicate = None
    if pick.es_apuesta:
        duplicate = await _find_duplicate_pick(
            session, raw.channel_name, pick, raw.received_at
        )
    if duplicate:
        print(
            f"  raw={raw.id}: duplicado de pick={duplicate.id} "
            f"({duplicate.seleccion!r}), no se crea."
        )
    else:
        informante = await get_or_create_informante(
            session, raw.channel_name, es_canal_telegram=True
        )
        es_reto = bool(
            re.search(
                r"\breto\b",
                f"{raw.text or ''} {raw.extracted_text or ''}",
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
            informante=raw.channel_name,
            explicacion=pick.explicacion,
            fecha_evento=pick.fecha_evento,
            linea=pick.linea,
            metodo=pick.metodo,
            confianza=pick.confianza,
            es_reto=es_reto,
        )
        session.add(parsed)
        await session.flush()
        print(
            f"  raw={raw.id}: creado pick={parsed.id} "
            f"es_apuesta={pick.es_apuesta} sel={pick.seleccion!r} "
            f"fecha_evento={pick.fecha_evento}"
        )

    raw.processed = True
    session.add(raw)


async def main() -> None:
    async with AsyncSessionLocal() as session:
        # 1) Reprocesar el raw perdido del día 16.
        print("== Reprocesando raws perdidos ==")
        raw = (
            (
                await session.exec(
                    select(TelegramRawMessage).where(
                        TelegramRawMessage.id == REPROCESS_RAW_ID
                    )
                )
            )
            .scalars()
            .first()
        )
        if raw is None:
            print(f"  raw={REPROCESS_RAW_ID} no existe.")
        else:
            await reprocess_raw(session, raw)

        # 2) Picks fantasma (marketing, no apuestas abiertas).
        print("== Marcando picks fantasma como es_apuesta=False ==")
        for pick_id in PHANTOM_PICK_IDS:
            pick = (
                (await session.exec(select(ParsedPick).where(ParsedPick.id == pick_id)))
                .scalars()
                .first()
            )
            if pick is None:
                print(f"  pick={pick_id} no existe.")
                continue
            pick.es_apuesta = False
            session.add(pick)
            print(f"  pick={pick_id} ({pick.seleccion!r}) -> es_apuesta=False")

        # 3) fecha_evento imposible -> fecha real del mensaje.
        print("== Corrigiendo fecha_evento ==")
        for pick_id in BAD_DATE_PICK_IDS:
            row = (
                await session.exec(
                    select(ParsedPick, TelegramRawMessage.received_at)
                    .join(
                        TelegramRawMessage,
                        TelegramRawMessage.id == ParsedPick.raw_message_id,
                    )
                    .where(ParsedPick.id == pick_id)
                )
            ).first()
            if row is None:
                print(f"  pick={pick_id} no existe.")
                continue
            pick, received_at = row
            old = pick.fecha_evento
            pick.fecha_evento = received_at
            session.add(pick)
            print(
                f"  pick={pick_id} ({(pick.seleccion or '')[:50]!r}) "
                f"fecha_evento: {old} -> {received_at}"
            )

        await session.commit()
    print("Reparación completada.")


if __name__ == "__main__":
    asyncio.run(main())
