"""Tests de `sofascore_native.py` (API nativa /api/v1 de Sofascore).

Los transportes se sustituyen por fakes — no se toca red ni los JSON
de estado/caché reales.
"""

import asyncio
from datetime import datetime, timezone

import pytest

import app.services.results.sofascore_native as native
from app.services.results.sofascore_native import (
    SofaScoreNativeResultsProvider,
    _DirectTransport,
    _RapidApiTransport,
    _SofaScore6Transport,
)

_PLAYER_ID = 338890
_EVENT_TS = int(datetime(2026, 9, 20, 11, 0, tzinfo=timezone.utc).timestamp())

_SEARCH_MERIDA = {
    "results": [
        {
            "type": "team",
            "entity": {
                "id": _PLAYER_ID,
                "name": "Daniel Merida",
                "sport": {"slug": "tennis"},
            },
        }
    ]
}

_EVENT_CANCELLED = {
    "id": 17124652,
    "startTimestamp": _EVENT_TS,
    "status": {"type": "canceled"},
    "homeTeam": {"name": "Cristian Garin"},
    "awayTeam": {"name": "Daniel Merida"},
}

_EVENT_FINISHED = {
    "id": 2,
    "startTimestamp": _EVENT_TS,
    "status": {"type": "finished"},
    "homeTeam": {"name": "Lukas Neumayer"},
    "awayTeam": {"name": "Juan Manzano"},
    "homeScore": {"current": 2, "period1": 6, "period2": 3, "period3": 6},
    "awayScore": {"current": 1, "period1": 4, "period2": 6, "period3": 2},
}

_EVENT_FOOTBALL = {
    "id": 3,
    "startTimestamp": _EVENT_TS,
    "status": {"type": "finished"},
    "homeTeam": {"name": "Hamilton Academical"},
    "awayTeam": {"name": "Cowdenbeath"},
    "homeScore": {"current": 1},
    "awayScore": {"current": 0},
}


class _FakeTransport:
    """Transporte fake: mapa path -> respuesta (None = error)."""

    def __init__(self, name: str, responses: dict):
        self.name = name
        self._responses = responses

    async def get_json(self, path: str):
        return self._responses.get(path, {})


