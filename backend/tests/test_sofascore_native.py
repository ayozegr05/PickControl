"""Tests de `sofascore_native.py` (API nativa /api/v1 de Sofascore).

Los transportes se sustituyen por fakes — no se toca red ni los JSON
de estado/caché reales.
"""

from datetime import datetime, timezone

import pytest

import app.services.results.sofascore_native as native
from app.services.results.sofascore_native import (
    SofaScoreNativeResultsProvider,
    _DirectTransport,
    _RapidApiTransport,
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
