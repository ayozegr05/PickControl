"""Tests de `_SofaScoreHistSource` (odds/historical_backfill.py).

La fuente casa picks contra el listado `scheduled-events` del día y
descarga `/api/v1/event/{id}/odds/1/all` por la cadena de transportes
Sofascore. Los tests usan transportes/providers falsos — sin red.
"""

from datetime import date, datetime, timedelta

import pytest

from app.services.odds.base import MarketChoice
from app.services.odds.historical_backfill import (
    _SEARCH_FAILED,
    _SofaFixture,
    _SofaScoreHistSource,
    _SofaSearchSpace,
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


def _source(listing=None, odds=None, search_providers=None) -> _SofaScoreHistSource:
    src = _SofaScoreHistSource.__new__(_SofaScoreHistSource)
    src._listing = listing or []
    src._odds = odds or []
    src._days = {}
    src._search_providers = search_providers or []
    return src


class _SearchProvider:
    """Provider de búsqueda falso: cola de respuestas de
    `/api/search` (None = error de API transitorio)."""

    NAME = "allsportsapi2"
    SUPPORTED_SPORTS = frozenset({"futbol", "tenis"})

    def __init__(self, responses: list, name: str = "allsportsapi2"):
        self._responses = list(responses)
        self.NAME = name
        self.queries: list[str] = []

    async def _search(self, client, sport: str, name: str):
        self.queries.append(f"{sport}:{name}")
        return self._responses.pop(0) if self._responses else None


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


# --- Modo búsqueda (plan B: listado vetado -> allsportsapi2) ----------


def _search_result(eid: int, home: str, away: str, ts=None) -> dict:
    return {
        "type": "event",
        "entity": {
            "id": eid,
            "name": f"{home} - {away}",
            "homeTeam": {"name": home},
            "awayTeam": {"name": away},
            "startTimestamp": (
                ts if ts is not None else int(datetime(2026, 10, 6, 18, 0).timestamp())
            ),
        },
    }


async def test_fixtures_search_space_si_listado_vetado():
    t = _ListingTransport({})  # todo None: Cloudflare vetó la IP
    provider = _SearchProvider([])
    src = _source(listing=[t], search_providers=[provider])
    out = await src.fixtures(None, 1, date(2026, 10, 6))
    assert isinstance(out, _SofaSearchSpace)
    assert out.sport == "futbol" and out.providers == [provider]


async def test_fixtures_sin_search_provider_sigue_dando_none():
    t = _ListingTransport({})
    src = _source(listing=[t])  # sin allsportsapi2
    assert await src.fixtures(None, 1, date(2026, 10, 6)) is None


async def test_search_match_resuelve_evento_por_fecha(monkeypatch):
    monkeypatch.setattr(
        "app.services.odds.historical_backfill.is_rate_limited", lambda name: False
    )
    provider = _SearchProvider(
        [
            [
                _search_result(1, "Sweden", "Poland"),  # 6 oct (el del pick)
                _search_result(
                    2,
                    "Sweden",
                    "Poland",
                    ts=int(datetime(2026, 3, 31, 18, 0).timestamp()),  # otra edición
                ),
                {"type": "team", "entity": {"id": 9, "name": "Sweden"}},
            ]
        ]
    )
    space = _SofaSearchSpace(sport="futbol", providers=[provider], http=None)
    src = _source(search_providers=[provider])
    coro = src.match(space, "Suecia - Polonia", datetime(2026, 10, 6, 20, 0), None)
    fx = await coro
    assert fx is not None and fx.event_id == 1
    assert provider.queries == ["futbol:sweden poland"]


async def test_search_match_none_si_nada_en_fecha(monkeypatch):
    monkeypatch.setattr(
        "app.services.odds.historical_backfill.is_rate_limited", lambda name: False
    )
    provider = _SearchProvider(
        [
            [
                _search_result(
                    2,
                    "Sweden",
                    "Poland",
                    ts=int(datetime(2026, 3, 31, 18, 0).timestamp()),
                )
            ]
        ]
    )
    space = _SofaSearchSpace(sport="futbol", providers=[provider], http=None)
    src = _source(search_providers=[provider])
    coro = src.match(space, "Suecia - Polonia", datetime(2026, 10, 6, 20, 0), None)
    assert await coro is None


async def test_search_match_error_api_es_transitorio(monkeypatch):
    monkeypatch.setattr(
        "app.services.odds.historical_backfill.is_rate_limited", lambda name: False
    )
    provider = _SearchProvider([None])  # fallo de API
    space = _SofaSearchSpace(sport="futbol", providers=[provider], http=None)
    src = _source(search_providers=[provider])
    coro = src.match(space, "Suecia - Polonia", datetime(2026, 10, 6, 20, 0), None)
    assert await coro is _SEARCH_FAILED


async def test_search_match_atajo_sofascore_id_sin_buscar():
    provider = _SearchProvider([])  # no debe llamarse
    space = _SofaSearchSpace(sport="futbol", providers=[provider], http=None)
    src = _source(search_providers=[provider])
    coro = src.match(space, "Suecia - Polonia", datetime(2026, 10, 6, 20, 0), 42)
    fx = await coro
    assert fx is not None and fx.event_id == 42 and fx.ext_id == "sofascore:42"
    assert provider.queries == []


async def test_search_match_cadena_providers_primero_falla_segundo_rescata(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.odds.historical_backfill.is_rate_limited", lambda name: False
    )
    caido = _SearchProvider([None], name="footapi7")  # error de API
    vivo = _SearchProvider([[_search_result(7, "Sweden", "Poland")]])
    space = _SofaSearchSpace(sport="futbol", providers=[caido, vivo], http=None)
    src = _source(search_providers=[caido, vivo])
    coro = src.match(space, "Suecia - Polonia", datetime(2026, 10, 6, 20, 0), None)
    fx = await coro
    assert fx is not None and fx.event_id == 7
    assert caido.queries and vivo.queries


async def test_search_match_todos_aparcados_es_transitorio(monkeypatch):
    monkeypatch.setattr(
        "app.services.odds.historical_backfill.is_rate_limited", lambda name: True
    )
    provider = _SearchProvider([])
    space = _SofaSearchSpace(sport="futbol", providers=[provider], http=None)
    src = _source(search_providers=[provider])
    coro = src.match(space, "Suecia - Polonia", datetime(2026, 10, 6, 20, 0), None)
    assert await coro is _SEARCH_FAILED
    assert provider.queries == []
