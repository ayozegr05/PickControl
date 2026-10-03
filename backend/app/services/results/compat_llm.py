"""Extractores texto→JSON gratuitos vía API OpenAI-compatible.

Groq, Cerebras y OpenRouter hablan el mismo `/chat/completions`: un
solo cliente, la cascada en `_ENDPOINTS`, cada endpoint se activa con
su key en Settings. La cuota de cada uno se aparca con `mark_cooldown`
usando el hint del 429 (`retry_hint_seconds`) — persistido en
provider_state.json, sobrevive a reinicios del contenedor.

Solo extracción: ninguna herramienta ni búsqueda web — eso lo hace el
fetch propio/FlareSolverr antes de llamar aquí. Quien llama valida el
JSON contra el texto de la página (literal + map_pick_choices), así
que un endpoint puede responder mal sin corromper datos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

import httpx

from app.core.config import Settings, get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.services.results.base import (
    is_rate_limited,
    mark_cooldown,
    retry_hint_seconds,
)

logger = get_logger("app.results.compat_llm")

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# Respuesta corta (JSON de una cuota/estado) pero con margen para
# respuestas multi-mercado; los free tiers no cobran por token.
_MAX_TOKENS = 2048
_429_FALLBACK = timedelta(minutes=20)
_ERROR_COOLDOWN = timedelta(minutes=5)
_MARK = "llm-compat"


@dataclass(frozen=True)
class Endpoint:
    """Un endpoint OpenAI-compatible con su key en Settings."""

    name: str
    base_url: str
    model: str
    env_key: str


# Cascada en orden de calidad/cuota: Groq 70B gratis y rapidísimo;
# Cerebras y OpenRouter :free como refuerzo; Gemini queda fuera — es
# el paracaídas que maneja quien llama (su cuota es la más pequeña).
_ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        "groq",
        "https://api.groq.com/openai/v1",
        "llama-3.3-70b-versatile",
        "groq_api_key",
    ),
    Endpoint(
        "cerebras",
        "https://api.cerebras.ai/v1",
        "llama-3.3-70b",
        "cerebras_api_key",
    ),
    Endpoint(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "meta-llama/llama-3.3-70b-instruct:free",
        "openrouter_api_key",
    ),
)


def _mark(endpoint: Endpoint) -> str:
    return f"{_MARK}-{endpoint.name}"


def configured(settings: Optional[Settings] = None) -> bool:
    """True si algún endpoint de la cascada tiene key — check gratis
    para que los loops puedan mostrar el estado sin gastar nada."""
    s = settings or get_settings()
    return any(getattr(s, ep.env_key, None) for ep in _ENDPOINTS)


def has_live_endpoint(settings: Optional[Settings] = None) -> bool:
    """True si algún endpoint configurado NO está aparcado por
    cooldown — un camino de extracción vivo aunque Gemini esté
    seco (los loops no deben salir si esto da True)."""
    s = settings or get_settings()
    return any(
        getattr(s, ep.env_key, None) and not is_rate_limited(_mark(ep))
        for ep in _ENDPOINTS
    )


async def extract_text(
    prompt: str, *, settings: Optional[Settings] = None
) -> Optional[str]:
    """Texto de la primera respuesta útil de la cascada, None si
    ningún endpoint configurado/vivo responde — el fallback (Gemini)
    corre por cuenta de quien llama."""
    s = settings or get_settings()
    for endpoint in _ENDPOINTS:
        key = getattr(s, endpoint.env_key, None)
        if not key or is_rate_limited(_mark(endpoint)):
            continue
        text = await _call(endpoint, key, prompt)
        if text:
            return text
    return None


async def _call(endpoint: Endpoint, key: str, prompt: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{endpoint.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": endpoint.model,
                    "temperature": 0,
                    "max_tokens": _MAX_TOKENS,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            secs = retry_hint_seconds(exc.response)
            mark_cooldown(
                _mark(endpoint),
                utc_now() + (timedelta(seconds=secs) if secs else _429_FALLBACK),
            )
            logger.warning(
                "[LLM] %s 429 — aparcado %s",
                endpoint.name,
                f"{secs:.0f}s" if secs else "20m",
            )
        else:
            # 4xx/5xx no-cuota: pausa corta para no martillear un
            # endpoint caído, pero reintentable pronto.
            mark_cooldown(_mark(endpoint), utc_now() + _ERROR_COOLDOWN)
            logger.warning(
                "[LLM] %s HTTP %s — pausa %dm",
                endpoint.name,
                exc.response.status_code,
                int(_ERROR_COOLDOWN.total_seconds() / 60),
            )
        return None
    except httpx.HTTPError as exc:
        mark_cooldown(_mark(endpoint), utc_now() + _ERROR_COOLDOWN)
        logger.warning("[LLM] %s fallo red: %s — pausa 5m", endpoint.name, exc)
        return None
    try:
        choices = resp.json().get("choices") or []
        content: Any = choices[0].get("message", {}).get("content")
    except (ValueError, IndexError, AttributeError) as exc:
        logger.warning("[LLM] %s respuesta ilegible: %s", endpoint.name, exc)
        return None
    if not content:
        logger.info("[LLM] %s devolvió vacío", endpoint.name)
        return None
    logger.info("[LLM] %s extrajo %d chars", endpoint.name, len(str(content)))
    return str(content)
