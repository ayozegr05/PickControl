"""Tests de `sofascore_basketball.py` (allsportsapi2 / Sofascore).

Fallback de baloncesto sin ventana de fechas: subclase de
`FootApiStatsProvider` que solo cambia el filtro de deporte, el
namespace de caché y el marcador al descanso (cuartos -> HT).
La red se corta en `_get_json`; el estado de cuota/misses se
neutraliza como en `test_footapi_stats.py`.
"""

from datetime import datetime, timezone

import httpx
import pytest

import app.services.results.footapi_stats as footapi
from app.models.parsed_pick import ParsedPick
from app.services.results.api_basketball import ApiBasketballProvider
from app.services.results.sofascore_basketball import SofascoreBasketballProvider
from app.services.results.verifier import verify_pick

_TEAM_ID = 3000
_EVENT_ID = 523149
_EVENT_TS = int(datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc).timestamp())

# La búsqueda va por la primera parte del cruce ("Barcelona - Joventut"
# -> "barcelona"). Devuelve una entidad de fútbol con nombre parecido
# (que debe ignorarse) y el equipo de basket correcto.
_SEARCH_BARCELONA = {
    "results": [
        {
            "entity": {
                "id": 4001,
                "name": "Barcelona SC",
                "sport": {"slug": "football"},
            }
        },
        {
            "entity": {
                "id": _TEAM_ID,
                "name": "FC Barcelona",
                "sport": {"slug": "basketball"},
            }
        },
    ]
}

# Solo una entidad de fútbol: el filtro `sport.slug` debe descartarla.
_SEARCH_SOLO_FUTBOL = {"results": [_SEARCH_BARCELONA["results"][0]]}

_PREVIOUS = {
    "events": [
        {
            "id": _EVENT_ID,
            "startTimestamp": _EVENT_TS,
            "status": {"type": "finished"},
            "homeTeam": {"name": "Joventut Badalona"},
            "awayTeam": {"name": "FC Barcelona"},
            "homeScore": {
                "current": 101,
                "period1": 25,
                "period2": 30,
                "period3": 28,
                "period4": 18,
            },
            "awayScore": {
                "current": 87,
                "period1": 20,
                "period2": 22,
                "period3": 25,
                "period4": 20,
            },
        }
    ]
}


