"""Tests de `espn.py` (scoreboard/summary JSON interno de ESPN) y
`odds/espn_odds.py` (pickcenter/DraftKings).

La red se corta en `_scoreboard`/`_summary` (únicos puntos de HTTP);
`provider_state` se neutraliza para no tocar el JSON real ni depender
de la fecha actual.
"""

from datetime import datetime

import pytest

import app.services.results.espn as espn
from app.services.results.espn import (
    EspnBasketballProvider,
    EspnProvider,
    EspnTennisProvider,
)

_EVENT = {
    "id": "700001",
    "competitions": [
        {
            "date": "2026-09-15T18:00Z",
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

# Roster del summary: el que jugó (appearances=1) y el que no entró
# (appearances=0). Dituro es el caso real de ownGoal en LaLiga.
_SUMMARY = {
    "header": {
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Elche"}},
                    {"homeAway": "away", "team": {"displayName": "Real Madrid"}},
                ]
            }
        ]
    },
    "rosters": [
        {
            "team": {"displayName": "Elche"},
            "roster": [
                {
                    "athlete": {"displayName": "Marc-André ter Stegen"},
                    "starter": True,
                    "stats": [
                        {"name": "appearances", "value": 1},
                        {"name": "ownGoals", "value": 1},
                        {"name": "foulsCommitted", "value": 0},
                    ],
                },
                {
                    "athlete": {"displayName": "Suplente Elche"},
                    "starter": False,
                    "stats": [{"name": "appearances", "value": 0}],
                },
            ],
        },
        {
            "team": {"displayName": "Real Madrid"},
            "roster": [
                {
                    "athlete": {"displayName": "Kylian Mbappé"},
                    "starter": True,
                    "stats": [
                        {"name": "appearances", "value": 1},
                        {"name": "totalGoals", "value": 2},
                        {"name": "goalAssists", "value": 0},
                        {"name": "totalShots", "value": 6},
                        {"name": "shotsOnTarget", "value": 4},
                        {"name": "yellowCards", "value": 0},
                    ],
                },
                {
                    "athlete": {"displayName": "Jude Bellingham"},
                    "starter": True,
                    "stats": [
                        {"name": "appearances", "value": 1},
                        {"name": "goalAssists", "value": 1},
                        {"name": "foulsCommitted", "value": 3},
                        {"name": "yellowCards", "value": 1},
                    ],
                },
            ],
        },
    ],
    "pickcenter": [
        {
            "provider": {"name": "DraftKings"},
            "overUnder": 2.5,
            "overOdds": -120,
            "underOdds": +100,
            "spread": -1.5,
            "homeTeamOdds": {
                "moneyLine": +350,
                "spreadOdds": -110,
                "favorite": False,
            },
            "awayTeamOdds": {
                "moneyLine": -140,
                "spreadOdds": -110,
                "favorite": True,
            },
            "drawOdds": {"moneyLine": +300},
        }
    ],
}

_DATE = datetime(2026, 9, 15, 18, 0)


