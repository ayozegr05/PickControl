"""Tests de `TheOddsApiProvider` (odds/theoddsapi.py) y del mapeo de
props en `map_pick_choices` (odds/compare.py).

Sin red: `httpx.AsyncClient` se sustituye por un fake de rutas —
`/events` gratis devuelve listados, `/events/{id}/odds` devuelve
bookmakers con outcomes prop/alternos.
"""

from datetime import timedelta

import httpx
import pytest

from app.core.dates import utc_now
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds import theoddsapi
from app.services.odds.compare import map_pick_choices
from app.services.odds.theoddsapi import (
    TheOddsApiProvider,
    pick_needs_theoddsapi,
)
from app.services.results.base import is_rate_limited


class _Resp:
    def __init__(self, payload, status: int = 200, remaining: str = "490"):
        self._payload = payload
        self.status_code = status
        self.headers = {"x-requests-remaining": remaining}
        self.request = httpx.Request("GET", "https://x.test/")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=self.request, response=self)


class _FakeClient:
    """AsyncClient falso: responde por subcadena de URL."""

    routes: dict = {}
    calls: list = []
    remaining = "490"

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        _FakeClient.calls.append(url)
        for pat, payload in _FakeClient.routes.items():
            if pat in url:
                return _Resp(payload, remaining=_FakeClient.remaining)
        return _Resp([])


def _patch_client(monkeypatch, routes: dict, remaining: str = "490"):
    _FakeClient.routes = routes
    _FakeClient.calls = []
    _FakeClient.remaining = remaining
    monkeypatch.setattr(theoddsapi.httpx, "AsyncClient", _FakeClient)


def _provider() -> TheOddsApiProvider:
    return TheOddsApiProvider(api_key="test", regions="uk,eu", markets="m1,m2")


_FUTURE = utc_now() + timedelta(days=2)

