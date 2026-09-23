"""Tests de `scores365.py` (webservice público de 365scores).

La red se corta en `_get_json` (único punto de HTTP); `provider_state`
y la caché persistente se neutralizan para no tocar los JSON reales.
"""

from datetime import datetime

import pytest

import app.services.results.scores365 as scores365
from app.services.results.scores365 import Scores365Provider

_GAME_TENNIS = {
    "id": 1,
    "statusText": "Ended",
    "startTime": "2026-09-20T11:00:00+02:00",
    "homeCompetitor": {"name": "Lukas Neumayer", "score": 2.0, "isWinner": True},
    "awayCompetitor": {"name": "Juan Manzano", "score": 1.0, "isWinner": False},
    "stages": [
        {"name": "Set 1", "homeCompetitorScore": 6.0, "awayCompetitorScore": 4.0},
        {"name": "Set 2", "homeCompetitorScore": 3.0, "awayCompetitorScore": 6.0},
        {"name": "Set 3", "homeCompetitorScore": 6.0, "awayCompetitorScore": 2.0},
    ],
}

_GAME_DOUBLES = {
    "id": 2,
    "statusText": "Ended",
    "startTime": "2026-09-20T14:00:00+02:00",
    "homeCompetitor": {
        "name": "Garay V./Pagani D.",
        "score": 0.0,
        "isWinner": False,
    },
    "awayCompetitor": {
        "name": "Arias B./Huertas Del Pino Cordova A.",
        "score": 2.0,
        "isWinner": True,
    },
}

_GAME_WALKOVER = {
    "id": 3,
    "statusText": "WalkOver",
    "startTime": "2026-09-20T16:00:00+02:00",
    "homeCompetitor": {"name": "Iñaki Montes", "score": 0.0},
    "awayCompetitor": {"name": "Rival Cualquiera", "score": 0.0},
}

_GAME_FOOTBALL = {
    "id": 4,
    "statusText": "Ended",
    "startTime": "2026-09-20T21:00:00+02:00",
    "homeCompetitor": {"name": "Hamilton", "score": 1.0, "isWinner": True},
    "awayCompetitor": {"name": "Cowdenbeath", "score": 0.0, "isWinner": False},
}

_GAME_BASKET = {
    "id": 5,
    "statusText": "Final",
    "startTime": "2026-09-20T19:00:00+02:00",
    "homeCompetitor": {"name": "Panathinaikos (W)", "score": 68.0},
    "awayCompetitor": {"name": "Emlak Konut (W)", "score": 91.0},
}

_GAME_CANCELLED = {
    "id": 6,
    "statusText": "Cancelled",
    "startTime": "2026-09-20T18:00:00+02:00",
    "homeCompetitor": {"name": "Cristian Garin", "score": None},
    "awayCompetitor": {"name": "Daniel Merida", "score": None},
}

_GAME_LIVE = {
    "id": 7,
    "statusText": "Set 2",
    "startTime": "2026-09-20T18:30:00+02:00",
    "homeCompetitor": {"name": "Live Player A", "score": 1.0},
    "awayCompetitor": {"name": "Live Player B", "score": 0.0},
}


