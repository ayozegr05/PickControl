"""Reintento con backoff ante errores 429 (TPM/RPM) de la API de OpenAI.

El SDK de OpenAI solo reintenta ~2 veces con esperas de ~0.2s, insuficiente
para la ventana de ~60s del límite de tokens por minuto: cuando se agota la
ráfaga (p. ej. un catch-up con OCR + extracción por mensaje), la llamada
termina fallando y el raw queda `processed=False`. Aquí se aplican esperas
largas guiadas por el hint "Please try again in X" del propio error.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

from openai import RateLimitError

from app.core.logging import get_logger

logger = get_logger("app.telegram.openai")

T = TypeVar("T")

# Espera mínima cuando el error no trae hint utilizable. La ventana de TPM
# se libera por minuto, así que los valores por defecto son largos.
_BACKOFF_SECONDS = (20.0, 45.0, 90.0)
_MAX_RETRIES = len(_BACKOFF_SECONDS)
# Margen sobre el hint del error: OpenAI indica cuándo se libera cuota pero
# otras llamadas concurrentes pueden consumirla justo antes del reintento.
_HINT_MARGIN_SECONDS = 5.0

_RETRY_HINT = re.compile(r"try again in ([\d.]+)\s*(ms|s)\b", re.IGNORECASE)


def _retry_delay(exc: RateLimitError, attempt: int) -> float:
    """Segundos a esperar: hint del error + margen, o backoff por intento."""
    fallback = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
    match = _RETRY_HINT.search(str(exc))
    if match:
        value = float(match.group(1))
        hinted = value / 1000 if match.group(2).lower() == "ms" else value
        return max(hinted + _HINT_MARGIN_SECONDS, 5.0)
    return fallback


async def call_with_retry(
    factory: Callable[[], Awaitable[T]],
    description: str,
    *,
    max_retries: int = _MAX_RETRIES,
) -> T:
    """Ejecuta `factory()` reintentando solo ante RateLimitError (429).

    Cualquier otra excepción se propaga sin reintentar. Si se agotan los
    reintentos, se relanza el último 429 para que el llamador decida
    (el processor deja el raw como reintentable).
    """
    for attempt in range(max_retries + 1):
        try:
            return await factory()
        except RateLimitError as exc:
            if attempt == max_retries:
                raise
            delay = _retry_delay(exc, attempt)
            logger.warning(
                "OpenAI 429 en %s (reintento %d/%d en %.0fs)",
                description,
                attempt + 1,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")
