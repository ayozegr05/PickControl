"""Backfill de cuotas históricas de mercado vía OddsPapi (bet36528).

Los snapshots en vivo (`odds_snapshots`) solo existen desde que arrancó
el snapshotter (~19-sep-2026): los picks anteriores no tienen cuota de
mercado y la comparación tipster-vs-mercado queda vacía para todo el
histórico. OddsPapi devuelve la curva completa de cada opción (cada
movimiento de precio con su timestamp), que se guarda como filas de
`odds_snapshots` con `provider="oddspapi"` y `captured_at` real — así
`compare.py` obtiene la cuota exacta del mercado al publicar el pick,
la apertura y el cierre, sin cambiar nada del comparador.

Flujo por pick (fútbol/tenis, evento ya pasado):

1. Resuelve el fixture: `sofascoreId` del `odds_event_id` si existe;
   si no, por nombres + fecha (±1 día) contra /fixtures del día.
2. Una llamada /historical-odds por fixture (la curva alimenta todos
   los picks del evento — dedup por evento como el snapshotter).
3. Traduce mercados al vocabulario Sofascore y reutiliza
   `map_pick_choices` para localizar la opción del pick — misma
   lógica que la comparación real, sin duplicar mapeos.
4. Inserta SOLO las opciones casadas (la auditoría es por pick; los
   mercados no mapeables — props, 1ª parte — no se guardan).

Reglas de seguridad:

- Idempotente: un evento que ya tiene filas `provider="oddspapi"` se
  salta entero. Fixtures sin historial (404 — ITF/UTR/exhibiciones
  que ninguna casa cubre) quedan marcados como miss definitivo.
- No se inventan valores: un pick sin opción casada solo informa,
  nunca escribe.
- `pick.odds_event_id` vacío se rellena con el fixture casado
  ("sofascore:N" si el fixture lo trae, si no "oddspapi:<fixtureId>")
  y se registra en `odds_events` — igual que hace el snapshotter.

    .\\.venv\\Scripts\\python.exe scripts\\backfill_historical_odds.py            # dry-run
    .\\.venv\\Scripts\\python.exe scripts\\backfill_historical_odds.py --apply    # escribe
    .\\.venv\\Scripts\\python.exe scripts\\backfill_historical_odds.py --limit 20 # prueba acotada
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401  (FKs de TelegramRawMessage)
from app.services.odds.compare import map_pick_choices
from app.services.odds.oddspapi import (
    NAME,
    SPORT_IDS,
    OddsPapiClient,
    OddsPapiFixture,
    downsample,
    event_ext_id,
    match_fixture,
    translate_history,
)
from app.services.odds.snapshotter import _canonical_sport, _lookup_hint
from app.services.results.base import is_missed, mark_missed

# La histórico del pick solo existe si el evento ya pasó (con margen
# para no pillar partidos en vivo a mitad de curva).
_EVENT_MARGIN = timedelta(hours=3)
_MISS_KEY = "oddspapi|hist|{}"
# Pequeña pausa entre fixtures: el histórico es ilimitado pero cada
# respuesta pesa 1-3 MB — cortesía y margen de rate-limit.
_FETCH_DELAY = 0.2


@dataclass
class _Report:
    picks: int = 0
    con_fixture: int = 0
    sin_fixture: int = 0
    sin_historial: int = 0
    mapeados: int = 0
    sin_opcion: int = 0
    filas: int = 0
    detalles: list[str] = field(default_factory=list)


def _sofascore_id(pick: ParsedPick) -> int | None:
    if pick.odds_event_id and pick.odds_event_id.startswith("sofascore:"):
        try:
            return int(pick.odds_event_id.split(":", 1)[1])
        except ValueError:
            return None
    return None


def _nearest_price(points, when) -> float | None:
    if not points or when is None:
        return None
    return min(points, key=lambda p: abs((p.captured_at - when).total_seconds())).cuota


async def _load_picks(session, limit: int | None) -> list[ParsedPick]:
    result = await session.execute(
        select(ParsedPick)
        .where(ParsedPick.es_apuesta == True)  # noqa: E712
        .where(ParsedPick.es_combinada == False)  # noqa: E712
        .where(ParsedPick.deporte != None)  # noqa: E711
        .where(ParsedPick.fecha_evento != None)  # noqa: E711
        .where(ParsedPick.fecha_evento <= utc_now() - _EVENT_MARGIN)
        .order_by(ParsedPick.fecha_evento)
    )
    picks = [
        p for p in result.scalars().all() if _canonical_sport(p.deporte) in SPORT_IDS
    ]
    return picks[:limit] if limit else picks


async def run(apply: bool, limit: int | None) -> _Report:
    settings = get_settings()
    report = _Report()
    if not settings.rapidapi_tennis_key:
        report.detalles.append("Sin RAPIDAPI_TENNIS_KEY configurada.")
        return report

    client = OddsPapiClient(
        api_key=settings.rapidapi_tennis_key,
        api_host=settings.rapidapi_oddspapi_host,
    )
    async with AsyncSessionLocal() as session, httpx.AsyncClient(timeout=60) as http:
        markets = await client.markets(http)
        if markets is None:
            report.detalles.append("No se pudo descargar el catálogo /markets.")
            return report

        picks = await _load_picks(session, limit)
        report.picks = len(picks)

        # Eventos ya procesados por este backfill (idempotencia).
        done = set(
            (
                await session.execute(
                    select(OddsSnapshot.event_ext_id)
                    .where(OddsSnapshot.provider == NAME)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        registered = set(
            (await session.execute(select(OddsEvent.event_ext_id))).scalars().all()
        )

        # Resolución pick -> fixture agrupada por (deporte, día): una
        # llamada /fixtures por cubo cubre todos sus picks.
        buckets: dict[tuple[int, object], list[ParsedPick]] = {}
        for pick in picks:
            sport = _canonical_sport(pick.deporte)
            if pick.odds_event_id in done:
                continue
            buckets.setdefault((SPORT_IDS[sport], pick.fecha_evento.date()), []).append(
                pick
            )

        fixtures_picks: dict[str, tuple[OddsPapiFixture, str, list[ParsedPick]]] = {}
        for (sport_id, day), day_picks in sorted(
            buckets.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            sport = _canonical_sport(day_picks[0].deporte)
            # Un día por llamada: /fixtures capa la respuesta (~330
            # eventos) y una ventana de 3 días perdería partidos. La
            # caché del cliente dedup los días compartidos entre
            # buckets vecinos.
            fixtures: list = []
            api_error = False
            for delta in (-1, 0, 1):
                day_target = day + timedelta(days=delta)
                day_fixtures = await client.fixtures(
                    http, sport_id, day_target, day_target
                )
                if day_fixtures is None:
                    api_error = True
                    break
                fixtures += day_fixtures
            if api_error:
                report.detalles.append(
                    f"  ! error de API en fixtures {sport} {day}: {len(day_picks)} picks sin revisar"
                )
                continue
            for pick in day_picks:
                hint = _lookup_hint(pick, sport) or ""
                fixture = match_fixture(
                    fixtures, hint, pick.fecha_evento, _sofascore_id(pick)
                )
                if fixture is None:
                    report.sin_fixture += 1
                    report.detalles.append(
                        f"  - pick {pick.id}: sin fixture ({pick.evento or pick.seleccion})"
                    )
                    continue
                ext_id = event_ext_id(fixture)
                if ext_id in done:
                    continue
                slot = fixtures_picks.setdefault(
                    fixture.fixture_id, (fixture, sport, [])
                )
                slot[2].append(pick)
                report.con_fixture += 1

        # Una curva por fixture alimenta todos sus picks.
        for fixture, sport, event_picks in fixtures_picks.values():
            ext_id = event_ext_id(fixture)
            if is_missed(_MISS_KEY.format(fixture.fixture_id)):
                report.sin_historial += len(event_picks)
                continue
            await asyncio.sleep(_FETCH_DELAY)
            status, payload = await client.historical_odds(http, fixture.fixture_id)
            if status == 404:
                # El fixture nunca tuvo mercados — definitivo.
                mark_missed(_MISS_KEY.format(fixture.fixture_id))
                report.sin_historial += len(event_picks)
                report.detalles.append(
                    f"  - {fixture.home_name} vs {fixture.away_name}: sin historial"
                )
                continue
            if payload is None:
                # Error/cuota: no se marca — reintenta en otra pasada.
                report.detalles.append(
                    f"  ! {fixture.home_name} vs {fixture.away_name}: "
                    f"error de API ({status}), se reintenta luego"
                )
                break

            choices = translate_history(
                payload, markets, fixture.home_name, fixture.away_name
            )
            # Filas virtuales con el formato exacto que lee el comparador.
            virtual = [
                OddsSnapshot(
                    provider=NAME,
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
            event = OddsEvent(
                event_ext_id=ext_id,
                sport=sport,
                home_team=fixture.home_name,
                away_team=fixture.away_name,
                start=fixture.start,
            )
            by_key = {
                (c.market_name, c.choice_name, c.choice_group): c for c in choices
            }

            matched_keys: set[tuple] = set()
            trigger_pick = event_picks[0]
            for pick in event_picks:
                matched = map_pick_choices(pick, event, virtual)
                if matched is None:
                    report.sin_opcion += 1
                    report.detalles.append(
                        f"  - pick {pick.id}: sin opción comparable "
                        f"({pick.mercado} / {pick.seleccion})"
                    )
                    continue
                key = (matched.market_name, matched.choice_name, matched.choice_group)
                matched_keys.add(key)
                report.mapeados += 1
                choice = by_key[key]
                precio_pub = _nearest_price(choice.points, pick.created_at)
                report.detalles.append(
                    f"  + pick {pick.id}: {matched.market_name} / {matched.choice_name}"
                    f" | tipster {pick.cuota} vs mercado {precio_pub}"
                    f" | {len(choice.points)} puntos"
                )
                if not pick.odds_event_id:
                    pick.odds_event_id = ext_id
                    if apply:
                        session.add(pick)

            if apply and matched_keys:
                if ext_id not in registered:
                    session.add(event)
                    registered.add(ext_id)
                for key in matched_keys:
                    for point in downsample(by_key[key].points):
                        session.add(
                            OddsSnapshot(
                                provider=NAME,
                                event_ext_id=ext_id,
                                market_name=key[0],
                                choice_name=key[1],
                                choice_group=key[2],
                                cuota=point.cuota,
                                cuota_apertura=by_key[key].cuota_apertura,
                                suspended=point.suspended,
                                captured_at=point.captured_at,
                                parsed_pick_id=trigger_pick.id,
                            )
                        )
                        report.filas += 1
            elif matched_keys:
                report.filas += sum(
                    len(downsample(by_key[k].points)) for k in matched_keys
                )

        if apply:
            await session.commit()

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="escribe en BD")
    parser.add_argument("--limit", type=int, default=None, help="acota nº de picks")
    args = parser.parse_args()

    report = asyncio.run(run(apply=args.apply, limit=args.limit))
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode} backfill cuotas históricas ===")
    print(f"picks revisados: {report.picks}")
    print(f"con fixture:     {report.con_fixture}")
    print(f"sin fixture:     {report.sin_fixture}")
    print(f"sin historial:   {report.sin_historial}")
    print(f"mapeados:        {report.mapeados}")
    print(f"sin opción:      {report.sin_opcion}")
    print(f"filas {'insertadas' if args.apply else 'a insertar'}: {report.filas}")
    for line in report.detalles[:80]:
        print(line)
    if len(report.detalles) > 80:
        print(f"  ... y {len(report.detalles) - 80} líneas más")
    if not args.apply:
        print("\n(dry-run: nada escrito — repite con --apply para persistir)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
