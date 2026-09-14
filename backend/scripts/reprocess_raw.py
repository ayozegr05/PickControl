# ruff: noqa: E402
"""Reprocesa mensajes crudos ya guardados con el extractor híbrido.

Uso:
    .venv\\Scripts\\python.exe scripts\reprocess_raw.py

Crea un ParsedPick para cada mensaje que lo permita y marca processed=True.
Si ya existe un ParsedPick para ese raw_message_id, lo salta.
"""

import asyncio
import os
import sys

# Permite importar `app` cuando se ejecuta desde scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.telegram.pick_extractor import extract_pick
from app.services.telegram.processor import _find_duplicate_pick


async def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY no está configurada.")
        return

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(TelegramRawMessage)
            .where(TelegramRawMessage.processed == False)  # noqa: E712
            .order_by(TelegramRawMessage.received_at.desc())
        )
        raw_messages = list(result.scalars().all())

        print(f"Mensajes por reprocesar: {len(raw_messages)}")

        for raw in raw_messages:
            existing = await session.exec(
                select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
            )
            if existing.first():
                print(f"  [{raw.id}] ya tiene ParsedPick, salto.")
                continue

            source_text = (raw.extracted_text or raw.text or "").strip()
            if not source_text:
                print(f"  [{raw.id}] sin texto, salto.")
                continue

            pick = await extract_pick(
                source_text, settings.openai_api_key, informante=raw.channel_name
            )
            if not pick:
                continue

            duplicate = None
            if pick.es_apuesta:
                duplicate = await _find_duplicate_pick(session, raw.channel_name, pick)

            if duplicate:
                print(
                    f"  [{raw.id}] duplicado de ParsedPick id={duplicate.id}, "
                    f"'{pick.seleccion}' ~ '{duplicate.seleccion}', no se crea."
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
                    informante=raw.channel_name,
                    explicacion=pick.explicacion,
                    metodo=pick.metodo,
                    confianza=pick.confianza,
                )
                session.add(parsed)
                await session.flush()

            raw.processed = True
            session.add(raw)

            print(
                f"  [{raw.id}] metodo={pick.metodo} es_apuesta={pick.es_apuesta} seleccion={pick.seleccion}"
            )

        await session.commit()

    print("Reproceso completado.")


if __name__ == "__main__":
    asyncio.run(main())