@pytest.fixture
def provider(monkeypatch):
    """Provider con HTTP y estado de cuota/misses neutralizados."""
    monkeypatch.setattr(footapi, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(footapi, "is_missed", lambda key, ttl=None: False)
    monkeypatch.setattr(footapi, "mark_missed", lambda key: None)
    monkeypatch.setattr(footapi, "mark_rate_limited", lambda name: None)

    prov = SofascoreBasketballProvider("key", "allsportsapi2.p.rapidapi.com")
    responses = {
        "/api/search/barcelona": _SEARCH_BARCELONA,
        "/api/search/joventut": _SEARCH_BARCELONA,
        f"/api/team/{_TEAM_ID}/matches/previous/0": _PREVIOUS,
    }

    async def fake_get(client, path):
        return responses.get(path, {})

    monkeypatch.setattr(prov, "_get_json", fake_get)
    return prov


class TestFindMatch:
    async def test_marcador_y_ht_por_cuartos(self, provider):
        # "Barcelona - Joventut" al revés de como jugó (Joventut local).
        match = await provider.find_match(
            datetime(2026, 9, 20, 18, 0), "Barcelona - Joventut"
        )
        assert match is not None
        assert match.home_score == 101
        assert match.away_score == 87
        # HT = Q1 + Q2 de cada equipo.
        assert match.ht_home_score == 55
        assert match.ht_away_score == 42

    async def test_filtro_deporte_basketball(self, provider):
        """Si solo hay entidad de fútbol con ese nombre, no se usa."""
        prov = SofascoreBasketballProvider("k", "h")
        responses = {"/api/search/joventut": _SEARCH_SOLO_FUTBOL}

        async def fake_get(client, path):
            return responses.get(path, {})

        prov._get_json = fake_get
        match = await prov.find_match(datetime(2026, 9, 20, 18, 0), "Joventut")
        assert match is None


class TestNamespaceYEstado:
    async def test_miss_key_con_etiqueta_baloncesto(self, provider, monkeypatch):
        marked: list[str] = []
        monkeypatch.setattr(footapi, "mark_missed", marked.append)

        prov = SofascoreBasketballProvider("k", "h")

        async def fake_get(client, path):
            return {"results": []} if "search" in path else {}

        prov._get_json = fake_get
        await prov.find_match(datetime(2026, 9, 20, 18, 0), "Sin Equipo")

        assert marked, "debió marcarse el miss"
        assert marked[0].startswith("allsportsapi2|baloncesto|")

    async def test_rate_limit_marca_allsportsapi2(self, monkeypatch):
        """429 en basket marca 'allsportsapi2' (cuota compartida)."""
        marked: list[str] = []
        monkeypatch.setattr(
            footapi,
            "mark_rate_limited",
            lambda name, response=None: marked.append(name),
        )

        prov = SofascoreBasketballProvider("k", "h")

        class _FakeClient:
            async def get(self, url, headers=None):
                request = httpx.Request("GET", url)
                return httpx.Response(429, request=request)

        result = await prov._get_json(_FakeClient(), "/api/search/joventut")

        assert result is None
        assert marked == ["allsportsapi2"]


class TestStatsDesactivadas:
    async def test_stats_devuelven_none(self, provider):
        date = datetime(2026, 9, 20, 18, 0)
        assert await provider.find_match_stats(date, "x") is None
        assert await provider.find_match_stats_1h(date, "x") is None
        assert await provider.find_match_events(date, "x") is None
        assert await provider.find_match_players(date, "x") is None


class TestIntegracionVerifyPick:
    async def test_ganador_fallo(self, provider):
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Barcelona gana",
            mercado="ganador",
            evento="Barcelona - Joventut",
            deporte="baloncesto",
            fecha_evento=datetime(2026, 9, 20, 18, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        # Ganó Joventut 101-87 -> "Barcelona gana" es fallo.
        assert (acierto, anulada) == (False, False)

    async def test_over_puntos_resuelve(self, provider):
        pick = ParsedPick(
            raw_message_id=1,
            seleccion="Más de 180.5 puntos",
            mercado="over/under",
            evento="Barcelona - Joventut",
            linea=180.5,
            deporte="baloncesto",
            fecha_evento=datetime(2026, 9, 20, 18, 0),
            es_apuesta=True,
        )
        acierto, anulada = await verify_pick(pick, [provider])
        # 101 + 87 = 188 puntos > 180.5 -> acierto.
        assert (acierto, anulada) == (True, False)


class TestRegistroEnVerifier:
    async def test_provider_en_cadena(self, monkeypatch):
        """Con key RapidAPI + api-basketball, el fallback va al final."""
        from types import SimpleNamespace

        import app.services.results.verifier as verifier

        monkeypatch.setattr(
            verifier,
            "get_settings",
            lambda: SimpleNamespace(
                rapidapi_tennis_key="k",
                rapidapi_allsports_host="allsportsapi2.p.rapidapi.com",
                api_basketball_key="k",
                api_basketball_host="v1.basketball.api-sports.io",
                football_data_api_key=None,
                api_football_key=None,
                api_football_host="x",
                api_tennis_key=None,
                rapidapi_tennis_host="x",
                rapidapi_tennisapi1_host="x",
                rapidapi_footapi_host="x",
            ),
        )
        providers = await verifier._get_providers()
        # El fallback allsportsapi2 va tras el primario API-Basketball
        # (detrás solo pueden quedar los nativos Sofascore, último
        # recurso por diseño).
        api_idx = next(
            i for i, p in enumerate(providers) if isinstance(p, ApiBasketballProvider)
        )
        fb_idx = next(
            i
            for i, p in enumerate(providers)
            if isinstance(p, SofascoreBasketballProvider)
        )
        assert fb_idx > api_idx
