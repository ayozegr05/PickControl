# ruff: noqa: E402
"""Backfill de informante_id en parsed_picks existentes.

Crea un Informante para cada nombre de canal distinto y rellena
parsed_picks.informante_id a partir de parsed_picks.informante.

Uso:
    .venv\\Scripts\\python.exe scripts\\backfill_parsed_informantes.py [--apply]

Sin --apply solo muestra qué haría (dry-run).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.pick_service import get_or_create_informante


async def main() -> None:
    apply = "--apply" in sys.argv

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ParsedPick).where(ParsedPick.informante_id.is_(None))
        )
        picks = list(result.scalars().all())

        if not picks:
            print("No hay parsed_picks sin informante_id. Nada que hacer.")
            return

        # Agrupar por nombre de canal para minimizar consultas.
        by_name: dict[str, list[ParsedPick]] = {}
        for pick in picks:
            by_name.setdefault(pick.informante or "desconocido", []).append(pick)

        print(f"Picks pendientes de backfill: {len(picks)}")
        for channel, channel_picks in by_name.items():
            print(f"  {channel}: {len(channel_picks)}")

        if not apply:
            print("\nDry-run: no se ha modificado nada. Ejecuta con --apply.")
            return

        for channel, channel_picks in by_name.items():
            informante = await get_or_create_informante(session, channel)
            for pick in channel_picks:
                pick.informante_id = informante.id
                session.add(pick)

        await session.commit()
        print(f"\nBackfill completado: {len(picks)} picks actualizados.")


if __name__ == "__main__":
    asyncio.run(main())
