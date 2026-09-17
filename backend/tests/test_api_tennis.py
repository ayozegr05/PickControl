"""Tests de los proveedores de resultados de tenis.

Sin llamadas reales: cubren el matching de nombres de jugador (los
tipsters escriben "Titouan Droguet" y las APIs devuelven "Droguet" o
"T. Droguet"), el parseo de `strResult` de TheSportsDB, el formato
Sofascore del fallback de RapidAPI y las cachés por fecha.
"""

from datetime import datetime

import httpx
import pytest

import app.services.results.api_tennis as api_tennis
import app.services.results.base as results_base
import app.services.results.rapidapi_tennis as rapidapi_tennis
import app.services.results.tennisapi1 as tennisapi1
from app.services.results.api_tennis import (
    ApiTennisProvider,
    _parse_result,
    _player_similar,
)
from app.services.results.rapidapi_tennis import RapidApiTennisProvider
from app.services.results.tennisapi1 import TennisApi1Provider


@pytest.fixture(autouse=True)
def _reset_provider_state(monkeypatch, tmp_path):
    """El estado de cuota/fallos se persiste en provider_state.json:
    cada test usa un archivo temporal para no contaminar otros tests
    ni escribir en el directorio real del backend."""
    monkeypatch.setattr(results_base, "_STATE", None)
    monkeypatch.setattr(results_base, "_STATE_FILE", tmp_path / "provider_state.json")


class TestPlayerSimilar:
    def test_nombre_completo_vs_solo_apellido(self):
        assert _player_similar("Titouan Droguet", "Droguet") >= 0.6

    def test_nombre_completo_vs_apellido_inicial(self):
        assert _player_similar("Carlos Alcaraz", "C. Alcaraz") >= 0.6

    def test_nombre_identico(self):
        assert _player_similar("Novak Djokovic", "Novak Djokovic") == 1.0

    def test_jugador_distinto_no_matchea(self):
        assert _player_similar("Carlos Alcaraz", "Jannik Sinner") < 0.6

    def test_apellido_distinto_aunque_comparta_inicial(self):
        assert _player_similar("Carlos Alcaraz", "C. Gomez") < 0.6


class TestParseResult:
    """Formato real de TheSportsDB:
    strResult="Siniakova  beat Udvardy  2-0\\r\\nSiniakova : 6 6..."."""

    def test_resultado_normal(self):
        event = {
            "strResult": "Siniakova  beat Udvardy  2-0\r\nSiniakova : 6 6 \r\n"
            "Udvardy : 1 2"
        }
        match = _parse_result(event)
        assert match is not None
        assert match.home_team == "Siniakova"
        assert match.away_team == "Udvardy"
        assert (match.home_score, match.away_score) == (2, 0)

    def test_resultado_tres_sets(self):
        event = {"strResult": "Fernandez  beat Tjen  2-1"}
        match = _parse_result(event)
        assert match is not None
        assert (match.home_score, match.away_score) == (2, 1)

    def test_sin_resultado_devuelve_none(self):
        assert _parse_result({"strResult": None}) is None
        assert _parse_result({"strResult": ""}) is None
        assert _parse_result({}) is None


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _CountingClient:
    def __init__(self, calls, payload):
        self._calls = calls
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self._calls.append(params or {})
        return _FakeResponse(self._payload)


class TestApiTennisProvider:
    """TheSportsDB (primario, gratis)."""

    async def test_encuentra_partido_y_resuelve_sets(self, monkeypatch):
        payload = {
            "events": [
                {
                    "strEvent": "Wuhan Droguet vs Cassone",
                    "strResult": "Droguet  beat Cassone  2-0",
                }
            ]
        }
        monkeypatch.setattr(
            api_tennis.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient([], payload),
        )
        provider = ApiTennisProvider("3")
        match = await provider.find_match(datetime(2026, 9, 15), "Titouan Droguet")
        assert match is not None
        assert match.home_team == "Droguet"
        assert (match.home_score, match.away_score) == (2, 0)

    async def test_reusa_eventos_de_la_misma_fecha(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            api_tennis.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"events": []}),
        )
        provider = ApiTennisProvider("3")

        await provider.find_match(datetime(2026, 9, 15), "Alcaraz")
        assert len(calls) == 3  # fecha y ±1 día

        await provider.find_match(datetime(2026, 9, 15), "Sinner")
        assert len(calls) == 3  # todo desde caché


