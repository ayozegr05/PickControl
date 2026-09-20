"""Tests de `footapi_stats.py` (footapi7 / Sofascore vía RapidAPI).

La red se corta en `_get_json` (único punto de HTTP); `provider_state`
se neutraliza para no tocar el JSON real ni depender de la fecha actual.
"""

from datetime import datetime, timezone

import pytest

import app.services.results.footapi_stats as footapi
from app.models.parsed_pick import ParsedPick
from app.services.results.footapi_stats import FootApiStatsProvider
from app.services.results.verifier import verify_pick

_TEAM_ID = 2833
_EVENT_ID = 16416342
_EVENT_TS = int(datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc).timestamp())

_SEARCH = {
    "results": [
        {
            "entity": {
                "id": _TEAM_ID,
                "name": "Elche",
                "sport": {"slug": "football"},
            }
        },
        {
            "entity": {
                "id": 999,
                "name": "Elche (basket)",
                "sport": {"slug": "basketball"},
            }
        },
    ]
}

_PREVIOUS = {
    "events": [
        {
            "id": _EVENT_ID,
            "startTimestamp": _EVENT_TS,
            "status": {"type": "finished"},
            "homeTeam": {"name": "Elche"},
            "awayTeam": {"name": "Real Madrid"},
            "homeScore": {"current": 2},
            "awayScore": {"current": 3},
        },
        {
            "id": 111,
            "startTimestamp": _EVENT_TS - 86400 * 10,
            "status": {"type": "finished"},
            "homeTeam": {"name": "Elche"},
            "awayTeam": {"name": "Getafe"},
            "homeScore": {"current": 1},
            "awayScore": {"current": 0},
        },
    ]
}

_STATS = {
    "statistics": [
        {
            "period": "ALL",
            "groups": [
                {
                    "groupName": "Match overview",
                    "statisticsItems": [
                        {"name": "Corner kicks", "home": "2", "away": "5"},
                        {"name": "Yellow cards", "home": "4", "away": "2"},
                        {"name": "Ball possession", "home": "56%", "away": "44%"},
                        {
                            "name": "Long balls",
                            "home": "23/37 (62%)",
                            "away": "25/37 (68%)",
                        },
                    ],
                },
                {
                    "groupName": "Shots",
                    "statisticsItems": [
                        {"name": "Total shots", "home": "11", "away": "20"},
                        {"name": "Shots on target", "home": "3", "away": "4"},
                    ],
                },
            ],
        },
        {
            "period": "1ST",
            "groups": [
                {
                    "groupName": "Match overview",
                    "statisticsItems": [
                        {"name": "Corner kicks", "home": "1", "away": "2"},
                    ],
                }
            ],
        },
    ]
}

_INCIDENTS = {
    "incidents": [
        {
            "text": "FT",
            "homeScore": 2,
            "awayScore": 3,
            "time": 90,
            "incidentType": "period",
        },
        {
            "player": {"name": "Alexander Isak", "id": 1},
            "assist1": {"name": "Cody Gakpo", "id": 2},
            "teamSide": "away",
            "homeScore": 0,
            "awayScore": 1,
            "time": 12,
            "incidentType": "goal",
            "incidentClass": "regular",
        },
        {
            # Propia puerta: el jugador aparece pero NO es goleador.
            "player": {"name": "Defensor Raro", "id": 3},
            "teamSide": "home",
            "homeScore": 0,
            "awayScore": 2,
            "time": 30,
            "incidentType": "goal",
            "incidentClass": "ownGoal",
        },
        {
            "player": {"name": "Alexis Mac Allister", "id": 4},
            "teamSide": "away",
            "time": 55,
            "incidentType": "card",
            "incidentClass": "yellow",
        },
        {
            "playerIn": {"name": "Suplente Uno", "id": 13},
            "playerOut": {"name": "Titular Dos", "id": 14},
            "time": 70,
            "incidentType": "substitution",
        },
    ]
}