@pytest.fixture
def tennis_provider(monkeypatch):
    """Provider de tenis con HTTP/estado/caché neutralizados."""
    monkeypatch.setattr(scores365, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(scores365, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(scores365, "mark_missed", lambda key: None)
    monkeypatch.setattr(scores365, "mark_rate_limited", lambda name: None)
    monkeypatch.setattr(scores365, "get_event_list", lambda key: None)
    monkeypatch.setattr(scores365, "set_event_list", lambda key, events: None)
    prov = Scores365Provider("tenis")
    return prov


def _feed(*games):
    return {"games": list(games)}


class TestFindMatchTenis:
    async def test_resultado_con_sets(self, tennis_provider, monkeypatch):
        async def fake_get(client, url):
            return _feed(_GAME_TENNIS, _GAME_DOUBLES)

        monkeypatch.setattr(tennis_provider, "_get_json", fake_get)
        match = await tennis_provider.find_match(
            datetime(2026, 9, 20, 12, 0), "Neumayer vs Manzano"
        )
        assert match is not None
        assert match.home_team == "Lukas Neumayer"
        assert (match.home_score, match.away_score) == (2, 1)
        assert match.sets == [(6, 4), (3, 6), (6, 2)]

    async def test_dobles_casa_por_pareja(self, tennis_provider, monkeypatch):
        async def fake_get(client, url):
            return _feed(_GAME_TENNIS, _GAME_DOUBLES)

        monkeypatch.setattr(tennis_provider, "_get_json", fake_get)
        match = await tennis_provider.find_match(
            datetime(2026, 9, 20, 12, 0), "Arias / Huertas del Pino"
        )
        assert match is not None
        assert "Arias" in match.away_team
        assert (match.home_score, match.away_score) == (0, 2)

    async def test_walkover_marca_status(self, tennis_provider, monkeypatch):
        async def fake_get(client, url):
            return _feed(_GAME_WALKOVER)

        monkeypatch.setattr(tennis_provider, "_get_json", fake_get)
        match = await tennis_provider.find_match(
            datetime(2026, 9, 20, 12, 0), "Iñaki Montes"
        )
        assert match is not None
        assert match.status == "walkover"

    async def test_partido_en_vivo_no_es_miss(self, tennis_provider, monkeypatch):
        """Un partido en juego no debe marcarse missed: se reintenta."""
        marked = []
        monkeypatch.setattr(scores365, "mark_missed", marked.append)

        async def fake_get(client, url):
            return _feed(_GAME_LIVE)

        monkeypatch.setattr(tennis_provider, "_get_json", fake_get)
        match = await tennis_provider.find_match(
            datetime(2026, 9, 20, 12, 0), "Live Player A vs Live Player B"
        )
        assert match is None
        assert marked == []

    async def test_miss_marca_missed(self, tennis_provider, monkeypatch):
        marked = []
        monkeypatch.setattr(scores365, "mark_missed", marked.append)

        async def fake_get(client, url):
            return _feed(_GAME_TENNIS)

        monkeypatch.setattr(tennis_provider, "_get_json", fake_get)
        match = await tennis_provider.find_match(
            datetime(2026, 9, 20, 12, 0), "Jugador Inexistente"
        )
        assert match is None
        assert len(marked) == 1


class TestFindMatchOtrosDeportes:
    async def test_futbol(self, monkeypatch):
        monkeypatch.setattr(scores365, "is_rate_limited", lambda name: False)
        monkeypatch.setattr(scores365, "is_missed", lambda key, ttl=None: False)
        monkeypatch.setattr(scores365, "get_event_list", lambda key: None)
        monkeypatch.setattr(scores365, "set_event_list", lambda k, e: None)
        prov = Scores365Provider("futbol")

        async def fake_get(client, url):
            return _feed(_GAME_FOOTBALL)

        monkeypatch.setattr(prov, "_get_json", fake_get)
        match = await prov.find_match(
            datetime(2026, 9, 20, 12, 0), "Hamilton - Cowdenbeath"
        )
        assert match is not None
        assert (match.home_score, match.away_score) == (1, 0)
        assert match.sets is None

    async def test_basket(self, monkeypatch):
        monkeypatch.setattr(scores365, "is_rate_limited", lambda name: False)
        monkeypatch.setattr(scores365, "is_missed", lambda key, ttl=None: False)
        monkeypatch.setattr(scores365, "get_event_list", lambda key: None)
        monkeypatch.setattr(scores365, "set_event_list", lambda k, e: None)
        prov = Scores365Provider("baloncesto")

        async def fake_get(client, url):
            return _feed(_GAME_BASKET)

        monkeypatch.setattr(prov, "_get_json", fake_get)
        match = await prov.find_match(
            datetime(2026, 9, 20, 12, 0), "Panathinaikos - Emlak Konut"
        )
        assert match is not None
        assert (match.home_score, match.away_score) == (68, 91)


class TestFindPostponed:
    async def test_cancelado_en_feed_current(self, tennis_provider, monkeypatch):
        async def fake_get(client, url):
            # El feed current lleva cancelados/programados/en vivo.
            return _feed(_GAME_CANCELLED, _GAME_LIVE)

        monkeypatch.setattr(tennis_provider, "_get_json", fake_get)
        state = await tennis_provider.find_postponed_match(
            datetime(2026, 9, 20, 12, 0), "Garin vs Merida"
        )
        assert state is not None
        assert state.status == "cancelled"
        assert state.away_team == "Daniel Merida"

    async def test_cancelado_otro_partido_no_casa(self, tennis_provider, monkeypatch):
        async def fake_get(client, url):
            return _feed(_GAME_CANCELLED)

        monkeypatch.setattr(tennis_provider, "_get_json", fake_get)
        state = await tennis_provider.find_postponed_match(
            datetime(2026, 9, 20, 12, 0), "Sinner vs Alcaraz"
        )
        assert state is None