class TestRapidApiTennisProvider:
    """ "Tennis API - ATP WTA ITF" en RapidAPI (fallback): busca por
    jugador en `profile/{nombre}/matches-played`, donde player1 es
    siempre el ganador."""

    async def test_encuentra_partido_por_jugador(self, monkeypatch):
        payload = {
            "singles": [
                {
                    "result": "6-3 3-6 7-5",
                    "date": "2026-09-15T14:00:00.000Z",
                    "player1": {"name": "Titouan Droguet"},
                    "player2": {"name": "Marco Cassone"},
                }
            ]
        }
        monkeypatch.setattr(
            rapidapi_tennis.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient([], payload),
        )
        provider = RapidApiTennisProvider("k", "tennis-api-atp-wta-itf.p.rapidapi.com")
        match = await provider.find_match(datetime(2026, 9, 15), "Droguet")
        assert match is not None
        assert match.home_team == "Titouan Droguet"
        assert (match.home_score, match.away_score) == (2, 1)

    async def test_ignora_partidos_de_otra_fecha(self, monkeypatch):
        payload = {
            "singles": [
                {
                    "result": "6-3 6-4",
                    "date": "2026-08-20T14:00:00.000Z",
                    "player1": {"name": "Titouan Droguet"},
                    "player2": {"name": "Marco Cassone"},
                }
            ]
        }
        monkeypatch.setattr(
            rapidapi_tennis.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient([], payload),
        )
        provider = RapidApiTennisProvider("k", "tennis-api-atp-wta-itf.p.rapidapi.com")
        match = await provider.find_match(datetime(2026, 9, 15), "Droguet")
        assert match is None

    async def test_retirada_queda_pendiente(self, monkeypatch):
        payload = {
            "singles": [
                {
                    "result": "6-3 2-1 RET",
                    "date": "2026-09-15T14:00:00.000Z",
                    "player1": {"name": "Titouan Droguet"},
                    "player2": {"name": "Marco Cassone"},
                }
            ]
        }
        monkeypatch.setattr(
            rapidapi_tennis.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient([], payload),
        )
        provider = RapidApiTennisProvider("k", "tennis-api-atp-wta-itf.p.rapidapi.com")
        match = await provider.find_match(datetime(2026, 9, 15), "Droguet")
        assert match is None

    async def test_reusa_historial_del_mismo_jugador(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            rapidapi_tennis.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"singles": []}),
        )
        provider = RapidApiTennisProvider("k", "tennis-api-atp-wta-itf.p.rapidapi.com")

        await provider.find_match(datetime(2026, 9, 15), "Alcaraz")
        await provider.find_match(datetime(2026, 9, 20), "Alcaraz")
        assert len(calls) == 1  # mismo jugador y año → caché


class _RateLimitedClient:
    """Cliente cuya respuesta siempre lanza HTTPStatusError 429."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("quota", request=request, response=response)


class TestRateLimit:
    async def test_429_marca_el_proveedor_y_se_salta(self, monkeypatch):
        monkeypatch.setattr(
            rapidapi_tennis.httpx, "AsyncClient", lambda **kw: _RateLimitedClient()
        )
        provider = RapidApiTennisProvider("k", "tennis-api-atp-wta-itf.p.rapidapi.com")

        assert await provider.find_match(datetime(2026, 9, 15), "Alcaraz") is None
        assert results_base.is_rate_limited("rapidapi-tennis-atp-wta-itf")
        # Las siguientes llamadas ni siquiera intentan la petición.
        assert await provider.find_match(datetime(2026, 9, 15), "Sinner") is None


class TestTennisApi1Provider:
    """tennisapi1 (Sofascore): eventos por categoría y fecha; player1 no
    es ganador — home/away vienen tal cual y ganan los que tienen más
    sets en `homeScore.current`/`awayScore.current`."""

    def _payload(self, events):
        return {"events": events}

    async def test_encuentra_partido_y_sale_en_la_primera_categoria(self, monkeypatch):
        calls = []
        payload = self._payload(
            [
                {
                    "status": {"type": "finished"},
                    "homeTeam": {"name": "Carlos Alcaraz"},
                    "awayTeam": {"name": "Ben Shelton"},
                    "homeScore": {"current": 3},
                    "awayScore": {"current": 2},
                }
            ]
        )
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, payload),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        match = await provider.find_match(datetime(2026, 9, 15), "Alcaraz")
        assert match is not None
        assert match.home_team == "Carlos Alcaraz"
        assert (match.home_score, match.away_score) == (3, 2)
        # Encontrado en la primera categoría del primer día: 1 llamada.
        assert len(calls) == 1

    async def test_ignora_en_juego_y_dobles(self, monkeypatch):
        payload = self._payload(
            [
                {
                    "status": {"type": "inprogress"},
                    "homeTeam": {"name": "Carlos Alcaraz"},
                    "awayTeam": {"name": "Ben Shelton"},
                    "homeScore": {"current": 1},
                    "awayScore": {"current": 0},
                },
                {
                    "status": {"type": "finished"},
                    "homeTeam": {"name": "Alcaraz C / Lopez P"},
                    "awayTeam": {"name": "Shelton B / Paul T"},
                    "homeScore": {"current": 2},
                    "awayScore": {"current": 0},
                },
            ]
        )
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient([], payload),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider.find_match(datetime(2026, 9, 15), "Alcaraz") is None

    async def test_fallo_no_se_repite(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"events": []}),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")

        assert await provider.find_match(datetime(2026, 9, 15), "Nadal") is None
        n = len(calls)
        assert n == 9  # fecha exacta × 9 categorías
        # El fallo queda registrado en el estado persistente: ni una
        # instancia nueva (otro ciclo del verificador) repite llamadas.
        provider2 = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider2.find_match(datetime(2026, 9, 15), "Nadal") is None
        assert len(calls) == n

    async def test_429_marca_y_no_gasta_mas(self, monkeypatch):
        monkeypatch.setattr(
            tennisapi1.httpx, "AsyncClient", lambda **kw: _RateLimitedClient()
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider.find_match(datetime(2026, 9, 15), "Nadal") is None
        assert results_base.is_rate_limited("tennisapi1")
