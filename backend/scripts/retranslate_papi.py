# ruff: noqa: E402
"""Re-traduce los payloads crudos de oddspapi guardados en disco.

Cada respuesta de /historical-odds queda persistida por el backfill
(`oddspapi_data/payloads/`). Cuando el mapa de traducción mejora (p. ej.
nuevos mercados de equipo), los picks que quedaron `nomarket` pueden
resolverse SIN pagar otra llamada a la API. La fase 2 además reencuentra
picks pendientes cuyo fixture llegó a disco pero sus filas no (p. ej. un
commit abortado): match por nombres dentro de la ventana ancha.

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
from app.services.odds.historical_backfill import _register_event
from app.services.odds.oddspapi import downsample
from app.services.odds.snapshotter import _canonical_sport, _lookup_hint
from app.services.results.base import _STATE_FILE, clear_missed, match_score

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


def _payload_ext_ids() -> dict[str, dict]:
    """ext_id -> sobre crudo de cada payload guardado en disco."""
    out: dict[str, dict] = {}
    for path in oddspapi._DATA_DIR.glob("payloads/*.json"):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if envelope.get("ext_id"):
            out[envelope["ext_id"]] = envelope
    return out


def _event_from_envelope(ext_id: str, envelope: dict, sport: str) -> OddsEvent:
    start = None
    if envelope.get("start"):
        try:
            start = datetime.fromisoformat(envelope["start"])
        except ValueError:
            start = None
    return OddsEvent(
        event_ext_id=ext_id,
        sport=sport,
        home_team=envelope.get("home_name") or "",
        away_team=envelope.get("away_name") or "",
        start=start,
    )


def _virtual_rows(ext_id: str, choices) -> list[OddsSnapshot]:
    return [
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


async def _write_matched(
    session, pick: ParsedPick, ext_id: str, choices, matched
) -> int:
    """Escribe (o re-sella) las filas de la opción casada. Devuelve n."""
    by_key = {(c.market_name, c.choice_name, c.choice_group): c for c in choices}
    key = (matched.market_name, matched.choice_name, matched.choice_group)
    choice = by_key[key]
    # No duplicar: si la opción ya tiene filas libres del mismo key,
    # se re-sellan al pick en vez de insertar.
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
        return len(existing)
    n = 0
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
        n += 1
    return n


async def retranslate(apply: bool) -> None:
    markets = oddspapi.load_markets()
    if markets is None:
        print("sin catálogo en disco (oddspapi_data/markets.json) — nada que hacer")
        return
    envelopes = _payload_ext_ids()
    marked_ids = {
        int(key.rsplit("|", 1)[1])
        for key in _missed()
        if key.startswith(_MARK) and key.rsplit("|", 1)[1].isdigit()
    }
    print(f"picks marcados {_MARK}* en provider_state: {len(marked_ids)}")
    print(f"payloads en disco: {len(envelopes)}")

    async with AsyncSessionLocal() as session:
        resolved_ids = set(
            (
                await session.exec(
                    select(OddsSnapshot.parsed_pick_id).where(
                        OddsSnapshot.parsed_pick_id != None  # noqa: E711
                    )
                )
            ).all()
        )
        registered = set((await session.exec(select(OddsEvent.event_ext_id))).all())

        # Fase 1 — picks marcados nomarket con ext_id y payload guardado.
        picks = (
            await session.exec(
                select(ParsedPick).where(ParsedPick.id.in_(marked_ids or {0}))
            )
        ).all()
        resueltos = sin_payload = sin_opcion = sin_evento = filas = 0
        done_pick_ids: set[int] = set()
        for pick in picks:
            ext_id = pick.odds_event_id
            if not ext_id:
                sin_evento += 1
                continue
            envelope = envelopes.get(ext_id)
            if envelope is None:
                sin_payload += 1
                print(f"  ? pick {pick.id}: {ext_id} sin payload guardado")
                continue
            event = _event_from_envelope(ext_id, envelope, pick.deporte or "futbol")
            choices = oddspapi.translate_history(
                envelope["payload"], markets, event.home_team, event.away_team
            )
            matched = map_pick_choices(pick, event, _virtual_rows(ext_id, choices))
            if matched is None:
                sin_opcion += 1
                continue
            resueltos += 1
            done_pick_ids.add(pick.id)
            print(
                f"  + pick {pick.id}: {matched.market_name} / "
                f"{matched.choice_name} ({matched.choice_group}) "
                f"[{envelope['home_name']} vs {envelope['away_name']}]"
            )
            if apply:
                await _register_event(session, event, registered)
                filas += await _write_matched(session, pick, ext_id, choices, matched)
                clear_missed(f"{_MARK}{pick.id}")
        print(
            f"fase 1 (marcados): resueltos {resueltos} · sin opción {sin_opcion} · "
            f"sin payload {sin_payload} · sin ext_id {sin_evento}"
        )

        # Fase 2 — reencuentro offline: picks pendientes (sin filas)
        # cuyo fixture SÍ llegó a disco — p. ej. los que mapearon en una
        # pasada cuyo commit abortó y perdieron filas y enlace. Mismo
        # criterio que _run_source: match por nombres en ventana ancha.
        sport_by_id = {v: k for k, v in oddspapi.SPORT_IDS.items()}
        resueltos2 = candidatos2 = filas2 = 0
        for ext_id, envelope in envelopes.items():
            start = None
            if envelope.get("start"):
                try:
                    start = datetime.fromisoformat(envelope["start"])
                except ValueError:
                    start = None
            sport = sport_by_id.get(envelope.get("sport_id"))
            if start is None or sport is None:
                continue
            candidates = (
                await session.exec(
                    select(ParsedPick)
                    .where(ParsedPick.fecha_evento != None)  # noqa: E711
                    .where(
                        ParsedPick.fecha_evento >= start - oddspapi._WIDE_DATE_TOLERANCE
                    )
                    .where(
                        ParsedPick.fecha_evento <= start + oddspapi._WIDE_DATE_TOLERANCE
                    )
                )
            ).all()
            choices = None
            for pick in candidates:
                if (
                    pick.id in resolved_ids
                    or pick.id in done_pick_ids
                    or _canonical_sport(pick.deporte) != sport
                ):
                    continue
                hint = _lookup_hint(pick, sport) or ""
                score = match_score(
                    hint,
                    envelope.get("home_name") or "",
                    envelope.get("away_name") or "",
                )
                if score < oddspapi._WIDE_MIN_SCORE:
                    continue
                candidatos2 += 1
                if choices is None:
                    choices = oddspapi.translate_history(
                        envelope["payload"],
                        markets,
                        envelope.get("home_name") or "",
                        envelope.get("away_name") or "",
                    )
                event = _event_from_envelope(ext_id, envelope, sport)
                matched = map_pick_choices(pick, event, _virtual_rows(ext_id, choices))
                if matched is None:
                    print(
                        f"  ? pick {pick.id}: casa fixture pero sin opción "
                        f"({pick.mercado} / {pick.seleccion}) [{ext_id}]"
                    )
                    continue
                resueltos2 += 1
                done_pick_ids.add(pick.id)
                print(
                    f"  + pick {pick.id}: {matched.market_name} / "
                    f"{matched.choice_name} ({matched.choice_group}) "
                    f"[{envelope.get('home_name')} vs {envelope.get('away_name')}] "
                    f"score {score:.2f}"
                )
                if apply:
                    await _register_event(session, event, registered)
                    if not pick.odds_event_id:
                        pick.odds_event_id = ext_id
                        session.add(pick)
                    filas2 += await _write_matched(
                        session, pick, ext_id, choices, matched
                    )
                    clear_missed(f"{_MARK}{pick.id}")
                    clear_missed(f"{oddspapi.NAME}|nofixture|{pick.id}")
        print(
            f"fase 2 (reencuentro): candidatos {candidatos2} · "
            f"resueltos {resueltos2} · filas {filas2}"
        )

        if apply:
            await session.commit()
        print(
            f"\nresueltos totales: {resueltos + resueltos2} · "
            f"filas {'insertadas' if apply else 'a insertar'}: {filas + filas2}"
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