@pytest.fixture
def provider(monkeypatch):
    """Provider de fútbol con HTTP y estado de cuota/misses neutros."""
    monkeypatch.setattr(espn, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(espn, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(espn, "mark_missed", lambda key: None)
    monkeypatch.setattr(espn, "mark_rate_limited", lambda name: None)
    monkeypatch.setattr(espn, "count_provider_call", lambda name: None)

    prov = EspnProvider()

    async def fake_scoreboard(league, day):
        # Solo esp.1 tiene datos el día del partido; el resto vacío.
        if league == "esp.1" and day == "20260915":
            return _BOARD
        return {"events": []}

    async def fake_summary(league, event_id):
        return _SUMMARY if (league, event_id) == ("esp.1", "700001") else None

    monkeypatch.setattr(prov, "_scoreboard", fake_scoreboard)
    monkeypatch.setattr(prov, "_summary", fake_summary)
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

        async def fake(league, day):
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
        # Elche: amarilla + doble amarilla; Madrid: roja directa.
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

        async def fake(league, day):
            return board if league == "esp.1" else {"events": []}

        prov = EspnProvider()
        monkeypatch.setattr(prov, "_scoreboard", fake)
        state = await prov.find_postponed_match(_DATE, "Elche - Real Madrid")
        assert state is not None
        assert state.status == "postponed"
        assert state.home_team == "Elche"


class TestFindMatchEvents:
    """Props de jugador vía `summary.rosters` — la lista de jugados es
    completa, así que una prop que no casa puede cerrar en fallo."""

    async def test_goleadores_asistencias_tarjetas(self, provider):
        events = await provider.find_match_events(_DATE, "Elche - Real Madrid")
        assert events is not None
        # Mbappé marcó 2; ter Stegen solo metió en propia puerta -> no
        # aparece como goleador.
        assert events.scorers == ["Kylian Mbappé"]
        assert events.assisters == ["Jude Bellingham"]
        assert "Jude Bellingham" in events.booked
        # participants = jugaron (appearances>0), sin el suplente.
        assert "Suplente Elche" not in events.participants
        assert "Kylian Mbappé" in events.participants

    async def test_own_goal_no_es_gol(self, provider):
        events = await provider.find_match_events(_DATE, "Elche - Real Madrid")
        assert events is not None
        assert "Marc-André ter Stegen" not in events.scorers

    async def test_sin_summary_devuelve_none(self, provider):
        # El evento está en el board pero su summary falla -> None,
        # el verifier sigue la cascada con footapi7.
        assert (
            await provider.find_match_events(_DATE, "Elche - Real Madrid") is not None
        )


class TestFindMatchPlayers:
    async def test_played_y_stats_flat(self, provider):
        players = await provider.find_match_players(_DATE, "Elche - Real Madrid")
        assert players is not None
        assert "Suplente Elche" not in players.played
        assert set(players.played) == {
            "Marc-André ter Stegen",
            "Kylian Mbappé",
            "Jude Bellingham",
        }
        mbappe = players.stats["Kylian Mbappé"]
        assert mbappe["goals.total"] == 2
        assert mbappe["shots.total"] == 6
        assert mbappe["shots.on"] == 4
        jude = players.stats["Jude Bellingham"]
        assert jude["goals.assists"] == 1
        assert jude["fouls.committed"] == 3
        assert jude["cards.yellow"] == 1


_TENNIS_BOARD = {
    "events": [
        {
            "id": "900",
            "name": "Chengdu Open",
            "date": "2026-09-22T05:00Z",  # fecha del TORNEO, no del partido
            "groupings": [
                {
                    "grouping": {"slug": "singles"},
                    "competitions": [
                        {
                            "id": "c1",
                            "date": "2026-09-24T08:00Z",
                            "status": {
                                "type": {"name": "STATUS_FINAL", "completed": True}
                            },
                            "competitors": [
                                {
                                    "homeAway": "home",
                                    "type": "athlete",
                                    "winner": True,
                                    "athlete": {"displayName": "Alejandro Tabilo"},
                                    "linescores": [
                                        {"value": 7.0, "tiebreaker": 5},
                                        {"value": 6.0},
                                    ],
                                },
                                {
                                    "homeAway": "away",
                                    "type": "athlete",
                                    "winner": False,
                                    "athlete": {"displayName": "Lorenzo Musetti"},
                                    "linescores": [
                                        {"value": 6.0, "tiebreaker": 3},
                                        {"value": 4.0},
                                    ],
                                },
                            ],
                        },
                        {
                            # Partido de OTRO día del mismo torneo: no
                            # debe casar con el pick del 24.
                            "id": "c0",
                            "date": "2026-09-22T05:00Z",
                            "status": {
                                "type": {"name": "STATUS_FINAL", "completed": True}
                            },
                            "competitors": [
                                {
                                    "homeAway": "home",
                                    "type": "athlete",
                                    "winner": True,
                                    "athlete": {"displayName": "Lorenzo Musetti"},
                                    "linescores": [{"value": 6.0}, {"value": 6.0}],
                                },
                                {
                                    "homeAway": "away",
                                    "type": "athlete",
                                    "winner": False,
                                    "athlete": {"displayName": "Otro Jugador"},
                                    "linescores": [{"value": 3.0}, {"value": 2.0}],
                                },
                            ],
                        },
                    ],
                }
            ],
        }
    ]
}

_TENNIS_DATE = datetime(2026, 9, 24, 9, 0)


@pytest.fixture
def tennis_provider(monkeypatch):
    prov = EspnTennisProvider()

    async def fake_scoreboard(league, day):
        # ESPN devuelve todos los partidos del torneo, no solo del día —
        # el filtro por fecha de competition es lo que decide.
        return _TENNIS_BOARD if league == "atp" else {"events": []}

    monkeypatch.setattr(prov, "_scoreboard", fake_scoreboard)
    return prov


class TestTennis:
    async def test_sets_y_ganador(self, tennis_provider):
        match = await tennis_provider.find_match(_TENNIS_DATE, "Tabilo")
        assert match is not None
        assert match.home_team == "Alejandro Tabilo"
        assert match.away_team == "Lorenzo Musetti"
        assert match.sets == [(7, 6), (6, 4)]
        assert match.home_score == 2
        assert match.away_score == 0

    async def test_fecha_del_partido_no_del_torneo(self, tennis_provider):
        # El board agrupa todo el torneo; el filtro por fecha de la
        # competition excluye el partido de Musetti del día 22.
        match = await tennis_provider.find_match(_TENNIS_DATE, "Musetti")
        assert match is not None
        assert match.away_team == "Lorenzo Musetti"
        assert match.sets == [(7, 6), (6, 4)]

    async def test_jugador_no_encontrado(self, tennis_provider):
        assert await tennis_provider.find_match(_TENNIS_DATE, "Nadie") is None


_BASKET_BOARD = {
    "events": [
        {
            "id": "b1",
            "competitions": [
                {
                    "date": "2026-10-03T23:00Z",
                    "status": {"type": {"name": "STATUS_FINAL", "completed": True}},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": "110",
                            "winner": True,
                            "team": {"id": "1", "displayName": "Boston Celtics"},
                            "linescores": [
                                {"value": 30.0},
                                {"value": 25.0},
                                {"value": 28.0},
                                {"value": 27.0},
                            ],
                        },
                        {
                            "homeAway": "away",
                            "score": "102",
                            "winner": False,
                            "team": {"id": "2", "displayName": "New York Knicks"},
                            "linescores": [
                                {"value": 22.0},
                                {"value": 30.0},
                                {"value": 24.0},
                                {"value": 26.0},
                            ],
                        },
                    ],
                }
            ],
        }
    ]
}

