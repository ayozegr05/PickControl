"""Tests de `allsports_tennis.py` (allsportsapi2 / Sofascore vía RapidAPI).

La red se corta en `_get_json` (único punto de HTTP); `provider_state`
se neutraliza para no tocar el JSON real ni depender de la fecha actual.
"""

from datetime import datetime, timezone

import pytest

import app.services.results.allsports_tennis as allsports
from app.models.parsed_pick import ParsedPick
from app.services.results.allsports_tennis import AllSportsTennisProvider
from app.services.results.verifier import verify_pick

_PLAYER_ID = 40800
_PAIR_ID = 55501
_EVENT_TS = int(datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc).timestamp())

_SEARCH_CARRENO = {
    "results": [
        {
            "entity": {
                "id": _PLAYER_ID,
                "name": "Pablo Carreno Busta",
                "sport": {"slug": "tennis"},
            }
        }
    ]
}

_SEARCH_ARENDS = {
    "results": [
        {
            "entity": {
                "id": _PAIR_ID,
                "name": "Arends S / Pel D",
                "sport": {"slug": "tennis"},
            }
        }
    ]
}

_ITF_EVENT = {
    "id": 1,
    "startTimestamp": _EVENT_TS,
    "status": {"type": "finished"},
    "tournament": {"name": "Montemar, Spain"},
    "homeTeam": {"name": "Pedro Martinez"},
    "awayTeam": {"name": "Pablo Carreno Busta"},
    "homeScore": {"current": 1, "period1": 4, "period2": 6, "period3": 4},
    "awayScore": {"current": 2, "period1": 6, "period2": 4, "period3": 6},
    "winnerCode": 2,
}

_OLD_EVENT = {
    "id": 2,
    "startTimestamp": _EVENT_TS - 86400 * 20,
    "status": {"type": "finished"},
    "homeTeam": {"name": "Pablo Carreno Busta"},
    "awayTeam": {"name": "Otro Rival"},
    "homeScore": {"current": 2, "period1": 6, "period2": 6},
    "awayScore": {"current": 0, "period1": 2, "period2": 3},
}

_DOUBLES_EVENT = {
    "id": 3,
    "startTimestamp": _EVENT_TS,
    "status": {"type": "finished"},
    "homeTeam": {"name": "Arends S / Pel D"},
    "awayTeam": {"name": "Rojer J J / Tecau H"},
    "homeScore": {"current": 2, "period1": 6, "period2": 7},
    "awayScore": {"current": 0, "period1": 3, "period2": 5},
    "winnerCode": 1,
}


@pytest.fixture
def provider(monkeypatch):
    """Provider con HTTP y estado de cuota/misses neutralizados."""
    monkeypatch.setattr(allsports, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(allsports, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(allsports, "mark_missed", lambda key: None)
    monkeypatch.setattr(allsports, "mark_rate_limited", lambda name: None)

    prov = AllSportsTennisProvider("key", "allsportsapi2.p.rapidapi.com")

    responses = {
        "/api/tennis/search/pedro%20martinez": {"results": []},
        "/api/tennis/search/pablo%20carreno%20busta": _SEARCH_CARRENO,
        "/api/tennis/search/arends": _SEARCH_ARENDS,
        "/api/tennis/search/rojer": {"results": []},
        f"/api/tennis/team/{_PLAYER_ID}/events/previous/0": {
            "events": [_ITF_EVENT, _OLD_EVENT]
        },
        f"/api/tennis/team/{_PAIR_ID}/events/previous/0": {"events": [_DOUBLES_EVENT]},
    }

    async def fake_get(client, path):
        return responses.get(path, {})

    monkeypatch.setattr(prov, "_get_json", fake_get)
    return prov


class TestFindMatch:
    async def test_resultado_itf_con_sets(self, provider):
        # El evento es un ITF Futures ("Montemar") — el hueco que este
        # provider cierra: fuera de ATP/WTA pero Sofascore lo indexa.
        match = await provider.find_match(
            datetime(2026, 9, 15, 12, 0), "Pedro Martinez - Pablo Carreno Busta"
        )
        assert match is not None
        assert match.home_team == "Pedro Martinez"
        assert match.away_team == "Pablo Carreno Busta"
        assert (match.home_score, match.away_score) == (1, 2)
        assert match.sets == [(4, 6), (6, 4), (4, 6)]

    async def test_evento_fuera_de_fecha_se_ignora(self, provider):
        # El evento viejo (20 días antes) no casa por fecha; solo queda
        # el del ITF.
        match = await provider.find_match(
            datetime(2026, 9, 15, 12, 0), "Pablo Carreno Busta - Otro Rival"
        )
        assert match is None or match.away_team != "Otro Rival"

    async def test_jugador_no_encontrado(self, provider):
        match = await provider.find_match(
            datetime(2026, 9, 15, 12, 0), "Xinavarro Tenis - Nadie ATP"
        )
        assert match is None

    async def test_evento_sin_terminar_devuelve_none(self, provider, monkeypatch):
        live = dict(_ITF_EVENT)
        live["status"] = {"type": "inprogress"}
        prov2 = AllSportsTennisProvider("k", "h")

        async def fake_get(client, path):
            if "search" in path:
                return _SEARCH_CARRENO
            if "previous" in path:
                return {"events": [live]}
            return {}

        monkeypatch.setattr(prov2, "_get_json", fake_get)
        match = await prov2.find_match(
            datetime(2026, 9, 15, 12, 0), "Pedro Martinez - Pablo Carreno Busta"
        )
        assert match is None

    async def test_dobles_se_resuelve_por_la_pareja(self, provider):
        # La pareja es entidad propia en Sofascore: sale al buscar el
        # apellido de un miembro y sus events son los del equipo.
        match = await provider.find_match(
            datetime(2026, 9, 15, 12, 0), "Arends / Pel - Rojer / Tecau"
        )
        assert match is not None
        assert match.home_team == "Arends S / Pel D"
        assert (match.home_score, match.away_score) == (2, 0)
        assert match.sets == [(6, 3), (7, 5)]

    async def test_integracion_con_verify_pick(self, provider):
        """Un pick de tenis se liquida entero: Carreño ganó 2-1."""
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Pablo Carreno Busta gana",
            mercado="ganador",
            evento="Pedro Martinez vs Pablo Carreno Busta",
            deporte="tenis",
            fecha_evento=datetime(2026, 9, 15, 12, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)

    async def test_integracion_over_juegos(self, provider):
        """Total de juegos: 4+6 + 6+4 + 4+6 = 30 -> más de 27.5 acierta."""
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Más de 27.5 juegos",
            mercado="over/under juegos",
            evento="Pedro Martinez vs Pablo Carreno Busta",
            linea=27.5,
            deporte="tenis",
            fecha_evento=datetime(2026, 9, 15, 12, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        assert (acierto, anulada) == (True, False)
