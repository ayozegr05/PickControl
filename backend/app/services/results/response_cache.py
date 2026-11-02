"""Caché persistente de respuestas de providers externos.

`provider_state.json` registra cuota y misses (estado); este módulo guarda
RESPUESTAS reutilizables en `provider_cache.json` para no quemar llamadas
repitiendo navegación que no cambia entre pasadas del verificador:

- `entity_ids`: "provider|nombre normalizado" -> id interno del equipo/
  jugador. Los ids de Sofascore son inmutables: la entrada no caduca.
  Ahorra la llamada `/api/search/{nombre}` por entidad en cada ciclo.
- `event_lists`: clave -> {"captured_at": iso, "events": [...]}.
  Historial de partidos jugados de una entidad: inmutable hacia atrás,
  solo crece con partidos nuevos.

Regla de frescura (`event_list_covers`): la lista capturada en T cubre
cualquier pick cuyo partido empezó antes de `T - _IN_FLIGHT_MARGIN` (un
partido en curso al capturar aún no aparece en `previous`) O cuya fecha
sea <= al evento más reciente de la lista (historial contiguo: si el
partido existiera estaría en ella). En caso contrario hay que refetchear:
el partido pudo jugarse después de la captura.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger("app.results.cache")

_CACHE_FILE = Path(__file__).resolve().parents[3] / "provider_cache.json"
# Un partido que empezó dentro de este margen antes de la captura puede
# seguir en juego y no aparecer aún en la lista -> toca refetch.
_IN_FLIGHT_MARGIN = timedelta(hours=4)
# Las listas sin actividad reciente se podan al guardar para que el
# archivo no crezca sin límite (los pendientes viven ~14 días).
_LIST_MAX_AGE = timedelta(days=30)
# Tope defensivo de eventos por lista persistida.
_LIST_CAP = 200

_CACHE: Optional[dict] = None


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _CACHE = {}
    _CACHE.setdefault("entity_ids", {})
    _CACHE.setdefault("event_lists", {})
    return _CACHE


def _save() -> None:
    cache = _load()
    cutoff = (datetime.now(timezone.utc) - _LIST_MAX_AGE).isoformat()
    cache["event_lists"] = {
        k: v
        for k, v in cache["event_lists"].items()
        if v.get("captured_at", "") >= cutoff
    }
    try:
        _CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "[RESULTS_CACHE] No se pudo guardar provider_cache.json: %s", exc
        )


# --- entity_ids: nombre -> id interno ------------------------------------


def get_entity_id(provider: str, name: str) -> Optional[int]:
    """Id interno del equipo/jugador si ya se resolvió antes; None si no."""
    value = _load()["entity_ids"].get(f"{provider}|{name.strip().lower()}")
    return value if isinstance(value, int) else None


def set_entity_id(provider: str, name: str, entity_id: int) -> None:
    """Persiste nombre -> id para no repetir /search nunca más."""
    _load()["entity_ids"][f"{provider}|{name.strip().lower()}"] = entity_id
    _save()


# --- event_lists: historial de eventos por entidad ------------------------


def get_event_list(key: str) -> Optional[dict]:
    """{"captured_at": iso, "events": [...]} o None si no hay entrada."""
    entry = _load()["event_lists"].get(key)
    if not isinstance(entry, dict) or "events" not in entry:
        return None
    return entry


def set_event_list(key: str, events: list) -> None:
    """Persiste la lista de eventos con su timestamp de captura."""
    _load()["event_lists"][key] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "events": events[:_LIST_CAP],
    }
    _save()


def max_event_start(events: list) -> Optional[datetime]:
    """Mayor `startTimestamp` (naive UTC) de eventos formato Sofascore."""
    best: Optional[datetime] = None
    for event in events:
        ts = event.get("startTimestamp") if isinstance(event, dict) else None
        if not ts:
            continue
        played = datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
        if best is None or played > best:
            best = played
    return best


def event_list_covers(entry: dict, pick_date: datetime) -> bool:
    """True si la lista cacheada basta para un pick de fecha `pick_date`.

    Cubierto si el partido empezó antes de `captured_at - margen` (si
    existía, estaba terminado y listado al capturar) o si su fecha es
    anterior/al evento más reciente de la lista (historial contiguo).
    """
    captured_raw = entry.get("captured_at") or ""
    try:
        captured = datetime.fromisoformat(captured_raw).replace(tzinfo=None)
    except ValueError:
        return False
    boundary = captured - _IN_FLIGHT_MARGIN
    newest = max_event_start(entry.get("events") or [])
    if newest is not None and newest > boundary:
        boundary = newest
    return pick_date <= boundary
