# ruff: noqa: E402
"""Dedup histórico de combinadas: mismo boleto republicado en un canal.

La dedup en tiempo real solo ve ±6 h alrededor del mensaje: cuando un
tipster republica el mismo slip días después, cada post creaba su
combinada (caso: slips de cuota ~91 en Dm7/AllSportsPicks de septiembre
que se limpiaron a mano). Este barrido agrupa las combinadas de cada
canal por FIRMA (patas normalizadas + cuota) y:

- Deja UNA canónica por firma: la más antigua con cuota (si ninguna la
  tiene, la más antigua). Si quedó anulada a mano, la devuelve a
  pendiente junto con sus patas anuladas sin veredicto para que la
  cascada las re-verifique — el settle llega solo.
- Marca el resto como `anulada · duplicado` (visibles en la lista de
  depuración, fuera de las stats del tipster). Sus patas pendientes se
  marcan igual; las que ya tienen veredicto se conservan (dato real).

Dry-run por defecto; `--apply` escribe en BD.

Uso:
    .venv\\Scripts\\python.exe scripts\\dedup_combinadas.py
    .venv\\Scripts\\python.exe scripts\\dedup_combinadas.py --apply
    docker exec controlpick-backend python scripts/dedup_combinadas.py --apply
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FKs de ParsedPick)
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.telegram.processor import combinada_signature

_DUP_REASON = "duplicado"


def _reset_to_pending(p: ParsedPick) -> None:
    """Vuelve a la cola de verificación: la cascada decide su suerte."""
    p.anulada = False
    p.acierto = None
    p.motivo_anulada = None
    p.verificado_por = None
    p.verificado_provider = None
    p.verificado_at = None
    p.anulada_rechecks = 0
    p.anulada_last_recheck = None


def _mark_duplicate(p: ParsedPick, now) -> None:
    p.anulada = True
    p.acierto = None
    p.motivo_anulada = _DUP_REASON
    p.verificado_por = "auto"
    p.verificado_provider = None
    p.verificado_at = now


async def main(apply: bool) -> None:
    now = utc_now()
    async with AsyncSessionLocal() as session:
        parents = list(
            (
                await session.exec(
                    select(ParsedPick)
                    .where(ParsedPick.es_apuesta)
                    .where(ParsedPick.es_combinada)
                    .where(ParsedPick.combinada_id.is_(None))  # type: ignore[union-attr]
                )
            )
            .scalars()
            .all()
        )
        legs = list(
            (
                await session.exec(
                    select(ParsedPick)
                    .where(
                        ParsedPick.combinada_id.in_(  # type: ignore[union-attr]
                            [p.id for p in parents]
                        )
                    )
                    .where(ParsedPick.es_apuesta)
                )
            )
            .scalars()
            .all()
        )
        legs_by_parent: dict[int, list[ParsedPick]] = defaultdict(list)
        for leg in legs:
            legs_by_parent[leg.combinada_id].append(leg)

        groups: dict[tuple, list[ParsedPick]] = defaultdict(list)
        for parent in parents:
            sig = (parent.informante, combinada_signature(legs_by_parent[parent.id]))
            groups[sig].append(parent)

        n_dupes = 0
        n_reopened = 0
        for (channel, _sig), members in groups.items():
            if len(members) < 2:
                continue
            # Canónica: la más antigua con cuota (si ninguna tiene, la
            # más antigua) — el repost suele ser el que mejor extrajo.
            with_cuota = [m for m in members if m.cuota is not None]
            canonical = min(with_cuota or members, key=lambda m: m.id)
            dupes = [m for m in members if m.id != canonical.id]
            print(
                f"CANAL {channel}: firma compartida por "
                f"{sorted(m.id for m in members)} -> canónica #{canonical.id}, "
                f"{len(dupes)} duplicada(s)"
            )
            for dup in dupes:
                print(f"  duplicada #{dup.id} -> anulada·{_DUP_REASON}")
                n_dupes += 1
                if apply:
                    _mark_duplicate(dup, now)
                    session.add(dup)
                for leg in legs_by_parent[dup.id]:
                    # Las patas con veredicto conservan su dato real;
                    # solo las que no resolvieron se marcan duplicado.
                    if leg.acierto is not None:
                        continue
                    if apply:
                        _mark_duplicate(leg, now)
                        session.add(leg)
            # La canónica queda en juego: si estaba anulada a mano (o el
            # padre nunca llegó a liquidar), vuelve a pendiente y sus
            # patas anuladas sin veredicto también — la cascada decide.
            if canonical.anulada and canonical.acierto is None:
                print(f"  canónica #{canonical.id} -> pendiente (re-verifica)")
                n_reopened += 1
                if apply:
                    _reset_to_pending(canonical)
                    session.add(canonical)
                for leg in legs_by_parent[canonical.id]:
                    if leg.anulada and leg.acierto is None:
                        print(f"    pata #{leg.id} -> pendiente")
                        if apply:
                            _reset_to_pending(leg)
                            session.add(leg)
            elif canonical.anulada and canonical.motivo_anulada == _DUP_REASON:
                # Canónica ya marcada duplicada por una pasada anterior
                # (la otra se eligió de canónica): deshacer el dup.
                print(f"  canónica #{canonical.id} era dup -> pendiente")
                n_reopened += 1
                if apply:
                    _reset_to_pending(canonical)
                    session.add(canonical)
                for leg in legs_by_parent[canonical.id]:
                    if leg.anulada and leg.motivo_anulada == _DUP_REASON:
                        if apply:
                            _reset_to_pending(leg)
                            session.add(leg)

        if apply:
            await session.commit()

    print()
    print(
        f"FIN: {n_dupes} combinadas duplicadas"
        + (f", {n_reopened} canónicas devueltas a pendiente" if n_reopened else "")
        + ("" if apply else " (dry-run: nada escrito, usa --apply)")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="escribe en BD")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
