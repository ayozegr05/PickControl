"""Tests de `nhl.py` (API pública oficial de la NHL).

La red se corta en `_day_games` (único punto de HTTP); el estado de
proveedor (`provider_state.json`) se neutraliza para no tocar disco.
"""

from datetime import date, datetime

import pytest

import app.services.results.nhl as nhl
from app.services.results.nhl import NhlProvider


def _team(place: str, name: str, score: int) -> dict:
    return {
        "placeName": {"default": place},
        "name": {"default": name},
        "abbrev": name[:3].upper(),
        "score": score,
    }


_GAME_FINISHED = {
    "id": 1,
    "gameState": "OFF",
    "startTimeUTC": "2025-10-30T23:00:00Z",
    "homeTeam": _team("Detroit", "Red Wings", 4),
    "awayTeam": _team("Chicago", "Blackhawks", 3),
}

_GAME_LIVE = {
    "id": 2,
    "gameState": "LIVE",
    "startTimeUTC": "2025-10-30T20:00:00Z",
    "homeTeam": _team("Boston", "Bruins", 1),
    "awayTeam": _team("Buffalo", "Sabres", 0),
}

_GAME_POSTPONED = {
    "id": 3,
    "gameState": "FUT",
    "gameScheduleState": "PPD",
    "startTimeUTC": "2025-10-30T18:00:00Z",
    "homeTeam": _team("Toronto", "Maple Leafs", 0),
    "awayTeam": _team("Montreal", "Canadiens", 0),
}


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(nhl, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(nhl, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(nhl, "mark_missed", lambda key: None)
    monkeypatch.setattr(nhl, "mark_rate_limited", lambda name: None)
    return NhlProvider()


def _patch_games(provider, monkeypatch, games):
    async def fake_day_games(client, day: date):
        return list(games)

    monkeypatch.setattr(provider, "_day_games", fake_day_games)


class TestFindMatch:
    async def test_resultado_final(self, provider, monkeypatch):
        _patch_games(provider, monkeypatch, [_GAME_FINISHED, _GAME_LIVE])
        match = await provider.find_match(
            datetime(2025, 10, 30, 12, 0), "Detroit Red Wings - Chicago Blackhawks"
        )
        assert match is not None
        assert match.home_team == "Detroit Red Wings"
        assert (match.home_score, match.away_score) == (4, 3)

    async def test_solo_apodo_tambien_casa(self, provider, monkeypatch):
        """El tipster escribe 'Red Wings' sin ciudad: subset de tokens."""
        _patch_games(provider, monkeypatch, [_GAME_FINISHED])
        match = await provider.find_match(
            datetime(2025, 10, 30, 12, 0), "Red Wings - Blackhawks"
        )
        assert match is not None
        assert "Red Wings" in match.home_team

    async def test_partido_en_vivo_no_es_miss(self, provider, monkeypatch):
        """El partido correcto aún en juego no se marca missed."""
        marked = []
        monkeypatch.setattr(nhl, "mark_missed", marked.append)
        _patch_games(provider, monkeypatch, [_GAME_LIVE])
        match = await provider.find_match(
            datetime(2025, 10, 30, 12, 0), "Boston Bruins - Buffalo Sabres"
        )
        assert match is None
        assert marked == []

    async def test_otro_partido_es_miss(self, provider, monkeypatch):
        marked = []
        monkeypatch.setattr(nhl, "mark_missed", marked.append)
        _patch_games(provider, monkeypatch, [_GAME_FINISHED])
        match = await provider.find_match(
            datetime(2025, 10, 30, 12, 0), "Spartak - Severstal"
        )
        assert match is None
        assert len(marked) == 1


class TestPostponed:
    async def test_ppd_devuelve_match_state(self, provider, monkeypatch):
        _patch_games(provider, monkeypatch, [_GAME_POSTPONED])
        state = await provider.find_postponed_match(
            datetime(2025, 10, 30, 12, 0), "Toronto - Montreal"
        )
        assert state is not None
        assert state.status == "postponed"
        assert "Maple Leafs" in state.home_team
