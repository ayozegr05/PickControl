# ruff: noqa: E402
"""Dry-run del emparejado foto+texto sobre raws reales.

Para cada par (foto, texto) conocido ejecuta extract_pick sobre el
texto combinado `texto + OCR del boleto` —exactamente lo que haría el
processor con _PAIR_WINDOW— y muestra el resultado SIN escribir en BD.

Uso:
    .venv\\Scripts\\python.exe scripts\\dryrun_pairing.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import and_, select

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.telegram.pick_extractor import extract_pick

# (canal_id, message_id foto, message_id texto) — casos reales reportados.
PAIRS = [
    ("Dm7 Gratuito", -1001125596067, 84472, 84473),
    ("Lady Bets", -1002077450014, 13610, 13611),
    ("Bet Fran", -1002491428353, 14848, 14849),
    ("AllSportsPicks Mallorca", -1002463340479, 79579, 79580),
    ("AllSportsPicks dobles", -1002463340479, 79619, 79620),
    ("CopetePicks (combinada)", -1001914772235, 21864, 21865),
]

# Texto solo (sin foto pareja): el caso del título largo de Dm7 Allsports.
TEXT_ONLY = [
    ("Dm7 Allsports", -1001472388026, 62978),
]


async def _raw(session, channel_id: int, message_id: int):
    result = await session.exec(
        select(TelegramRawMessage).where(
            and_(
                TelegramRawMessage.channel_id == channel_id,
                TelegramRawMessage.message_id == message_id,
            )
        )
    )
    return result.scalars().first()


async def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY no está configurada.")
        return

    async with AsyncSessionLocal() as session:
        for label, channel_id, photo_id, text_id in PAIRS:
            photo = await _raw(session, channel_id, photo_id)
            text = await _raw(session, channel_id, text_id)
            print(f"\n=== {label} (foto {photo_id} + texto {text_id}) ===")
            if not photo or not text:
                print(f"  raw no encontrado (photo={bool(photo)} text={bool(text)})")
                continue
            ocr = (photo.extracted_text or "").strip()
            if not ocr:
                print("  la foto no tiene OCR guardado, salto.")
                continue
            combined = f"{text.text}\n\n{ocr}"
            pick = await extract_pick(
                combined,
                settings.openai_api_key,
                informante=text.channel_name,
                fecha_referencia=text.received_at,
            )
            _show(pick)

        for label, channel_id, text_id in TEXT_ONLY:
            text = await _raw(session, channel_id, text_id)
            print(f"\n=== {label} (solo texto {text_id}) ===")
            if not text:
                print("  raw no encontrado.")
                continue
            pick = await extract_pick(
                text.text or "",
                settings.openai_api_key,
                informante=text.channel_name,
                fecha_referencia=text.received_at,
            )
            _show(pick)


def _show(pick) -> None:
    if pick is None:
        print("  -> extract_pick devolvió None")
        return
    print(f"  es_apuesta={pick.es_apuesta} metodo={pick.metodo}")
    print(f"  seleccion={pick.seleccion!r}")
    print(f"  evento={pick.evento!r} mercado={pick.mercado!r} deporte={pick.deporte!r}")
    print(f"  cuota={pick.cuota} stake={pick.stake} linea={pick.linea}")
    if pick.patas:
        print(f"  patas={len(pick.patas)}")
        for p in pick.patas:
            print(f"    - {p.seleccion!r} evento={p.evento!r} linea={p.linea}")


if __name__ == "__main__":
    asyncio.run(main())
