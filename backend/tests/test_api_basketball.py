"""Tests de `api_basketball.py` (API-Basketball / api-sports).

Sin llamadas reales: se mockea `httpx.AsyncClient` y se neutralizan
`provider_state` y `provider_cache` para no tocar los JSON reales.
"""

from datetime import datetime

import pytest

import app.services.results.api_basketball as api_basketball
import app.services.results.base as results_base
import app.services.results.response_cache as rc
from app.models.parsed_pick import ParsedPick
from app.services.results.api_basketball import ApiBasketballProvider
from app.services.results.verifier import verify_pick

_GAME_DATE = datetime(2026, 9, 20, 19, 0)

_SUPERCOPA_GAME = {
    "id": 523149,
    "date": "2026-09-20T17:00:00+00:00",
    "league": {"id": 118, "name": "Supercopa ACB"},
    "teams": {
        "home": {"id": 2334, "name": "Joventut Badalona"},
        "away": {"id": 2329, "name": "Barcelona"},
    },
    "scores": {
        "home": {
            "quarter_1": 32,
            "quarter_2": 20,
            "quarter_3": 19,
            "quarter_4": 30,
            "over_time": None,
            "total": 101,
        },
        "away": {
            "quarter_1": 23,
            "quarter_2": 23,
            "quarter_3": 22,
            "quarter_4": 19,
            "over_time": None,
            "total": 87,
        },
    },
    "status": {"long": "Game Finished", "short": "FT", "timer": None},
}


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch, tmp_path):
    """Estado de cuota/misses y la caché de respuestas se persisten en
    JSON junto al backend: cada test usa archivos temporales."""
    monkeypatch.setattr(results_base, "_STATE", None)
    monkeypatch.setattr(results_base, "_STATE_FILE", tmp_path / "provider_state.json")
    monkeypatch.setattr(rc, "_CACHE", None)
    monkeypatch.setattr(rc, "_CACHE_FILE", tmp_path / "provider_cache.json")


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


def _patch_http(monkeypatch, payload, calls=None):
    monkeypatch.setattr(
        api_basketball.httpx,
        "AsyncClient",
        lambda **kw: _CountingClient(calls if calls is not None else [], payload),
    )


def _provider() -> ApiBasketballProvider:
    return ApiBasketballProvider("k", "v1.basketball.api-sports.io")


class TestFindMatch:
    async def test_encuentra_supercopa_y_marcador(self, monkeypatch):
        """El pick decía "Barcelona - Joventut" pero el partido real era
        Joventut (local) vs Barcelona: el matching es bidireccional."""
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]})
        match = await _provider().find_match(_GAME_DATE, "Barcelona - Joventut")
        assert match is not None
        assert match.home_team == "Joventut Badalona"
        assert match.away_team == "Barcelona"
        assert (match.home_score, match.away_score) == (101, 87)
        # HT = Q1+Q2 por lado (32+20 / 23+23).
        assert (match.ht_home_score, match.ht_away_score) == (52, 46)

    async def test_partido_no_encontrado(self, monkeypatch):
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]})
        match = await _provider().find_match(_GAME_DATE, "Baskonia - Real Madrid")
        assert match is None

    async def test_ignora_partidos_no_terminados(self, monkeypatch):
        live = dict(_SUPERCOPA_GAME, status={"long": "Q3", "short": "Q3"})
        _patch_http(monkeypatch, {"response": [live]})
        match = await _provider().find_match(_GAME_DATE, "Joventut - Barcelona")
        assert match is None

    async def test_reusa_lista_de_la_misma_fecha(self, monkeypatch):
        """Dos picks del mismo día: una sola llamada a games?date=."""
        calls = []
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]}, calls)
        provider = _provider()
        await provider.find_match(_GAME_DATE, "Barcelona - Joventut")
        await provider.find_match(_GAME_DATE, "Joventut Badalona")
        fechas = {c.get("date") for c in calls}
        # Fecha + offsets ±1 día: máximo 3 llamadas, la fecha central una.
        assert len(fechas) <= 3
        assert len(calls) <= 3


