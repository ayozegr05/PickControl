"""Rate limiting en memoria para endpoints sensibles (login/register).

Nota: es una implementación mínima. En producción con varias réplicas del
backend conviene usar Redis (p. ej. `fastapi-limiter` o `slowapi` con
Redis) para compartir los contadores entre instancias.
"""

from collections import defaultdict, deque
from time import monotonic

from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("app.rate_limit")

_WINDOW_SECONDS = 60
_DEFAULT_LIMIT = 5

_store: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(request: Request, limit: int = _DEFAULT_LIMIT) -> None:
    """Rechaza la petición si la IP ha superado `limit` peticiones en 1 minuto.

    Si no se puede determinar la IP (tests, proxies sin X-Forwarded-For),
    se omite el control para no bloquear llamadas legítimas.
    """
    settings = get_settings()
    if settings.node_env in ("development", "test"):
        return

    if request.client is None:
        return

    ip = request.client.host
    now = monotonic()
    window_start = now - _WINDOW_SECONDS

    queue = _store[ip]
    while queue and queue[0] < window_start:
        queue.popleft()

    if len(queue) >= limit:
        logger.warning("Rate limit exceeded for IP %s", ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiadas peticiones. Inténtalo de nuevo en un minuto.",
        )

    queue.append(now)
