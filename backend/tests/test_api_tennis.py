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

    async def test_retirada_reporta_status_void(self, monkeypatch):
        # El partido se encontró pero acabó por retirada: el verificador
        # lo anula, no lo deja pendiente ni lo resuelve con el parcial.
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
        assert match is not None
        assert match.status == "retired"
        assert match.sets is None

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
        # 1 llamada a /search (sin jugador -> cae al barrido) + 1 a la
        # primera categoría del primer día.
        assert len(calls) == 2

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
        assert n == 10  # 1 search (jugador no hallado) + fecha × 9 categorías
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


class _ErrorClient:
    """Cliente cuya respuesta siempre lanza HTTPStatusError 500
    (error transitorio, no de cuota)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("boom", request=request, response=response)


class TestMissedProvisional:
    """Un "no encontrado" sobre un evento reciente (<48h) NO es
    definitivo: el partido puede no haberse jugado aún cuando lo
    buscamos. Se recuerda con TTL corto (6h) bajo una clave "|prov";
    el definitivo (15 días) solo se usa para eventos ya pasados."""

    async def test_fallo_reciente_es_provisional_no_definitivo(self, monkeypatch):
        # Evento de hoy: el fallo se marca provisional, NO definitivo.
        hoy = datetime.now()
        calls = []
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, {"events": []}),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider.find_match(hoy, "Nadal") is None

        prov_key = f"tennisapi1|{hoy.strftime('%Y-%m-%d')}|nadal|prov"
        def_key = f"tennisapi1|{hoy.strftime('%Y-%m-%d')}|nadal"
        assert results_base.is_missed(prov_key, results_base.MISSED_TTL_PROVISIONAL)
        assert not results_base.is_missed(def_key)

    async def test_fallo_antiguo_es_definitivo(self, monkeypatch):
        # Evento de hace 5 días: la ausencia es real -> definitivo.
        viejo = datetime(2026, 9, 10)
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient([], {"events": []}),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider.find_match(viejo, "Nadal") is None
        def_key = f"tennisapi1|{viejo.strftime('%Y-%m-%d')}|nadal"
        assert results_base.is_missed(def_key)

    async def test_evento_en_vivo_no_marca_missed(self, monkeypatch):
        # El partido aparece en el feed pero está "inprogress": no es un
        # fallo de búsqueda, no se marca missed y el siguiente ciclo
        # vuelve a llamar.
        hoy = datetime.now()
        calls = []
        payload = {
            "events": [
                {
                    "status": {"type": "inprogress"},
                    "homeTeam": {"name": "Marco Cecchinato"},
                    "awayTeam": {"name": "Marvin Moeller"},
                    "homeScore": {"current": 1},
                    "awayScore": {"current": 0},
                }
            ]
        }
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _CountingClient(calls, payload),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider.find_match(hoy, "Cecchinato") is None
        n = len(calls)
        assert n > 0
        provider2 = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider2.find_match(hoy, "Cecchinato") is None
        assert len(calls) > n  # reintenta: no quedó marcado como missed

    async def test_error_api_no_marca_missed(self, monkeypatch):
        # Un 500 transitorio no es evidencia de ausencia: no se marca
        # missed y el siguiente ciclo reintenta.
        hoy = datetime.now()
        calls = []

        class _CountingErrorClient(_ErrorClient):
            async def get(self, url, params=None, headers=None):
                calls.append(url)
                return await super().get(url, params=params, headers=headers)

        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _CountingErrorClient(),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider.find_match(hoy, "Nadal") is None
        n = len(calls)
        assert n > 0
        provider2 = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider2.find_match(hoy, "Nadal") is None
        assert len(calls) > n

    async def test_rapidapi_error_no_marca_missed(self, monkeypatch):
        calls = []

        class _CountingErrorClient(_ErrorClient):
            async def get(self, url, params=None, headers=None):
                calls.append(url)
                return await super().get(url, params=params, headers=headers)

        monkeypatch.setattr(
            rapidapi_tennis.httpx,
            "AsyncClient",
            lambda **kw: _CountingErrorClient(),
        )
        provider = RapidApiTennisProvider("k", "tennis-api-atp-wta-itf.p.rapidapi.com")
        assert await provider.find_match(datetime.now(), "Cecchinato") is None
        provider2 = RapidApiTennisProvider("k", "tennis-api-atp-wta-itf.p.rapidapi.com")
        assert await provider2.find_match(datetime.now(), "Cecchinato") is None
        assert len(calls) == 2  # reintentó: el error no quedó como missed


class _RoutingClient:
    """Cliente que devuelve payloads distintos según la URL pedida:
    /search/, /events/near y /category/ por separado."""

    def __init__(self, calls, search=None, near=None, category=None):
        self._calls = calls
        self._search = search if search is not None else {"results": []}
        self._near = near if near is not None else {}
        self._category = category if category is not None else {"events": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self._calls.append(url)
        if "/search/" in url:
            return _FakeResponse(self._search)
        if url.endswith("/events/near"):
            return _FakeResponse(self._near)
        return _FakeResponse(self._category)


class TestTennisApi1PlayerLookup:
    """Vía por jugador: /search -> id -> events/near. ~2 llamadas por
    pick frente al barrido de hasta 9 categorías, y resuelve nombres
    parciales en servidor ("chidek" -> "Clement Chidekh")."""

    _NEAR_CECCHINATO = {
        "previousEvent": {
            "id": 17058706,
            "startTimestamp": 1789642800,  # 2026-09-17 ~11:00 UTC
            "status": {"type": "finished"},
            "homeTeam": {"name": "Marco Cecchinato"},
            "awayTeam": {"name": "Marvin Möller"},
            "homeScore": {"current": 2, "period1": 7, "period2": 6, "period3": 6},
            "awayScore": {"current": 1, "period1": 6, "period2": 7, "period3": 2},
        },
        "nextEvent": None,
    }

    async def test_resuelve_por_jugador_en_dos_llamadas(self, monkeypatch):
        calls = []
        search = {
            "results": [
                {
                    "entity": {
                        "id": 44549,
                        "name": "Marco Cecchinato",
                        "sport": {"slug": "tennis"},
                    }
                }
            ]
        }
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(
                calls, search=search, near=self._NEAR_CECCHINATO
            ),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        match = await provider.find_match(datetime(2026, 9, 17, 10, 0), "cecchinato")
        assert match is not None
        assert match.home_team == "Marco Cecchinato"
        assert match.away_team == "Marvin Möller"
        assert (match.home_score, match.away_score) == (2, 1)
        # 2 llamadas: /search + /events/near — ninguna de categoría.
        assert len(calls) == 2
        assert not any("/category/" in u for u in calls)

    async def test_nombre_parcial_resuelto_en_servidor(self, monkeypatch):
        # "chidek" -> "Clement Chidekh": la búsqueda del lado de la API
        # corrige el nombre truncado del OCR/tipster.
        calls = []
        search = {
            "results": [
                {
                    "entity": {
                        "id": 231620,
                        "name": "Clement Chidekh",
                        "sport": {"slug": "tennis"},
                    }
                }
            ]
        }
        near = {
            "previousEvent": {
                "id": 1,
                "startTimestamp": 1789736400,  # 2026-09-18
                "status": {"type": "finished"},
                "homeTeam": {"name": "Clement Chidekh"},
                "awayTeam": {"name": "Harold Mayot"},
                "homeScore": {"current": 2, "period1": 6, "period2": 6},
                "awayScore": {"current": 0, "period1": 3, "period2": 4},
            }
        }
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(calls, search=search, near=near),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        match = await provider.find_match(datetime(2026, 9, 18, 10, 0), "chidek")
        assert match is not None
        assert match.home_team == "Clement Chidekh"

    async def test_jugador_no_encontrado_cae_al_barrido(self, monkeypatch):
        calls = []
        category = {
            "events": [
                {
                    "status": {"type": "finished"},
                    "homeTeam": {"name": "Carlos Alcaraz"},
                    "awayTeam": {"name": "Ben Shelton"},
                    "homeScore": {"current": 2},
                    "awayScore": {"current": 0},
                }
            ]
        }
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(calls, category=category),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        match = await provider.find_match(datetime(2026, 9, 15), "Alcaraz")
        assert match is not None
        assert match.home_team == "Carlos Alcaraz"
        assert any("/category/" in u for u in calls)

    async def test_evento_near_en_vivo_no_escanea_ni_marca(self, monkeypatch):
        # Su partido aparece en near pero está en vivo: no se gasta el
        # barrido ni se marca missed — el próximo ciclo lo resuelve.
        hoy = datetime.now()
        calls = []
        search = {
            "results": [
                {
                    "entity": {
                        "id": 44549,
                        "name": "Marco Cecchinato",
                        "sport": {"slug": "tennis"},
                    }
                }
            ]
        }
        near = {
            "previousEvent": {
                "id": 1,
                "startTimestamp": int(hoy.timestamp()),
                "status": {"type": "inprogress"},
                "homeTeam": {"name": "Marco Cecchinato"},
                "awayTeam": {"name": "Marvin Möller"},
                "homeScore": {"current": 1},
                "awayScore": {"current": 0},
            }
        }
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(calls, search=search, near=near),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider.find_match(hoy, "cecchinato") is None
        assert len(calls) == 2  # no llegó al barrido
        assert not any("/category/" in u for u in calls)

    async def test_near_fuera_de_fecha_cae_al_barrido(self, monkeypatch):
        # El pick es de hace 5 días: events/near solo trae los partidos
        # recientes del jugador — cae al barrido por categorías.
        viejo = datetime(2026, 9, 10)
        calls = []
        search = {
            "results": [
                {
                    "entity": {
                        "id": 44549,
                        "name": "Marco Cecchinato",
                        "sport": {"slug": "tennis"},
                    }
                }
            ]
        }
        monkeypatch.setattr(
            tennisapi1.httpx,
            "AsyncClient",
            lambda **kw: _RoutingClient(
                calls, search=search, near=self._NEAR_CECCHINATO
            ),
        )
        provider = TennisApi1Provider("k", "tennisapi1.p.rapidapi.com")
        assert await provider.find_match(viejo, "Cecchinato") is None
        assert any("/category/" in u for u in calls)
