# ruff: noqa: E402
"""Repara picks mal extraídos re-procesando su material guardado.

DRY-RUN por defecto: muestra qué corregiría la re-extracción sin
escribir nada. Con `--apply` aplica los cambios en sitio (patas viejas
quedan excluidas como anuladas, nunca se borran filas).

Modos:
    python scripts/repair_extraction.py --ids 743,1172,3612
    python scripts/repair_extraction.py --ids 743,1172,3612 --apply
    python scripts/repair_extraction.py --fixtures 1325,1335,3200 --apply

`--fixtures` reconstruye el `evento` de patas/picks que solo guardaron
el torneo ("CHALLENGER BIELLA") preguntando a Gemini por el cruce real
("Brunold Biella" -> "Brunold vs X").
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
from app.models.parsed_pick import ParsedPick
from app.services.maintenance.repair import repair_picks
from app.services.results.gemini_research import GeminiResearchProvider

# "GANA BRUNOLD Y +7,5..." / "De Jong gana" -> el jugador de la pata.
_PLAYER_PATTERN = re.compile(
    r"gana\s+([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.]+"
    r"(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ.]+){0,2})",
    re.IGNORECASE,
)


def _arg(name: str) -> list[int]:
    if name not in sys.argv:
        return []
    return [int(x) for x in sys.argv[sys.argv.index(name) + 1].split(",")]


def _player_of(pick: ParsedPick) -> str | None:
    m = _PLAYER_PATTERN.search(pick.seleccion or "")
    return m.group(1) if m else None


async def fix_fixtures(pick_ids: list[int], apply: bool) -> None:
    settings = get_settings()
    provider = GeminiResearchProvider("tenis", settings.google_api_key)
    async with AsyncSessionLocal() as session:
        for pid in pick_ids:
            pick = await session.get(ParsedPick, pid)
            if pick is None:
                print(f"#{pid}: no existe")
                continue
            if re.search(r"\bvs\b| - ", pick.evento or "", re.IGNORECASE):
                print(f"#{pid}: evento ya tiene cruce ({pick.evento!r})")
                continue
            player = _player_of(pick)
            if not player:
                print(f"#{pid}: sin jugador en selección {pick.seleccion!r}")
                continue
            fixture = await provider.find_fixture(
                pick.fecha_evento, player, pick.evento
            )
            if not fixture:
                print(f"#{pid}: Gemini no encontró fixture ({player} @ {pick.evento})")
                continue
            print(f"#{pid}: {pick.evento!r} -> {fixture!r}")
            if apply:
                pick.evento = fixture
                session.add(pick)
                # Las patas heredan el evento del padre si no tienen
                # cruce propio.
                legs = (
                    await session.exec(
                        select(ParsedPick).where(ParsedPick.combinada_id == pid)
                    )
                ).all()
                for leg in legs:
                    if not re.search(r"\bvs\b| - ", leg.evento or "", re.IGNORECASE):
                        leg.evento = fixture
                        session.add(leg)
        if apply:
            await session.commit()


async def main() -> None:
    apply = "--apply" in sys.argv
    ids = _arg("--ids")
    fixtures = _arg("--fixtures")

    if fixtures:
        await fix_fixtures(fixtures, apply)
        return

    if not ids:
        print("Uso: --ids <ids> [--apply] | --fixtures <ids> [--apply]")
        return

    reports = await repair_picks(ids, apply=apply)
    for r in reports:
        print(f"#{r.pick_id} [{r.action}] {r.detail}")
        if r.old:
            print(f"   antes: {r.old}")
        if r.new:
            print(f"   nuevo: {r.new}")
    if not apply:
        print("\nDRY-RUN: nada escrito. Relanza con --apply para aplicar.")


if __name__ == "__main__":
    asyncio.run(main())