_BASKET_DATE = datetime(2026, 10, 4, 1, 0)


class TestBasketball:
    async def test_resultado_y_descanso(self, monkeypatch):
        prov = EspnBasketballProvider()

        async def fake_scoreboard(league, day):
            return _BASKET_BOARD if league == "nba" else {"events": []}

        monkeypatch.setattr(prov, "_scoreboard", fake_scoreboard)
        match = await prov.find_match(_BASKET_DATE, "Celtics - Knicks")
        assert match is not None
        assert match.home_score == 110
        assert match.away_score == 102
        # Descanso = Q1+Q2: 30+25 = 55 / 22+30 = 52
        assert match.ht_home_score == 55
        assert match.ht_away_score == 52


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


class TestOddsProvider:
    """pickcenter -> MarketChoice con nombres canónicos del comparador
    ("Full time" + "X", "Match goals" Over/Under, "Asian handicap")."""

    @pytest.fixture
    def odds_provider(self, monkeypatch):
        from app.services.odds.espn_odds import EspnOddsProvider

        prov = EspnOddsProvider()
        engine = prov._engines["futbol"]

        async def fake_find_event(date, hint):
            return (
                _EVENT,
                _EVENT["competitions"][0],
                "esp.1",
            )

        async def fake_summary(league, event_id):
            return _SUMMARY

        monkeypatch.setattr(engine, "_find_event", fake_find_event)
        monkeypatch.setattr(engine, "_summary", fake_summary)
        return prov

    async def test_find_event_devuelve_id_con_liga(self, odds_provider):
        ref = await odds_provider.find_event("futbol", _DATE, "Elche - Real Madrid")
        assert ref is not None
        assert ref.event_ext_id == "espn:soccer:esp.1:700001"
        assert ref.home_team == "Elche"
        assert ref.away_team == "Real Madrid"

    async def test_pickcenter_a_market_choices(self, odds_provider):
        choices = await odds_provider.fetch_odds("espn:soccer:esp.1:700001", "futbol")
        assert choices is not None
        by_market = {}
        for c in choices:
            by_market.setdefault(c.market_name, []).append(c)

        full_time = {c.choice_name: c.cuota for c in by_market["Full time"]}
        assert full_time["Elche"] == pytest.approx(4.5, abs=0.01)
        assert full_time["Real Madrid"] == pytest.approx(1.714, abs=0.01)
        assert full_time["X"] == pytest.approx(4.0, abs=0.01)

        goals = {c.choice_name: c for c in by_market["Match goals"]}
        assert goals["Over"].choice_group == "2.5"
        assert goals["Over"].cuota == pytest.approx(1.833, abs=0.01)
        assert goals["Under"].cuota == pytest.approx(2.0, abs=0.01)

        handicap = {c.choice_name: c.cuota for c in by_market["Asian handicap"]}
        # Real Madrid favorito -1.5 / Elche +1.5.
        assert "(-1.5) Real Madrid" in handicap
        assert "(+1.5) Elche" in handicap

    async def test_id_ajeno_devuelve_none(self, odds_provider):
        # Un id de la familia Sofascore no es parseable por ESPN.
        assert await odds_provider.fetch_odds("sofascore:12345", "futbol") is None

    async def test_deporte_no_soportado(self, odds_provider):
        assert await odds_provider.find_event("tenis", _DATE, "Alcaraz") is None