_LINEUPS = {
    "confirmed": True,
    "home": {
        "players": [
            {
                "player": {"name": "Ante Budimir", "id": 10},
                "substitute": False,
                "statistics": {
                    "minutesPlayed": 88,
                    "shotsOnTarget": 2,
                    "shotsOffTarget": 1,
                    "blockedScoringAttempt": 1,
                    "goals": 1,
                    "fouls": 2,
                    "yellowCards": 0,
                },
            },
            {
                "player": {"name": "Defensor Raro", "id": 3},
                "substitute": False,
                "statistics": {"minutesPlayed": 90, "yellowCards": 0},
            },
        ],
        "formation": "4-4-2",
    },
    "away": {
        "players": [
            {
                "player": {"name": "Alexander Isak", "id": 1},
                "substitute": False,
                "statistics": {
                    "minutesPlayed": 90,
                    "goals": 1,
                    "shotsOnTarget": 1,
                },
            },
            {
                "player": {"name": "Cody Gakpo", "id": 2},
                "substitute": False,
                "statistics": {"minutesPlayed": 75, "goalAssist": 1},
            },
            {
                # Convocado pero no entró: minutesPlayed = 0.
                "player": {"name": "Convocado Sin Jugar", "id": 12},
                "substitute": True,
                "statistics": {"minutesPlayed": 0},
            },
            {
                "player": {"name": "Suplente Uno", "id": 13},
                "substitute": True,
                "statistics": {"minutesPlayed": 15},
            },
        ],
        "formation": "4-3-3",
    },
}