class TestPersistentCache:
    async def test_dia_terminado_se_persiste_y_no_rellama(self, monkeypatch):
        """Un día con todos los partidos cerrados queda en
        provider_cache.json: un provider nuevo lo reusa sin HTTP."""
        calls = []
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]}, calls)
        first = _provider()
        await first.find_match(_GAME_DATE, "Barcelona - Joventut")
        assert calls  # hubo llamadas

        entry = rc.get_event_list("api-basketball|games|2026-09-20")
        assert entry is not None

        calls.clear()
        second = _provider()
        match = await second.find_match(_GAME_DATE, "Barcelona - Joventut")
        assert match is not None and match.home_score == 101
        assert not calls  # cero HTTP

    async def test_dia_con_partidos_vivos_no_se_persiste(self, monkeypatch):
        live = dict(_SUPERCOPA_GAME, status={"long": "Q4", "short": "Q4"})
        _patch_http(monkeypatch, {"response": [live]})
        await _provider().find_match(_GAME_DATE, "Joventut - Barcelona")
        assert rc.get_event_list("api-basketball|games|2026-09-20") is None


class TestPostponed:
    async def test_partido_aplazado(self, monkeypatch):
        post = dict(_SUPERCOPA_GAME, status={"long": "Postponed", "short": "POST"})
        _patch_http(monkeypatch, {"response": [post]})
        state = await _provider().find_postponed_match(
            _GAME_DATE, "Joventut - Barcelona"
        )
        assert state is not None
        assert state.status == "postponed"
        assert state.home_team == "Joventut Badalona"


class TestRateLimit:
    async def test_errors_request_limit_marca_cuota(self, monkeypatch):
        payload = {"errors": {"requests": "request limit reached"}, "response": []}
        _patch_http(monkeypatch, payload)
        match = await _provider().find_match(_GAME_DATE, "Joventut - Barcelona")
        assert match is None
        assert results_base.is_rate_limited("api-basketball")

    async def test_provider_saltado_cuando_rate_limited(self, monkeypatch):
        calls = []
        results_base.mark_rate_limited("api-basketball")
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]}, calls)
        match = await _provider().find_match(_GAME_DATE, "Joventut - Barcelona")
        assert match is None
        assert not calls


class TestVerifyPickIntegration:
    """El pick real pendiente: 2249 "Barcelona - Joventut" ganador
    Barcelona — el resultado real fue Joventut 101-87 Barcelona."""

    def _pick(self, **kw) -> ParsedPick:
        base = dict(
            raw_message_id=1,
            evento="Barcelona - Joventut",
            deporte="baloncesto",
            fecha_evento=_GAME_DATE,
            es_apuesta=True,
        )
        base.update(kw)
        return ParsedPick(**base)

    async def test_ganador_barca_falla(self, monkeypatch):
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]})
        pick = self._pick(seleccion="Barcelona gana", mercado="ganador")
        acierto, anulada = await verify_pick(pick, [_provider()])
        assert (acierto, anulada) == (False, False)

    async def test_ganador_joventut_acierta(self, monkeypatch):
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]})
        pick = self._pick(seleccion="Joventut gana", mercado="ganador")
        acierto, anulada = await verify_pick(pick, [_provider()])
        assert (acierto, anulada) == (True, False)

    async def test_over_puntos_total_resuelve(self, monkeypatch):
        """101+87=188 > 180.5 puntos -> acierto (los puntos SÍ son el
        marcador en basket)."""
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]})
        pick = self._pick(
            seleccion="Más de 180.5 puntos",
            mercado="over/under",
            linea=180.5,
        )
        acierto, anulada = await verify_pick(pick, [_provider()])
        assert (acierto, anulada) == (True, False)

    async def test_over_rebotes_queda_pendiente(self, monkeypatch):
        """ "Más de 8.5 rebotes" no se resuelve con el marcador —
        pendiente, nunca falso resultado."""
        _patch_http(monkeypatch, {"response": [_SUPERCOPA_GAME]})
        pick = self._pick(
            seleccion="Más de 8.5 rebotes",
            mercado="over/under",
            linea=8.5,
        )
        acierto, anulada = await verify_pick(pick, [_provider()])
        assert (acierto, anulada) == (None, False)
