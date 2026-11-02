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
    if not m:
        return None
    # "GANA DE JONG Y +7,5...": la conjunción final no es parte del nombre.
    return re.sub(r"\s+[YE]$", "", m.group(1).strip())


# Evento corrupto tipo "Jue - sep": el extractor guardó el fragmento de
# fecha como si fuera el cruce. No vale ni como pista de torneo.
_DATE_FRAGMENT = re.compile(
    r"^(?:lun|mar|mi[ée]|jue|vie|s[áa]b|dom|"
    r"ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic|"
    r"\d{1,2}|\d{4})[\s\-/:]*(?:\w*)$",
    re.IGNORECASE,
)


def _looks_like_cross(evento: str | None) -> bool:
    """El evento guardado parece un cruce real ("A vs B"), no un torneo
    ni un fragmento de fecha."""
    if not evento:
        return False
    if _DATE_FRAGMENT.match(evento.strip()):
        return False
    return bool(re.search(r"\bvs\b| - ", evento, re.IGNORECASE))


def _torneo_of(raw_text: str) -> str | None:
    """Línea de competición del mensaje ("CHALL SAINT TROPEZ DOBLES") —
    pista para find_fixture cuando el evento guardado es basura."""
    m = re.search(
        r"(?im)^[^\n]*\b(?:chall(?:enger)?|atp|wta|itf|copa|open)\b[^\n]*$",
        raw_text,
    )
    return m.group(0).strip().strip("*_# 🏆🎾") if m else None


async def fix_fixtures(pick_ids: list[int], apply: bool) -> None:
    from app.models.telegram_raw_message import TelegramRawMessage

    settings = get_settings()
    provider = GeminiResearchProvider("tenis", settings.google_api_key)
    async with AsyncSessionLocal() as session:
        for pid in pick_ids:
            pick = await session.get(ParsedPick, pid)
            if pick is None:
                print(f"#{pid}: no existe")
                continue
            if _looks_like_cross(pick.evento):
                print(f"#{pid}: evento ya tiene cruce ({pick.evento!r})")
                continue
            player = _player_of(pick)
            if not player:
                print(f"#{pid}: sin jugador en selección {pick.seleccion!r}")
                continue
            # Pista de torneo: el evento guardado si parece competición;
            # si es basura ("Jue - sep"), la línea del torneo del raw.
            torneo = pick.evento
            if (
                torneo is None
                or _looks_like_cross(torneo)
                or _DATE_FRAGMENT.match(torneo.strip())
            ):
                torneo = None
            if torneo is None and pick.raw_message_id:
                raw = await session.get(TelegramRawMessage, pick.raw_message_id)
                if raw:
                    torneo = _torneo_of(raw.text or raw.extracted_text or "")
            fixture = await provider.find_fixture(pick.fecha_evento, player, torneo)
            if not fixture:
                print(f"#{pid}: Gemini no encontró fixture ({player} @ {torneo})")
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
                    if not _looks_like_cross(leg.evento):
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
