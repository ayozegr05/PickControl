# ruff: noqa: E402
"""Reprocesa raws processed=False con texto de los últimos N días.

Versión acotada de reprocess_raw.py: solo reintenta raws recientes que
tienen contenido (los vacíos sin texto/media solo se recuperan
re-descargando de Telegram — lo hace el catch-up al arrancar).

Uso:
    .venv\\Scripts\\python.exe scripts\\reprocess_recent.py [dias]
"""

import asyncio
import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from openai import RateLimitError
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.pick_service import get_or_create_informante
from app.services.telegram.pick_extractor import extract_pick
from app.services.telegram.processor import _find_duplicate_pick, _merge_pick_data


async def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY no está configurada.")
        return

    cutoff = utc_now() - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(TelegramRawMessage)
            .where(TelegramRawMessage.processed == False)  # noqa: E712
            .where(TelegramRawMessage.received_at >= cutoff)
            .order_by(TelegramRawMessage.received_at)
        )
        raws = list(result.all())
        print(f"Raws processed=False desde {cutoff.date()}: {len(raws)}")

        created = skipped = 0
        for raw in raws:
            existing = await session.exec(
                select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
            )
            if existing.first():
                raw.processed = True
                session.add(raw)
                skipped += 1
                continue

            source_text = (raw.extracted_text or raw.text or "").strip()
            if not source_text:
                print(
                    f"  raw={raw.id} msg={raw.message_id}: sin texto "
                    "(necesita re-descarga de Telegram), salto."
                )
                skipped += 1
                continue

            pick = None
            for attempt, wait in enumerate((5, 20, 60), start=1):
                try:
                    pick = await extract_pick(
                        source_text,
                        settings.openai_api_key,
                        informante=raw.channel_name,
                        fecha_referencia=raw.received_at,
                    )
                    break
                except RateLimitError:
                    print(
                        f"  raw={raw.id}: 429 de OpenAI "
                        f"(intento {attempt}), espero {wait}s..."
                    )
                    await asyncio.sleep(wait)
                except Exception as exc:  # noqa: BLE001
                    print(f"  raw={raw.id}: extractor falló de nuevo: {exc}")
                    break
            if pick is None:
                skipped += 1
                continue

            if pick.fecha_evento is None and raw.received_at is not None:
                pick.fecha_evento = raw.received_at

            # El catch-up del backend puede haber borrado y recreado el raw
            # mientras extraíamos: comprobar que sigue existiendo antes de
            # insertar el pick que referencia su id (FK).
            still_there = await session.exec(
                select(TelegramRawMessage.id).where(TelegramRawMessage.id == raw.id)
            )
            if still_there.first() is None:
                print(
                    f"  raw={raw.id}: eliminado por el catch-up durante "
                    "la extracción, salto."
                )
                skipped += 1
                continue

            duplicate = None
            if pick.es_apuesta:
                duplicate = await _find_duplicate_pick(
                    session, raw.channel_name, pick, raw.received_at
                )

            if duplicate:
                print(
                    f"  raw={raw.id}: duplicado de pick={duplicate.id} "
                    f"({duplicate.seleccion!r}), fusiono."
                )
                if _merge_pick_data(duplicate, pick):
                    session.add(duplicate)
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
                created += 1

            raw.processed = True
            session.add(raw)
            try:
                await session.commit()
            except IntegrityError:
                # Carrera residual con el catch-up (raw borrado tras el check).
                await session.rollback()
                print(f"  raw={raw.id}: conflicto FK con el catch-up, salto.")
                skipped += 1
                continue
            if pick.es_apuesta and not duplicate:
                print(
                    f"  raw={raw.id}: PICK creado "
                    f"sel={pick.seleccion!r} fev={pick.fecha_evento}"
                )
        print(f"Creados/actualizados: {created}, saltados: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
