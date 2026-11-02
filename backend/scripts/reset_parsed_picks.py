# ruff: noqa: E402
"""Borra TODOS los ParsedPick y resetea `processed=False` en los mensajes
crudos, para poder relanzar la extracción desde cero (p. ej. tras mejorar
el extractor) sin perder los mensajes originales de Telegram.

Uso:
    .venv\\Scripts\\python.exe scripts\\reset_parsed_picks.py --apply

Sin --apply solo muestra cuántos registros se verían afectados (dry-run).
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
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401


async def main() -> None:
    apply_changes = "--apply" in sys.argv

    async with AsyncSessionLocal() as session:
        parsed_result = await session.exec(select(ParsedPick))
        parsed_picks = list(parsed_result.scalars().all())

        raw_result = await session.exec(select(TelegramRawMessage))
        raw_messages = list(raw_result.scalars().all())

        print(f"ParsedPick a borrar: {len(parsed_picks)}")
        print(f"TelegramRawMessage a marcar processed=False: {len(raw_messages)}")

        if not apply_changes:
            print("Dry-run: no se ha borrado ni modificado nada. Usa --apply.")
            return

        for pick in parsed_picks:
            await session.delete(pick)

        for raw in raw_messages:
            raw.processed = False
            session.add(raw)

        await session.commit()
        print("Listo: ParsedPicks borrados y mensajes crudos marcados como pendientes.")


if __name__ == "__main__":
    asyncio.run(main())