@pytest.fixture
def provider(monkeypatch):
    """Provider con HTTP y estado de cuota/misses neutralizados."""
    monkeypatch.setattr(footapi, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(footapi, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(footapi, "mark_missed", lambda key: None)
    monkeypatch.setattr(footapi, "mark_rate_limited", lambda name: None)

    prov = FootApiStatsProvider("key", "footapi7.p.rapidapi.com")

    responses = {
        "/api/search/elche": _SEARCH,
        f"/api/team/{_TEAM_ID}/matches/previous/0": _PREVIOUS,
        f"/api/match/{_EVENT_ID}/statistics": _STATS,
        f"/api/match/{_EVENT_ID}/incidents": _INCIDENTS,
        f"/api/match/{_EVENT_ID}/lineups": _LINEUPS,
    }

    async def fake_get(client, path):
        return responses.get(path, {})

    monkeypatch.setattr(prov, "_get_json", fake_get)
    return prov


class TestFindMatch:
    async def test_marcador_final(self, provider):
        match = await provider.find_match(
            datetime(2026, 9, 15, 18, 0), "Elche - Real Madrid"
        )
        assert match is not None
        assert match.home_score == 2
        assert match.away_score == 3
        assert match.home_team == "Elche"

    async def test_partido_no_terminado_devuelve_none(self, provider, monkeypatch):
        event = dict(_PREVIOUS["events"][0])
        event["status"] = {"type": "inprogress"}
        prov2 = FootApiStatsProvider("k", "h")

        async def fake_get(client, path):
            if "previous" in path:
                return {"events": [event]}
            if "search" in path:
                return _SEARCH
            return {}

        monkeypatch.setattr(prov2, "_get_json", fake_get)
        match = await prov2.find_match(
            datetime(2026, 9, 15, 18, 0), "Elche - Real Madrid"
        )
        assert match is None

    async def test_equipo_no_encontrado(self, provider):
        match = await provider.find_match(
            datetime(2026, 9, 15, 18, 0), "Xinavarro FC - Nadadeporte"
        )
        assert match is None


class TestFindMatchStats:
    async def test_stats_normalizadas_a_claves_canonicas(self, provider):
        stats = await provider.find_match_stats(
            datetime(2026, 9, 15, 18, 0), "Elche - Real Madrid"
        )
        assert stats is not None
        # Solo periodo ALL, solo las claves mapeadas; % y "23/37" fuera.
        assert stats.values["Corner Kicks"] == (2, 5)
        assert stats.values["Yellow Cards"] == (4, 2)
        assert stats.values["Total Shots"] == (11, 20)
        assert stats.values["Shots on Goal"] == (3, 4)
        assert "Ball possession" not in stats.values

    async def test_integracion_con_verify_pick(self, provider):
        """Un pick de córners se liquida entero con la respuesta mock."""
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Menos de 11.5 córners",
            mercado="over/under córners",
            evento="Elche - Real Madrid",
            linea=11.5,
            deporte="futbol",
            fecha_evento=datetime(2026, 9, 15, 18, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        # 2 + 5 = 7 córners < 11.5 -> acierto.
        assert (acierto, anulada) == (True, False)


class TestFindMatchEvents:
    """`/incidents` -> MatchEvents: desbloquea mercados de jugador fuera
    de la ventana del plan gratis de API-Football."""

    async def test_incidents_a_eventos_canonicos(self, provider):
        events = await provider.find_match_events(
            datetime(2026, 9, 15, 18, 0), "Elche - Real Madrid"
        )
        assert events is not None
        assert "Alexander Isak" in events.scorers
        assert "Cody Gakpo" in events.assisters
        assert "Alexis Mac Allister" in events.booked
        # Propia puerta: participa pero no es goleador.
        assert "Defensor Raro" not in events.scorers
        assert "Defensor Raro" in events.participants
        # Los cambios cuentan como participación.
        assert "Suplente Uno" in events.participants

    async def test_integracion_jugador_marca(self, provider):
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Alexander Isak marca",
            mercado="goleador",
            evento="Elche - Real Madrid",
            deporte="futbol",
            fecha_evento=datetime(2026, 9, 15, 18, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_jugador_que_jugo_sin_marcar_falla(self, provider):
        # Gakpo asistió pero no marcó: "marca" falla, no anulada.
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Cody Gakpo marca",
            mercado="goleador",
            evento="Elche - Real Madrid",
            deporte="futbol",
            fecha_evento=datetime(2026, 9, 15, 18, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (False, False)

    async def test_jugador_que_no_jugo_anulada(self, provider):
        # Convocado sin minutos: la casa devuelve -> anulada.
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Convocado Sin Jugar marca",
            mercado="goleador",
            evento="Elche - Real Madrid",
            deporte="futbol",
            fecha_evento=datetime(2026, 9, 15, 18, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (None, True)


class TestFindMatchPlayers:
    """`/lineups` -> MatchPlayers: minutos disputados + stats por
    jugador para props con número."""

    async def test_played_y_stats_canonicos(self, provider):
        players = await provider.find_match_players(
            datetime(2026, 9, 15, 18, 0), "Elche - Real Madrid"
        )
        assert players is not None
        assert "Ante Budimir" in players.played
        assert "Suplente Uno" in players.played  # entró 15 min
        assert "Convocado Sin Jugar" not in players.played  # 0 min
        # Tiros totales = on + off + blocked (2+1+1 = 4).
        assert players.stats["Ante Budimir"]["shots.total"] == 4
        assert players.stats["Ante Budimir"]["shots.on"] == 2
        assert players.stats["Ante Budimir"]["goals.total"] == 1
        assert players.stats["Alexander Isak"]["goals.total"] == 1
        assert players.stats["Cody Gakpo"]["goals.assists"] == 1

    async def test_integracion_prop_tiros_a_puerta(self, provider):
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Ante Budimir más de 1.5 tiros a puerta",
            mercado="over/under",
            evento="Elche - Real Madrid",
            linea=1.5,
            deporte="futbol",
            fecha_evento=datetime(2026, 9, 15, 18, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        # Budimir: 2 tiros a puerta > 1.5 -> acierto.
        assert (acierto, anulada) == (True, False)
