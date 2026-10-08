"""compat_llm: cascada de extractores OpenAI-compatible gratuitos.

Sin HTTP real: `_call` o `httpx.AsyncClient` se stubbean; el estado de
cooldown va a un provider_state.json aislado por test.
"""

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.core.dates import utc_now
from app.services.results import base as results_base
from app.services.results import compat_llm

pytestmark = pytest.mark.anyio


_REAL_ENDPOINTS = compat_llm._ENDPOINTS


@pytest.fixture(autouse=True)
def _real_endpoints(monkeypatch):
    """Este archivo SÍ prueba la cascada real: conftest la vacía."""
    monkeypatch.setattr(compat_llm, "_ENDPOINTS", _REAL_ENDPOINTS)


@pytest.fixture(autouse=True)
def state_file(tmp_path, monkeypatch):
    """provider_state.json aislado por test (cooldowns)."""
    monkeypatch.setattr(results_base, "_STATE_FILE", tmp_path / "provider_state.json")


def _settings(groq=None, cerebras=None, openrouter=None):
    return SimpleNamespace(
        groq_api_key=groq,
        cerebras_api_key=cerebras,
        openrouter_api_key=openrouter,
    )


def _client(resp=None, exc=None):
    """AsyncClient falso que devuelve `resp` o lanza `exc` en post()."""

    class Fake:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, json=None):
            if exc is not None:
                raise exc
            return resp

    return Fake


def _payload(text: str):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": text}}]},
        request=httpx.Request("POST", "https://x"),
    )


def _error(status: int, body: str = "", headers=None):
    resp = httpx.Response(
        status,
        text=body,
        headers=headers or {},
        request=httpx.Request("POST", "https://x"),
    )
    return httpx.HTTPStatusError("e", request=resp.request, response=resp)


class TestExtractText:
    async def test_sin_keys_devuelve_none(self):
        assert await compat_llm.extract_text("p", settings=_settings()) is None

    async def test_cascada_salta_sin_key_y_corta_al_primer_ok(self, monkeypatch):
        calls = []

        async def fake(ep, key, prompt, purpose=""):
            calls.append(ep.name)
            return "ok" if ep.name == "cerebras" else None

        monkeypatch.setattr(compat_llm, "_call", fake)
        s = _settings(cerebras="k2", openrouter="k3")
        assert await compat_llm.extract_text("p", settings=s) == ("ok", "cerebras")
        # groq sin key ni se intenta; openrouter ni llega al turno.
        assert calls == ["cerebras"]

    async def test_endpoint_aparcado_se_salta_sin_llamar(self, monkeypatch):
        results_base.mark_cooldown("llm-compat-groq", utc_now() + timedelta(hours=1))
        calls = []

        async def fake(ep, key, prompt, purpose=""):
            calls.append(ep.name)
            return "ok"

        monkeypatch.setattr(compat_llm, "_call", fake)
        s = _settings(groq="k1", cerebras="k2")
        assert await compat_llm.extract_text("p", settings=s) == ("ok", "cerebras")
        assert calls == ["cerebras"]  # groq aparcado ni se intenta

    async def test_todos_fallan_devuelve_none(self, monkeypatch):
        async def fake(ep, key, prompt, purpose=""):
            return None

        monkeypatch.setattr(compat_llm, "_call", fake)
        s = _settings(groq="k", cerebras="k", openrouter="k")
        assert await compat_llm.extract_text("p", settings=s) is None

    async def test_ingesta_usa_pool_separado_primero(self, monkeypatch):
        calls = []

        async def fake(ep, key, prompt, purpose=""):
            calls.append(ep.name)
            return "ok"

        monkeypatch.setattr(compat_llm, "_call", fake)
        s = _settings(groq="k1")
        assert await compat_llm.extract_text("p", purpose="ingesta", settings=s) == (
            "ok",
            "groq-ingesta",
        )
        assert calls == ["groq-ingesta"]

    async def test_ingesta_cae_a_cascada_si_pool_aparcado(self, monkeypatch):
        results_base.mark_cooldown(
            "llm-compat-groq-ingesta", utc_now() + timedelta(hours=1)
        )
        calls = []

        async def fake(ep, key, prompt, purpose=""):
            calls.append(ep.name)
            return "ok"

        monkeypatch.setattr(compat_llm, "_call", fake)
        s = _settings(groq="k1")
        assert await compat_llm.extract_text("p", purpose="ingesta", settings=s) == (
            "ok",
            "groq",
        )
        # groq-ingesta ni se intenta (aparcado), groq compartido responde.
        assert calls == ["groq"]

    async def test_otros_propositos_no_usan_pool_ingesta(self, monkeypatch):
        calls = []

        async def fake(ep, key, prompt, purpose=""):
            calls.append(ep.name)
            return "ok"

        monkeypatch.setattr(compat_llm, "_call", fake)
        s = _settings(groq="k1")
        await compat_llm.extract_text("p", purpose="cuotas", settings=s)
        assert calls == ["groq"]


class TestCall:
    async def test_200_devuelve_content(self, monkeypatch):
        monkeypatch.setattr(
            compat_llm.httpx, "AsyncClient", _client(resp=_payload('{"a":1}'))
        )
        ep = compat_llm._ENDPOINTS[0]
        assert await compat_llm._call(ep, "k", "p") == '{"a":1}'

    async def test_429_aparca_con_hint(self, monkeypatch):
        exc = _error(429, body='{"error":{"message":"Please try again in 2h30m"}}')
        monkeypatch.setattr(compat_llm.httpx, "AsyncClient", _client(exc=exc))
        ep = compat_llm._ENDPOINTS[0]
        assert await compat_llm._call(ep, "k", "p") is None
        assert results_base.is_rate_limited("llm-compat-groq")

    async def test_error_http_pausa_corta(self, monkeypatch):
        monkeypatch.setattr(compat_llm.httpx, "AsyncClient", _client(exc=_error(503)))
        ep = compat_llm._ENDPOINTS[1]
        assert await compat_llm._call(ep, "k", "p") is None
        assert results_base.is_rate_limited("llm-compat-cerebras")

    async def test_fallo_red_pausa_corta(self, monkeypatch):
        req = httpx.Request("POST", "https://x")
        monkeypatch.setattr(
            compat_llm.httpx,
            "AsyncClient",
            _client(exc=httpx.ConnectError("down", request=req)),
        )
        ep = compat_llm._ENDPOINTS[2]
        assert await compat_llm._call(ep, "k", "p") is None
        assert results_base.is_rate_limited("llm-compat-openrouter")

    async def test_configured(self):
        assert not compat_llm.configured(_settings())
        assert compat_llm.configured(_settings(groq="k"))

    async def test_has_live_endpoint(self):
        s = _settings(groq="k", cerebras="k")
        assert compat_llm.has_live_endpoint(s)
        # Groq aparcado pero Cerebras libre -> sigue habiendo camino.
        results_base.mark_cooldown("llm-compat-groq", utc_now() + timedelta(hours=1))
        assert compat_llm.has_live_endpoint(s)
        results_base.mark_cooldown(
            "llm-compat-cerebras", utc_now() + timedelta(hours=1)
        )
        assert not compat_llm.has_live_endpoint(s)
