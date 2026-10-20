# ruff: noqa: E402
"""Re-traduce los payloads crudos de oddspapi guardados en disco.

Cada respuesta de /historical-odds queda persistida por el backfill
(`oddspapi_data/payloads/`). Cuando el mapa de traducción mejora (p. ej.
nuevos mercados de equipo), los picks que quedaron `nomarket` pueden
resolverse SIN pagar otra llamada a la API:

    .\\.venv\\Scripts\\python.exe scripts\\retranslate_papi.py            # dry-run
    .\\.venv\\Scripts\\python.exe scripts\\retranslate_papi.py --apply    # escribe

También limpia marcas `missed` por prefijo (p. ej. tras mejorar la
ventana de búsqueda de fixtures):

    .\\.venv\\Scripts\\python.exe scripts\\retranslate_papi.py --clear-marks oddspapi|nofixture| --apply
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlmodel import select

from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401
from app.services.odds import oddspapi
from app.services.odds.compare import map_pick_choices
from app.services.odds.oddspapi import downsample
from app.services.results.base import _STATE_FILE

_MARK = f"{oddspapi.NAME}|nomarket|"


def _missed() -> dict:
    return json.loads(_STATE_FILE.read_text(encoding="utf-8"))["missed"]


def clear_marks(prefix: str, apply: bool) -> int:
    """Borra las marcas `missed` que empiezan por `prefix`. Devuelve n."""
    path = _STATE_FILE
    state = json.loads(path.read_text(encoding="utf-8"))
    keys = [k for k in state.get("missed", {}) if k.startswith(prefix)]
    print(f"marcas '{prefix}*': {len(keys)}")
    if apply and keys:
        for key in keys:
            del state["missed"][key]
        path.write_text(json.dumps(state), encoding="utf-8")
        print("borradas — la próxima pasada los reintenta")
    return len(keys)


async def retranslate(apply: bool) -> None:
    markets = oddspapi.load_markets()
    if markets is None:
        print("sin catálogo en disco (oddspapi_data/markets.json) — nada que hacer")
        return
    marked_ids = [
        int(key.rsplit("|", 1)[1])
        for key in _missed()
        if key.startswith(_MARK) and key.rsplit("|", 1)[1].isdigit()
    ]
    print(f"picks marcados {_MARK}* en provider_state: {len(marked_ids)}")
    if not marked_ids:
        return

    async with AsyncSessionLocal() as session:
        picks = (
            await session.exec(select(ParsedPick).where(ParsedPick.id.in_(marked_ids)))
        ).all()
        resueltos = sin_payload = sin_opcion = sin_evento = filas = 0
        for pick in picks:
            ext_id = pick.odds_event_id
            if not ext_id:
                sin_evento += 1
                continue
            envelope = oddspapi.load_payload(ext_id)
            if envelope is None:
                sin_payload += 1
                print(f"  ? pick {pick.id}: {ext_id} sin payload guardado")
                continue
            start = None
            if envelope.get("start"):
                try:
                    start = datetime.fromisoformat(envelope["start"])
                except ValueError:
                    start = None
            event = OddsEvent(
                event_ext_id=ext_id,
                sport=pick.deporte or "futbol",
                home_team=envelope.get("home_name") or "",
                away_team=envelope.get("away_name") or "",
                start=start,
            )
            choices = oddspapi.translate_history(
                envelope["payload"], markets, event.home_team, event.away_team
            )
            virtual = [
                OddsSnapshot(
                    provider=oddspapi.NAME,
                    event_ext_id=ext_id,
                    market_name=c.market_name,
                    choice_name=c.choice_name,
                    choice_group=c.choice_group,
                    cuota=p.cuota,
                    cuota_apertura=c.cuota_apertura,
                    suspended=p.suspended,
                    captured_at=p.captured_at,
                )
                for c in choices
                for p in c.points
            ]
            matched = map_pick_choices(pick, event, virtual)
            if matched is None:
                sin_opcion += 1
                continue
            resueltos += 1
            print(
                f"  + pick {pick.id}: {matched.market_name} / "
                f"{matched.choice_name} ({matched.choice_group}) "
                f"[{envelope['home_name']} vs {envelope['away_name']}]"
            )
            if apply:
                by_key = {
                    (c.market_name, c.choice_name, c.choice_group): c for c in choices
                }
                key = (
                    matched.market_name,
                    matched.choice_name,
                    matched.choice_group,
                )
                choice = by_key[key]
                # No duplicar: si la opción ya tiene filas libres del
                # mismo key, se re-sellan al pick en vez de insertar.
                existing = (
                    await session.exec(
                        select(OddsSnapshot)
                        .where(OddsSnapshot.provider == oddspapi.NAME)
                        .where(OddsSnapshot.event_ext_id == ext_id)
                        .where(OddsSnapshot.market_name == key[0])
                        .where(OddsSnapshot.choice_name == key[1])
                        .where(
                            (OddsSnapshot.choice_group == key[2])
                            if key[2] is not None
                            else (OddsSnapshot.choice_group == None)  # noqa: E711
                        )
                        .where(OddsSnapshot.parsed_pick_id == None)  # noqa: E711
                    )
                ).all()
                if existing:
                    for row in existing:
                        row.parsed_pick_id = pick.id
                        session.add(row)
                    filas += len(existing)
                    continue
                for point in downsample(choice.points):
                    session.add(
                        OddsSnapshot(
                            provider=oddspapi.NAME,
                            event_ext_id=ext_id,
                            market_name=key[0],
                            choice_name=key[1],
                            choice_group=key[2],
                            cuota=point.cuota,
                            cuota_apertura=choice.cuota_apertura,
                            suspended=point.suspended,
                            captured_at=point.captured_at,
                            parsed_pick_id=pick.id,
                        )
                    )
                    filas += 1
        if apply:
            await session.commit()
        print(
            f"\nresueltos: {resueltos} · sin opción aún: {sin_opcion} · "
            f"sin payload: {sin_payload} · sin ext_id: {sin_evento} · "
            f"filas {'insertadas' if apply else 'a insertar'}: {filas}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="escribe en BD")
    parser.add_argument(
        "--clear-marks",
        metavar="PREFIX",
        default=None,
        help="borra marcas missed con ese prefijo (p. ej. 'oddspapi|nofixture|')",
    )
    args = parser.parse_args()
    if args.clear_marks:
        clear_marks(args.clear_marks, args.apply)
    else:
        asyncio.run(retranslate(args.apply))
    if not args.apply:
        print("\n(dry-run: nada escrito — repite con --apply para persistir)")


if __name__ == "__main__":
    main()
