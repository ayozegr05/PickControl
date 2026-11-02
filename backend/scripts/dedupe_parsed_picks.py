# ruff: noqa: E402
"""Elimina ParsedPicks duplicados ya guardados (mismo canal + pick repetido).

Usa la misma heurística de similitud que `processor.py` (texto similar y/o
cuota igual). Se queda con el registro más antiguo de cada grupo de
duplicados y borra el resto.

Uso:
    .venv\\Scripts\\python.exe scripts\\dedupe_parsed_picks.py [--apply]

Sin --apply solo muestra qué borraría (dry-run).
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
from app.models.user import User  # noqa: F401
from app.services.telegram.processor import _text_similarity, _word_set_similarity

_TEXT_SIMILARITY = 0.8
_TEXT_SIMILARITY_WITH_MATCHING_CUOTA = 0.6
_WORD_SET_SIMILARITY = 0.85


def _is_duplicate(a: ParsedPick, b: ParsedPick) -> bool:
    if not a.seleccion or not b.seleccion:
        return False
    similarity = _text_similarity(a.seleccion, b.seleccion)
    if similarity >= _TEXT_SIMILARITY:
        return True
    if _word_set_similarity(a.seleccion, b.seleccion) >= _WORD_SET_SIMILARITY:
        return True
    cuotas_coinciden = (
        a.cuota is not None and b.cuota is not None and abs(a.cuota - b.cuota) < 0.01
    )
    return cuotas_coinciden and similarity >= _TEXT_SIMILARITY_WITH_MATCHING_CUOTA


async def main() -> None:
    apply_changes = "--apply" in sys.argv

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ParsedPick)
            .where(ParsedPick.es_apuesta == True)  # noqa: E712
            .order_by(ParsedPick.informante, ParsedPick.created_at)
        )
        picks = list(result.scalars().all())

        by_channel: dict[str, list[ParsedPick]] = {}
        for pick in picks:
            by_channel.setdefault(pick.informante or "desconocido", []).append(pick)

        to_delete: list[ParsedPick] = []
        for channel, channel_picks in by_channel.items():
            kept: list[ParsedPick] = []
            for pick in channel_picks:
                duplicate_of = next((k for k in kept if _is_duplicate(k, pick)), None)
                if duplicate_of:
                    print(
                        f"[{channel}] id={pick.id} '{pick.seleccion}' es duplicado "
                        f"de id={duplicate_of.id} '{duplicate_of.seleccion}'"
                    )
                    to_delete.append(pick)
                else:
                    kept.append(pick)

        print(f"\nTotal duplicados encontrados: {len(to_delete)}")

        if apply_changes:
            for pick in to_delete:
                await session.delete(pick)
            await session.commit()
            print("Duplicados eliminados.")
        else:
            print("Dry-run: no se ha borrado nada. Ejecuta con --apply para borrar.")


if __name__ == "__main__":
    asyncio.run(main())
