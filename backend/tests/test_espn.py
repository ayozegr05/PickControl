"""Tests de `espn.py` (scoreboard JSON interno de ESPN).

La red se corta en `_scoreboard` (único punto de HTTP); `provider_state`
se neutraliza para no tocar el JSON real ni depender de la fecha actual.
"""

from datetime import datetime

import pytest

import app.services.results.espn as espn
from app.services.results.espn import EspnProvider

_EVENT = {
    "id": "700001",
    "competitions": [
        {
            "status": {"type": {"name": "STATUS_FULL_TIME", "completed": True}},
            "competitors": [
                {
                    "homeAway": "home",
                    "score": "2",
                    "team": {"id": "10", "displayName": "Elche"},
                    "linescores": [{"value": 1.0}],
                    "statistics": [
                        {"name": "wonCorners", "displayValue": "3"},
                        {"name": "foulsCommitted", "displayValue": "10"},
                        {"name": "shotsOnTarget", "displayValue": "2"},
                        {"name": "totalShots", "displayValue": "8"},
                    ],
                },
                {
                    "homeAway": "away",
                    "score": "1",
                    "team": {"id": "20", "displayName": "Real Madrid"},
                    "linescores": [{"value": 0.0}],
                    "statistics": [
                        {"name": "wonCorners", "displayValue": "6"},
                        {"name": "foulsCommitted", "displayValue": "14"},
                        {"name": "shotsOnTarget", "displayValue": "5"},
                        {"name": "totalShots", "displayValue": "15"},
                    ],
                },
            ],
            "details": [
                {
                    "type": {"text": "Goal"},
                    "team": {"id": "20"},
                    "athletesInvolved": [{"displayName": "Mbappé"}],
                },
                {
                    "type": {"text": "Yellow Card"},
                    "team": {"id": "10"},
                    "athletesInvolved": [{"displayName": "Defensa Elche"}],
                },
                {
                    "type": {"text": "Red Card"},
                    "team": {"id": "20"},
                    "athletesInvolved": [{"displayName": "Roja Madrid"}],
                },
                {
                    # Doble amarilla: cuenta como amarilla y roja a la vez.
                    "type": {"text": "Yellow - Red Card"},
                    "team": {"id": "10"},
                    "athletesInvolved": [{"displayName": "Expulsado Elche"}],
                },
            ],
        }
    ],
}

_BOARD = {"events": [_EVENT]}

_DATE = datetime(2026, 9, 15, 18, 0)


@pytest.fixture
def provider(monkeypatch):
    """Provider con HTTP y estado de cuota/misses neutralizados."""
    monkeypatch.setattr(espn, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(espn, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(espn, "mark_missed", lambda key: None)
    monkeypatch.setattr(espn, "mark_rate_limited", lambda name: None)
    monkeypatch.setattr(espn, "count_provider_call", lambda name: None)

    prov = EspnProvider()

    async def fake_scoreboard(client, league, day):
        # Solo esp.1 tiene datos el día del partido; el resto vacío.
        if league == "esp.1" and day == "20260915":
            return _BOARD
        return {"events": []}

    monkeypatch.setattr(prov, "_scoreboard", fake_scoreboard)
    return prov


class TestFindMatch:
    async def test_marcador_final_con_descanso(self, provider):
        match = await provider.find_match(_DATE, "Elche - Real Madrid")
        assert match is not None
        assert match.home_score == 2
        assert match.away_score == 1
        assert match.ht_home_score == 1
        assert match.ht_away_score == 0

    async def test_partido_no_terminado_devuelve_none(self, provider, monkeypatch):
        board = {
            "events": [
                {
                    "id": "x",
                    "competitions": [
                        {
                            "status": {
                                "type": {
                                    "name": "STATUS_IN_PROGRESS",
                                    "completed": False,
                                }
                            },
                            "competitors": _EVENT["competitions"][0]["competitors"],
                        }
                    ],
                }
            ]
        }

        async def fake(client, league, day):
            return board if league == "esp.1" else {"events": []}

        prov = EspnProvider()
        monkeypatch.setattr(prov, "_scoreboard", fake)
        assert await prov.find_match(_DATE, "Elche - Real Madrid") is None

    async def test_equipo_no_encontrado(self, provider):
        assert await provider.find_match(_DATE, "Inventado FC - Nadie") is None


class TestFindMatchStats:
    async def test_stats_canonicas(self, provider):
        stats = await provider.find_match_stats(_DATE, "Elche - Real Madrid")
        assert stats is not None
        assert stats.values["Corner Kicks"] == (3, 6)
        assert stats.values["Fouls"] == (10, 14)
        assert stats.values["Total Shots"] == (8, 15)
        assert stats.values["Shots on Goal"] == (2, 5)

    async def test_tarjetas_desde_details(self, provider):
        stats = await provider.find_match_stats(_DATE, "Elche - Real Madrid")
        assert stats is not None
        # Elche: amarilla + doble amarilla; Madrid: roja directa +
        # la roja de la doble amarilla de Elche va a home.
        assert stats.values["Yellow Cards"] == (2, 0)
        assert stats.values["Red Cards"] == (1, 1)


class TestFindPostponed:
    async def test_aplazado(self, provider, monkeypatch):
        board = {
            "events": [
                {
                    "id": "x",
                    "competitions": [
                        {
                            "status": {
                                "type": {
                                    "name": "STATUS_POSTPONED",
                                    "completed": False,
                                }
                            },
                            "competitors": _EVENT["competitions"][0]["competitors"],
                        }
                    ],
                }
            ]
        }

        async def fake(client, league, day):
            return board if league == "esp.1" else {"events": []}

        prov = EspnProvider()
        monkeypatch.setattr(prov, "_scoreboard", fake)
        state = await prov.find_postponed_match(_DATE, "Elche - Real Madrid")
        assert state is not None
        assert state.status == "postponed"
        assert state.home_team == "Elche"


class TestSaludApi:
    def test_fallos_consecutivos_aparcan_y_avisan(self, monkeypatch):
        """5 fallos seguidos -> mark_rate_limited (push a admins)."""
        calls = []
        monkeypatch.setattr(espn, "mark_rate_limited", calls.append)
        prov = EspnProvider()
        for _ in range(espn._MAX_CONSECUTIVE_FAILURES - 1):
            prov._register_failure("esp.1", RuntimeError("boom"))
        assert calls == []
        prov._register_failure("esp.1", RuntimeError("boom"))
        assert calls == ["espn"]

    def test_exito_resetea_el_contador(self, monkeypatch):
        calls = []
        monkeypatch.setattr(espn, "mark_rate_limited", calls.append)
        prov = EspnProvider()
        for _ in range(espn._MAX_CONSECUTIVE_FAILURES - 1):
            prov._register_failure("esp.1", RuntimeError("boom"))
        prov._consecutive_failures = 0  # lo que hace _scoreboard al tener éxito
        for _ in range(espn._MAX_CONSECUTIVE_FAILURES - 1):
            prov._register_failure("esp.1", RuntimeError("boom"))
        assert calls == []
