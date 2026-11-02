"""Tests del backoff ante 429 de OpenAI (openai_retry.call_with_retry)."""

from unittest.mock import AsyncMock

import httpx
import pytest
from openai import RateLimitError

from app.services.telegram.openai_retry import _retry_delay, call_with_retry


def _rate_limit(
    message: str = "Rate limit reached. Please try again in 20s.",
) -> RateLimitError:
    return RateLimitError(
        message,
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        ),
        body=None,
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.telegram.openai_retry.asyncio.sleep", sleep)
    return sleep


@pytest.mark.asyncio
async def test_success_sin_reintentos(_no_sleep):
    async def ok():
        return "pick"

    assert await call_with_retry(ok, "test") == "pick"
    _no_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_reintenta_tras_429(_no_sleep):
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _rate_limit()
        return "pick"

    assert await call_with_retry(flaky, "test") == "pick"
    assert calls["n"] == 3
    assert _no_sleep.await_count == 2


@pytest.mark.asyncio
async def test_otros_errores_no_reintentan(_no_sleep):
    async def boom():
        raise ValueError("json roto")

    with pytest.raises(ValueError, match="json roto"):
        await call_with_retry(boom, "test")
    _no_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_agota_reintentos_y_relanza(_no_sleep):
    async def always_429():
        raise _rate_limit()

    with pytest.raises(RateLimitError):
        await call_with_retry(always_429, "test", max_retries=2)
    assert _no_sleep.await_count == 2


def test_retry_delay_usa_hint_del_error():
    exc = _rate_limit("Please try again in 20s.")
    assert _retry_delay(exc, 0) == 25.0

    exc_ms = _rate_limit("Please try again in 800ms.")
    assert _retry_delay(exc_ms, 0) == pytest.approx(5.8)


def test_retry_delay_sin_hint_usa_backoff():
    exc = _rate_limit("rate limit exceeded")
    assert _retry_delay(exc, 0) == 20.0
    assert _retry_delay(exc, 2) == 90.0
