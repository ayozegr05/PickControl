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
