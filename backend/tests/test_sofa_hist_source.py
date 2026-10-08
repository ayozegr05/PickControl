"""Tests de `_SofaScoreHistSource` (odds/historical_backfill.py).

La fuente casa picks contra el listado `scheduled-events` del día y
descarga `/api/v1/event/{id}/odds/1/all` por la cadena de transportes
Sofascore. Los tests usan transportes/providers falsos — sin red.
"""

from datetime import date, datetime, timedelta

import pytest

from app.services.odds.base import MarketChoice
from app.services.odds.historical_backfill import (
    _SofaFixture,
    _SofaScoreHistSource,
)

pytestmark = pytest.mark.asyncio


class _ListingTransport:
    """Transporte de listado falso: dict (sport_slug, iso-day) -> respuesta."""

    name = "fake-listing"

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    async def get_json(self, path: str):
        self.calls.append(path)
        return self.responses.get(path)


class _OddsProvider:
    """Provider de odds falso: cola de respuestas (None = error)."""

    def __init__(self, responses: list):
        self._responses = list(responses)

    async def fetch_odds(self, event_ext_id: str, sport: str):
        return self._responses.pop(0) if self._responses else None


def _source(listing=None, odds=None) -> _SofaScoreHistSource:
    src = _SofaScoreHistSource.__new__(_SofaScoreHistSource)
    src._listing = listing or []
    src._odds = odds or []
    src._days = {}
    return src


def _event(eid: int, home: str, away: str, day: str = "2026-10-06") -> dict:
    return {
        "id": eid,
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "startTimestamp": int(datetime(2026, 10, 6, 18, 0).timestamp()),
    }


async def test_fixtures_parsea_listado_y_cachea():
    t = _ListingTransport(
        {
            "/api/v1/sport/football/scheduled-events/2026-10-06": {
                "events": [
                    _event(1, "Sweden", "Poland"),
                    {"id": 2, "homeTeam": {}, "awayTeam": {"name": "X"}},  # incompleto
                ]
            }
        }
    )
    src = _source(listing=[t])
    out = await src.fixtures(None, 1, date(2026, 10, 6))
    assert len(out) == 1
    assert out[0].ext_id == "sofascore:1"
    assert out[0].home_name == "Sweden"
    # segunda llamada: caché, no re-fetchea
    await src.fixtures(None, 1, date(2026, 10, 6))
    assert len(t.calls) == 1


async def test_fixtures_error_si_todos_los_transportes_fallan():
    t = _ListingTransport({})  # path desconocido -> None
    src = _source(listing=[t])
    assert await src.fixtures(None, 1, date(2026, 10, 6)) is None


async def test_fixtures_dia_vacio_legitimo():
    t = _ListingTransport(
        {"/api/v1/sport/tennis/scheduled-events/2026-10-06": {"events": []}}
    )
    src = _source(listing=[t])
    out = await src.fixtures(None, 2, date(2026, 10, 6))
    assert out == []


async def test_match_por_nombre_y_por_sofascore_id():
    fixtures = [
        _SofaFixture(10, "Sweden", "Poland", datetime(2026, 10, 6, 18, 0)),
        _SofaFixture(11, "Croatia", "Spain", datetime(2026, 10, 6, 20, 0)),
    ]
    src = _source()
    m = src.match(fixtures, "Suecia - Polonia", None, None)
    assert m is not None and m.event_id == 10
    # sofascore_id directo gana aunque el hint no case
    m2 = src.match(fixtures, "basura irreconocible", None, 11)
    assert m2 is not None and m2.event_id == 11
    assert src.match(fixtures, "Real Madrid - Barça", None, None) is None


async def test_choices_ok_con_curva_apertura_cierre():
    rows = [
        MarketChoice(
            market_name="Full time",
            choice_name="1",
            cuota=2.5,
            cuota_apertura=3.0,
            choice_group=None,
        ),
        MarketChoice(
            market_name="Corners 2-Way",
            choice_name="Over",
            cuota=1.8,
            cuota_apertura=None,
            choice_group="9.5",
        ),
    ]
    src = _source(odds=[_OddsProvider([rows])])
    fix = _SofaFixture(42, "Sweden", "Poland", datetime(2026, 10, 6, 18, 0))
    status, choices = await src.choices(None, fix, "futbol", [])
    assert status == "ok"
    assert len(choices) == 2
    ft = choices[0]
    assert ft.market_name == "Full time" and ft.cuota_apertura == 3.0
    assert len(ft.points) == 2
    assert ft.points[0].cuota == 3.0  # apertura anclada a inicio-7d
    assert ft.points[0].captured_at == fix.start - timedelta(days=7)
    assert ft.points[1].captured_at == fix.start
    corners = choices[1]
    assert corners.choice_group == "9.5" and len(corners.points) == 1


async def test_choices_miss_si_evento_sin_mercados():
    src = _source(odds=[_OddsProvider([[]])])
    fix = _SofaFixture(7, "A", "B", datetime(2026, 10, 6))
    assert (await src.choices(None, fix, "futbol", [])) == ("miss", [])


async def test_choices_error_si_todos_fallan():
    src = _source(odds=[_OddsProvider([None]), _OddsProvider([None])])
    fix = _SofaFixture(7, "A", "B", datetime(2026, 10, 6))
    assert (await src.choices(None, fix, "futbol", [])) == ("error", [])


async def test_choices_segundo_provider_rescata_si_primero_vacio():
    src = _source(
        odds=[
            _OddsProvider([[]]),  # allsportsapi2 sin mercados
            _OddsProvider(
                [
                    [
                        MarketChoice(
                            market_name="Full time",
                            choice_name="1",
                            cuota=1.9,
                            cuota_apertura=None,
                            choice_group=None,
                        )
                    ]
                ]
            ),
        ]
    )
    fix = _SofaFixture(7, "A", "B", datetime(2026, 10, 6))
    status, choices = await src.choices(None, fix, "futbol", [])
    assert status == "ok" and len(choices) == 1