_EVENTS_PAYLOAD = [
    {
        "id": "abc123",
        "home_team": "Ipswich Town",
        "away_team": "Liverpool",
        "commence_time": _FUTURE.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
]

_ODDS_PAYLOAD = {
    "bookmakers": [
        {
            "key": "paddypower",
            "markets": [
                {
                    "key": "player_goal_scorer_anytime",
                    "outcomes": [
                        {"name": "Yes", "description": "Alexander Isak", "price": 2.4},
                        {"name": "Yes", "description": "Cody Gakpo", "price": 3.7},
                    ],
                }
            ],
        },
        {
            "key": "williamhill",
            "markets": [
                {
                    "key": "player_goal_scorer_anytime",
                    "outcomes": [
                        {"name": "Yes", "description": "Alexander Isak", "price": 2.6},
                    ],
                },
                {
                    "key": "alternate_totals_corners",
                    "outcomes": [
                        {"name": "Over", "point": 9.5, "price": 1.58},
                        {"name": "Under", "point": 9.5, "price": 2.2},
                        {"name": "Over", "point": 10.5, "price": 1.92},
                    ],
                },
            ],
        },
    ]
}


@pytest.mark.asyncio
async def test_find_event_casa_por_equipos(monkeypatch):
    """El listado gratis de ligas casa el evento por home/away."""
    _patch_client(monkeypatch, {"/soccer_epl/events?": _EVENTS_PAYLOAD})
    provider = _provider()

    ref = await provider.find_event("futbol", _FUTURE, "Ipswich - Liverpool")

    assert ref is not None
    assert ref.event_ext_id == "theoddsapi:soccer_epl:abc123"
    assert ref.home_team == "Ipswich Town"


@pytest.mark.asyncio
async def test_find_event_pasado_sin_llamadas(monkeypatch):
    """Un evento ya jugado no aparece en el listado — ni se intenta
    (14 llamadas gratis pero inútiles por pick viejo)."""
    _patch_client(monkeypatch, {"/soccer_epl/events?": _EVENTS_PAYLOAD})
    provider = _provider()

    ref = await provider.find_event(
        "futbol", utc_now() - timedelta(days=7), "Ipswich - Liverpool"
    )

    assert ref is None
    assert _FakeClient.calls == []


@pytest.mark.asyncio
async def test_fetch_odds_traduce_props_y_alternas(monkeypatch):
    """Props: jugador en `description` -> choice_name. Alternas:
    `point` -> choice_group con el vocabulario O/U de Sofascore."""
    _patch_client(monkeypatch, {"/odds?": _ODDS_PAYLOAD})
    provider = _provider()

    choices = await provider.fetch_odds("theoddsapi:soccer_epl:abc123", "futbol")

    assert choices is not None
    by_key = {(c.market_name, c.choice_name, c.choice_group): c for c in choices}
    # Mejor precio entre bookies (2.6 > 2.4).
    assert by_key[("Goalscorer anytime", "Alexander Isak", None)].cuota == 2.6
    # La escalera alterna queda como "Corners 2-Way" + línea.
    assert by_key[("Corners 2-Way", "Over", "9.5")].cuota == 1.58
    assert by_key[("Corners 2-Way", "Under", "9.5")].cuota == 2.2
    assert by_key[("Corners 2-Way", "Over", "10.5")].cuota == 1.92


@pytest.mark.asyncio
async def test_cuota_agotada_aparca_el_mes(monkeypatch):
    """x-requests-remaining: 0 -> cooldown hasta el día 1 siguiente."""
    _patch_client(monkeypatch, {"/odds?": _ODDS_PAYLOAD}, remaining="0")
    provider = _provider()

    assert await provider.fetch_odds("theoddsapi:soccer_epl:x", "futbol") is None
    assert is_rate_limited("theoddsapi")


def test_gate_pick_needs_theoddsapi():
    prop = ParsedPick(
        id=1,
        es_apuesta=True,
        deporte="fútbol",
        mercado="jugador",
        seleccion="Isak: Marca gol",
        fecha_evento=_FUTURE,
    )
    corners = ParsedPick(
        id=2,
        es_apuesta=True,
        deporte="fútbol",
        mercado="over/under",
        seleccion="Menos de 9,5 córners",
        fecha_evento=_FUTURE,
    )
    winner = ParsedPick(
        id=3,
        es_apuesta=True,
        deporte="fútbol",
        mercado="ganador",
        seleccion="Betis gana",
        fecha_evento=_FUTURE,
    )
    assert pick_needs_theoddsapi(prop)
    assert pick_needs_theoddsapi(corners)
    assert not pick_needs_theoddsapi(winner)


def _event() -> OddsEvent:
    return OddsEvent(
        event_ext_id="espn:evt1",
        sport="futbol",
        home_team="Ipswich Town",
        away_team="Liverpool",
        start=_FUTURE,
    )


def _snap(market: str, choice: str, group=None, cuota=2.0) -> OddsSnapshot:
    return OddsSnapshot(
        provider="theoddsapi",
        event_ext_id="espn:evt1",
        market_name=market,
        choice_name=choice,
        choice_group=group,
        cuota=cuota,
        captured_at=utc_now(),
    )


def test_map_prop_goleador_casa_jugador():
    """'Isak: Marca gol' -> "Goalscorer anytime" / "Alexander Isak"."""
    pick = ParsedPick(
        id=1,
        es_apuesta=True,
        deporte="fútbol",
        mercado="jugador",
        seleccion="Alexander Isak: Marca gol",
        cuota=2.5,
        fecha_evento=_FUTURE,
    )
    rows = [
        _snap("Goalscorer anytime", "Alexander Isak", cuota=2.6),
        _snap("Goalscorer anytime", "Cody Gakpo", cuota=3.7),
        _snap("Full time", "2", cuota=1.4),
    ]
    matched = map_pick_choices(pick, _event(), rows)
    assert matched is not None
    assert matched.market_name == "Goalscorer anytime"
    assert matched.choice_name == "Alexander Isak"


def test_map_prop_marca_o_asiste():
    pick = ParsedPick(
        id=1,
        es_apuesta=True,
        deporte="fútbol",
        mercado="jugador",
        seleccion="Cody Gakpo: Jugador que Marca o Asiste",
        fecha_evento=_FUTURE,
    )
    rows = [
        _snap("Goalscorer anytime", "Cody Gakpo", cuota=3.7),
        _snap("Goalscorer or assist", "Cody Gakpo", cuota=1.9),
    ]
    matched = map_pick_choices(pick, _event(), rows)
    assert matched is not None
    assert matched.market_name == "Goalscorer or assist"


def test_map_prop_sin_mercado_es_miss_honesto():
    """Tarjetas de jugador no existen en la fuente -> None."""
    pick = ParsedPick(
        id=1,
        es_apuesta=True,
        deporte="fútbol",
        mercado="jugador",
        seleccion="Enciso: Recibirá una tarjeta",
        fecha_evento=_FUTURE,
    )
    rows = [_snap("Goalscorer anytime", "Julio Enciso", cuota=5.0)]
    assert map_pick_choices(pick, _event(), rows) is None


def test_map_prop_asistencias_linea_over():
    """'Gakpo asiste' -> "Player assists" Over 0.5 (nunca Under)."""
    pick = ParsedPick(
        id=1,
        es_apuesta=True,
        deporte="fútbol",
        mercado="jugador",
        seleccion="Gakpo da asistencia",
        fecha_evento=_FUTURE,
    )
    rows = [
        _snap("Player assists", "Cody Gakpo", "Over 0.5", cuota=1.8),
        _snap("Player assists", "Cody Gakpo", "Under 0.5", cuota=2.0),
    ]
    matched = map_pick_choices(pick, _event(), rows)
    assert matched is not None
    assert matched.choice_group == "Over 0.5"


def test_prop_no_roba_pick_ganador():
    """Un pick de equipo normal nunca cae en la rama de props."""
    pick = ParsedPick(
        id=1,
        es_apuesta=True,
        deporte="fútbol",
        mercado="ganador",
        seleccion="Liverpool gana",
        fecha_evento=_FUTURE,
    )
    rows = [
        _snap("Goalscorer anytime", "Alexander Isak", cuota=2.6),
        _snap("Full time", "2", cuota=1.4),
    ]
    matched = map_pick_choices(pick, _event(), rows)
    assert matched is not None
    assert matched.market_name == "Full time"