@pytest.fixture(autouse=True)
def _neutral_state(monkeypatch):
    monkeypatch.setattr(native, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(native, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(native, "mark_missed", lambda key: None)
    monkeypatch.setattr(native, "mark_rate_limited", lambda name: None)
    monkeypatch.setattr(native, "get_entity_id", lambda ns, name: None)
    monkeypatch.setattr(native, "set_entity_id", lambda ns, name, eid: None)
    monkeypatch.setattr(native, "get_event_list", lambda key: None)
    monkeypatch.setattr(native, "set_event_list", lambda key, events: None)
    monkeypatch.setattr(native, "event_list_covers", lambda entry, date: False)


def _tennis_provider(responses: dict) -> SofaScoreNativeResultsProvider:
    return SofaScoreNativeResultsProvider(
        _FakeTransport("test_transport", responses), "tenis"
    )


class TestFindMatch:
    async def test_resultado_tenis_con_sets(self):
        prov = _tennis_provider(
            {
                "/api/v1/search/all?q=neumayer": {
                    "results": [
                        {
                            "type": "team",
                            "entity": {
                                "id": _PLAYER_ID,
                                "name": "Lukas Neumayer",
                                "sport": {"slug": "tennis"},
                            },
                        }
                    ]
                },
                f"/api/v1/team/{_PLAYER_ID}/events/last/0": {
                    "events": [_EVENT_FINISHED]
                },
            }
        )
        match = await prov.find_match(
            datetime(2026, 9, 20, 12, 0), "Neumayer vs Manzano"
        )
        assert match is not None
        assert (match.home_score, match.away_score) == (2, 1)
        assert match.sets == [(6, 4), (3, 6), (6, 2)]

    async def test_futbol_mismo_transporte(self):
        prov = SofaScoreNativeResultsProvider(
            _FakeTransport(
                "test_transport",
                {
                    "/api/v1/search/all?q=hamilton": {
                        "results": [
                            {
                                "type": "team",
                                "entity": {
                                    "id": 99,
                                    "name": "Hamilton Academical",
                                    "sport": {"slug": "football"},
                                },
                            }
                        ]
                    },
                    "/api/v1/team/99/events/last/0": {"events": [_EVENT_FOOTBALL]},
                },
            ),
            "futbol",
        )
        match = await prov.find_match(
            datetime(2026, 9, 20, 12, 0), "Hamilton - Cowdenbeath"
        )
        assert match is not None
        assert (match.home_score, match.away_score) == (1, 0)

    async def test_entidad_de_otro_deporte_se_descarta(self):
        # La búsqueda devuelve un equipo de FÚTBOL llamado igual: el
        # provider de tenis debe ignorarlo (filtro por sport.slug).
        prov = _tennis_provider(
            {
                "/api/v1/search/all?q=merida": {
                    "results": [
                        {
                            "type": "team",
                            "entity": {
                                "id": 7,
                                "name": "Estudiantes de Merida",
                                "sport": {"slug": "football"},
                            },
                        }
                    ]
                },
            }
        )
        match = await prov.find_match(datetime(2026, 9, 20, 12, 0), "Merida")
        assert match is None


class TestFindPostponed:
    async def test_cancelado_es_match_state(self):
        prov = _tennis_provider(
            {
                "/api/v1/search/all?q=merida": _SEARCH_MERIDA,
                f"/api/v1/team/{_PLAYER_ID}/events/last/0": {
                    "events": [_EVENT_CANCELLED]
                },
            }
        )
        state = await prov.find_postponed_match(
            datetime(2026, 9, 20, 12, 0), "Garin vs Merida"
        )
        assert state is not None
        assert state.status == "cancelled"
        assert state.away_team == "Daniel Merida"


class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=None
            )


class TestDirectTransport:
    @staticmethod
    def _mock_session(monkeypatch, response):
        class _Session:
            def get(self, *a, **k):
                return response

        monkeypatch.setattr(_DirectTransport, "_session", _Session())

    async def test_404_es_lista_vacia(self, monkeypatch):
        self._mock_session(monkeypatch, _FakeResponse(404))
        transport = _DirectTransport()
        assert await transport.get_json("/api/v1/team/1/events/next/0") == {}

    async def test_403_marca_rate_limited(self, monkeypatch):
        marked = []
        monkeypatch.setattr(native, "mark_rate_limited", marked.append)
        self._mock_session(monkeypatch, _FakeResponse(403))
        transport = _DirectTransport()
        assert await transport.get_json("/api/v1/search/all?q=x") is None
        assert marked == ["sofascore_direct"]

    async def test_200_html_marca_rate_limited(self, monkeypatch):
        """Un 200 con HTML (desafío Cloudflare) se trata como bloqueo:
        no se insiste a través del challenge."""
        marked = []
        monkeypatch.setattr(native, "mark_rate_limited", marked.append)

        class _HtmlResponse(_FakeResponse):
            def json(self):
                raise ValueError("not json")

        self._mock_session(monkeypatch, _HtmlResponse(200))
        transport = _DirectTransport()
        assert await transport.get_json("/api/v1/search/all?q=x") is None
        assert marked == ["sofascore_direct"]

    async def test_llamadas_concurrentes_identicas_se_deduplican(self, monkeypatch):
        """Dos picks pidiendo la misma ruta a la vez comparten UNA
        petición — no se duplica la llamada ante Cloudflare."""
        calls = []

        class _Session:
            def get(self, *a, **k):
                calls.append(a)

                import time as _t

                _t.sleep(0.05)  # ventana para que llegue el duplicado
                return _FakeResponse(200, {"ok": 1})

        monkeypatch.setattr(_DirectTransport, "_session", _Session())
        monkeypatch.setattr(_DirectTransport, "_inflight", {})
        transport = _DirectTransport()
        a, b = await asyncio.gather(
            transport.get_json("/api/v1/search/all?q=x"),
            transport.get_json("/api/v1/search/all?q=x"),
        )
        assert a == {"ok": 1} and b == {"ok": 1}
        assert len(calls) == 1

    async def test_cada_respuesta_cuenta_en_snapshot(self, monkeypatch):
        """La métrica diaria cuenta TODAS las respuestas (incluidos
        403/HTML) — lo que Cloudflare registra en su edge."""
        from app.services.results import base as results_base

        self._mock_session(monkeypatch, _FakeResponse(200, {"ok": 1}))
        transport = _DirectTransport()
        await transport.get_json("/api/v1/search/all?q=x")
        snap = results_base.providers_snapshot()
        assert snap["calls_today"]["sofascore_direct"] == 1

    def test_contador_agrega_por_provider_y_dia(self):
        from app.services.results import base as results_base

        results_base.count_provider_call("sofascore_direct")
        results_base.count_provider_call("sofascore_direct")
        results_base.count_provider_call("sportapi7")
        snap = results_base.providers_snapshot()
        assert snap["calls_today"]["sofascore_direct"] == 2
        assert snap["calls_today"]["sportapi7"] == 1


class TestRapidApiTransport:
    async def test_404_es_lista_vacia(self, monkeypatch):
        class _Client:
            async def get(self, url, headers=None):
                return _FakeResponse(404)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(native.httpx, "AsyncClient", lambda **k: _Client())
        transport = _RapidApiTransport("sportapi7", "sportapi7.p.rapidapi.com", "k")
        assert await transport.get_json("/api/v1/team/1/events/next/0") == {}


class TestSofaScore6Transport:
    """El transporte traduce rutas nativas /api/v1 al esquema propio
    de sofascore6 y reenvuelve las respuestas al formato nativo."""

    def test_map_path_search(self):
        t = _SofaScore6Transport("h", "k")
        assert t._map_path("/api/v1/search/all?q=merida") == (
            "/api/sofascore/v1/search/all?q=merida"
        )

    def test_map_path_events(self):
        t = _SofaScore6Transport("h", "k")
        assert t._map_path("/api/v1/team/338890/events/last/1") == (
            "/api/sofascore/v1/team/matches/finished?team_id=338890&page=1"
        )
        assert t._map_path("/api/v1/team/338890/events/next/0") == (
            "/api/sofascore/v1/team/matches/upcoming?team_id=338890&page=0"
        )

    def test_map_path_odds(self):
        t = _SofaScore6Transport("h", "k")
        assert t._map_path("/api/v1/event/17124652/odds/1/all") == (
            "/api/sofascore/v1/match/odds?match_id=17124652"
        )

    def test_map_path_desconocida(self):
        t = _SofaScore6Transport("h", "k")
        assert t._map_path("/api/v1/otra/cosa") is None

    def test_normalize_search_lista(self):
        data = _SofaScore6Transport._normalize(
            "/api/v1/search/all?q=x", [{"entity": {"id": 1}}]
        )
        assert data == {"results": [{"entity": {"id": 1}}]}

    def test_normalize_matches_renombra_timestamp(self):
        data = _SofaScore6Transport._normalize(
            "/api/v1/team/1/events/last/0",
            {"matches": [{"id": 5, "timestamp": _EVENT_TS}]},
        )
        assert data == {
            "events": [{"id": 5, "timestamp": _EVENT_TS, "startTimestamp": _EVENT_TS}]
        }

    def test_normalize_odds_a_markets(self):
        data = _SofaScore6Transport._normalize(
            "/api/v1/event/1/odds/1/all",
            [
                {
                    "name": "Full time",
                    "choiceGroup": "Home/Away",
                    "isLive": False,
                    "suspended": False,
                    "choices": [
                        {
                            "name": "1",
                            "value": {"decimal": 3.25},
                            "initialValue": {"decimal": 3.10},
                        }
                    ],
                }
            ],
        )
        market = data["markets"][0]
        assert market["marketName"] == "Full time"
        assert market["choices"][0]["fractionalValue"] == 3.25
        assert market["choices"][0]["initialFractionalValue"] == 3.10

    async def test_get_json_end_to_end(self, monkeypatch):
        """Ruta nativa -> mapped -> respuesta normalizada."""
        captured = {}

        class _Client:
            async def get(self, url, headers=None):
                captured["url"] = url
                return _FakeResponse(200, [{"entity": {"id": 1}}])

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(native.httpx, "AsyncClient", lambda **k: _Client())
        t = _SofaScore6Transport("sofascore6.p.rapidapi.com", "k")
        data = await t.get_json("/api/v1/search/all?q=merida")
        assert captured["url"].endswith("/api/sofascore/v1/search/all?q=merida")
        assert data == {"results": [{"entity": {"id": 1}}]}
