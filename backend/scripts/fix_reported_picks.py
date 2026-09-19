# ruff: noqa: E402
"""Backfill de los picks contaminados tras el fix de emparejado foto+texto.

Para cada caso reportado re-extrae el pick con el texto del mensaje +
OCR del boleto emparejado (la misma lógica que `_persist` aplica en
vivo), enriquece el pick existente y elimina el duplicado real si la
foto y el texto generaron un pick cada uno (caso Dm7 Gratuito).

Solo toca `parsed_picks`: los raws nunca se borran.

Uso:
    .venv\\Scripts\\python.exe scripts\\fix_reported_picks.py --apply

Sin --apply solo muestra lo que haría.
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
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.telegram.pick_extractor import extract_pick
from app.services.telegram.processor import (
    _enrich_paired_pick,
    _find_pair_slip,
    _persist_patas,
)

APPLY = "--apply" in sys.argv

# (etiqueta, channel_id, message_id del texto del pick)
CASES = [
    ("Dm7 Gratuito Celta-Racing", -1001125596067, 84473),
    ("Lady Bets Bergs", -1002077450014, 13611),
    ("Bet Fran Bergs", -1002491428353, 14849),
    ("AllSports Mallorca", -1002463340479, 79580),
    ("AllSports dobles Copa Davis", -1002463340479, 79620),
    ("CopetePicks combinada", -1001914772235, 21865),
    ("Dm7 Allsports Sevilla-Barça", -1001472388026, 62978),
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


async def _real_pick_of(session, raw_id: int):
    result = await session.exec(
        select(ParsedPick).where(
            and_(
                ParsedPick.raw_message_id == raw_id,
                ParsedPick.es_apuesta == True,  # noqa: E712
            )
        )
    )
    return result.scalars().all()


async def main() -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY no está configurada.")
        return
    print(f"MODO: {'APLICAR' if APPLY else 'DRY-RUN'}")

    async with AsyncSessionLocal() as session:
        for label, channel_id, text_id in CASES:
            print(f"\n=== {label} (texto {text_id}) ===")
            text_raw = await _raw(session, channel_id, text_id)
            if not text_raw:
                print("  raw de texto no encontrado, salto.")
                continue

            slip_raw = await _find_pair_slip(
                session, channel_id, text_raw.received_at, text_id
            )
            source = text_raw.text or ""
            if slip_raw is not None:
                print(f"  emparejado con boleto {slip_raw.message_id}")
                source = f"{source}\n\n{slip_raw.extracted_text}"

            pick = await extract_pick(
                source,
                settings.openai_api_key,
                informante=text_raw.channel_name,
                fecha_referencia=text_raw.received_at,
            )
            if pick is None or not pick.es_apuesta:
                print("  la extracción combinada no ve apuesta, salto.")
                continue

            # Superviviente: el pick real del texto; si no lo hay, el
            # del boleto (la foto lo creó primero en el flujo en vivo).
            text_picks = await _real_pick_of(session, text_raw.id)
            slip_picks = (
                await _real_pick_of(session, slip_raw.id)
                if slip_raw is not None
                else []
            )
            target = (text_picks or slip_picks or [None])[0]
            if target is None:
                print("  no hay pick existente que enriquecer, salto.")
                continue

            print(
                f"  antes: pick {target.id} sel={target.seleccion!r:.55} "
                f"ev={target.evento!r:.40} cuota={target.cuota} "
                f"stake={target.stake}"
            )
            print(
                f"  nuevo: sel={pick.seleccion!r:.55} ev={pick.evento!r:.40} "
                f"mercado={pick.mercado!r} cuota={pick.cuota} "
                f"stake={pick.stake} patas={len(pick.patas)}"
            )

            if not APPLY:
                continue

            _enrich_paired_pick(target, pick)
            if len(pick.patas) >= 2 and not target.es_combinada:
                target.es_combinada = True
                await session.flush()
                await _persist_patas(
                    session,
                    target,
                    pick,
                    target.informante_id,
                    text_raw.channel_name,
                    target.es_reto,
                )
            session.add(target)

            # Duplicado real: foto y texto crearon un pick cada uno.
            duplicados = [p for p in text_picks + slip_picks if p.id != target.id]
            for dup in duplicados:
                print(
                    f"  eliminando pick duplicado id={dup.id} "
                    f"(sel={dup.seleccion!r:.50})"
                )
                await session.delete(dup)

            await session.commit()
            print("  aplicado.")

    print("\nBackfill terminado.")


if __name__ == "__main__":
    asyncio.run(main())
