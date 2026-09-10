"""Helpers de fecha/hora.

Las columnas de la BD son `TIMESTAMP WITHOUT TIME ZONE` (asyncpg rechaza
datetimes tz-aware ahí), así que el estándar interno es "UTC naive":
hora UTC sin `tzinfo`. `datetime.utcnow()` está deprecado desde Python
3.12; `utc_now()` es su reemplazo equivalente.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Devuelve la hora actual en UTC sin tzinfo (naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
