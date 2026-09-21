# ruff: noqa: E402
"""Limpieza de picks pendientes que nunca se resolverán solos.

Actúa sobre `parsed_picks` con `acierto IS NULL`, `anulada=False` y
`es_apuesta=True`. Tres reglas:

A) ANULAR — sin partido identificable: `evento` vacío o sin cruce
   ("A - B" / "A vs B" / "A/B") y que no sea tenis salvable por nombre
   de jugador en `seleccion` (el verifier de tenis puede buscar por
   jugador cuando el evento solo trae el torneo). Incluye el marketing
   que se coló como apuesta y los eventos-liga ("LA LIGA", "España
   Segunda División"): ningún provider puede localizar el partido.
B) ANULAR — props de "Ipswich - Liverpool": combinada de props de
   jugador importada con `fecha_evento` = fecha de importación
   (sep-2026); el partido real es de ago-2025 — fuera de la ventana de
   verificación y con fecha equivocada, miss garantizado eterno.

Los PADRES combinada (`es_combinada=True` con patas) se excluyen de A/B:
su `evento` es irrelevante — su estado lo deriva `settle_combinada` de
las patas (pata anulada = excluida a cuota 1.0, no perdida). Anular el
padre a mano convertiría en void una combinada cuyas patas activas
ganaron. Solo un padre SIN patas (huérfano de extracción) entra en A.
C) RELLENAR `fecha_evento = raw.received_at`: picks reales sin fecha
   (cruce válido en `evento`, o tenis con jugador extraíble de
   `seleccion`). El tipster publica el pick el día del partido, así que
   `received_at` es la mejor estimación. Al tener fecha entran en el
   ciclo del verifier; si son patas, su padre combinada se re-liquida
   solo cuando todas las patas se resuelvan.

Las patas anuladas re-liquidan a su padre combinada (`settle_combinada`)
como si el usuario las anulara a mano.

Uso:
    .\\.venv\\Scripts\\python.exe scripts\\clean_pending_noise.py            # dry-run
    .\\.venv\\Scripts\\python.exe scripts\\clean_pending_noise.py --apply    # escribe
"""

import argparse
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlmodel import select

from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.results.verifier import (
    _extract_tennis_player_name,
    _normalize_sport,
    settle_combinada,
)

# Separadores de cruce que identifican un partido real en `evento`.
_MATCHUP_SEP = re.compile(r"\s+-\s+|\s+vs\.?\s+|/")


def _has_matchup(evento: str | None) -> bool:
    return bool(evento and _MATCHUP_SEP.search(evento))


def _tennis_salvageable(pick: ParsedPick) -> bool:
    """True si es tenis y `seleccion` aporta un jugador buscable
    (mismo fallback que `_tennis_lookup_hint` del verifier)."""
    return _normalize_sport(pick.deporte) == "tenis" and bool(
        _extract_tennis_player_name(pick.seleccion or "")
    )


async def run(apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        pendientes = (
            (
                await session.execute(
                    select(ParsedPick).where(
                        ParsedPick.acierto.is_(None),  # type: ignore[union-attr]
                        ParsedPick.anulada.is_(False),  # type: ignore[union-attr]
                        ParsedPick.es_apuesta.is_(True),  # type: ignore[union-attr]
                    )
                )
            )
            .scalars()
            .all()
        )

        # Padres combinada que tienen patas: su estado lo deriva
        # settle_combinada, nunca se anulan directamente por A/B.
        con_patas = set(
            (
                await session.execute(
                    select(ParsedPick.combinada_id)
                    .where(ParsedPick.combinada_id.is_not(None))  # type: ignore[union-attr]
                    .group_by(ParsedPick.combinada_id)
                )
            )
            .scalars()
            .all()
        )

        anular: list[tuple[ParsedPick, str]] = []
        rellenar: list[ParsedPick] = []
        for p in pendientes:
            ev = (p.evento or "").strip()
            if p.es_combinada and p.id in con_patas:
                continue
            if "ipswich" in ev.lower() and "liverpool" in ev.lower():
                anular.append((p, "B:fecha-importación"))
            elif not _has_matchup(ev) and not _tennis_salvageable(p):
                anular.append((p, "A:sin-partido"))
            elif p.fecha_evento is None:
                rellenar.append(p)

        print(f"Pendientes analizados: {len(pendientes)}")
        print(f"\n== A/B: a anular ({len(anular)}) ==")
        for p, why in anular:
            print(
                f"  id={p.id} [{why}] ev={str(p.evento)[:45]!r} "
                f"sel={str(p.seleccion)[:35]!r} comb={p.combinada_id}"
            )
        print(f"\n== C: rellenar fecha con received_at ({len(rellenar)}) ==")

        padres: set[int] = set()
        for p in rellenar:
            raw = await session.get(TelegramRawMessage, p.raw_message_id)
            rec = raw.received_at if raw else None
            print(
                f"  id={p.id} ev={str(p.evento)[:45]!r} "
                f"sel={str(p.seleccion)[:30]!r} -> fecha={rec} "
                f"comb={p.combinada_id}"
            )
            if apply and rec is not None:
                p.fecha_evento = rec
                session.add(p)

        if apply:
            for p, _why in anular:
                p.anulada = True
                p.verificado_por = "manual"
                session.add(p)
                if p.combinada_id:
                    padres.add(p.combinada_id)
            await session.flush()
            # Las patas anuladas/rellenadas re-liquidan su combinada.
            for parent_id in padres:
                parent = await session.get(ParsedPick, parent_id)
                if parent is not None:
                    await settle_combinada(session, parent)
            await session.commit()
            print(
                f"\nAPLICADO: {len(anular)} anuladas, "
                f"{sum(1 for p in rellenar)} fechas rellenadas, "
                f"{len(padres)} combinadas re-liquidadas."
            )
        else:
            print("\n(dry-run — sin cambios; --apply para escribir)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="escribe en BD")
    args = parser.parse_args()
    asyncio.run(run(args.apply))
