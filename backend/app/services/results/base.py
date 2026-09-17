"""Interfaz común para proveedores de resultados deportivos.

Cada proveedor sabe consultar su propia API y devolver el resultado de
un partido concreto, sin conocer nada de `ParsedPick` ni de picks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Protocol

import httpx

from app.core.logging import get_logger

logger = get_logger("app.results.base")


@dataclass
class MatchResult:
    """Resultado final de un partido, ya normalizado entre proveedores."""

    home_team: str
    away_team: str
    home_score: int
    away_score: int


class ResultsProvider(Protocol):
    """Un proveedor de resultados deportivos (football-data.org, API-Football...)."""

    # Deportes canónicos (minúsculas, sin tilde) que el proveedor sabe
    # resolver: "futbol", "tenis", "baloncesto"... El verificador solo
    # consulta a un proveedor los picks de los deportes que cubre — un
    # pick de tenis nunca debe buscarse en una API de fútbol.
    SUPPORTED_SPORTS: frozenset

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Busca un partido finalizado en la fecha dada que involucre a
        un equipo parecido a `team_hint`. Devuelve None si no lo
        encuentra (partido no cubierto, aún no jugado, o error de API)."""
        ...


# --- Estado persistente de cuotas y fallos de los proveedores ---------
#
# Los proveedores de pago/cuota (RapidAPI: ~50 req/día) guardan aquí dos
# cosas en un JSON junto al backend para sobrevivir a los reinicios de
# uvicorn (--reload reinicia en cada guardado y resetearía la memoria):
#
# - "rate_limited": proveedor -> fecha ISO en que agotó su cuota
#   (403/429). Ese día se salta sin ni siquiera llamar; al cambiar de
#   día vuelve a intentarse.
# - "missed": clave de búsqueda (p. ej. "2026-09-15|alcaraz" en
#   proveedor) -> fecha ISO en que se comprobó sin resultado. Una
#   búsqueda que falló no se repite durante _MISSED_TTL_DAYS: los
#   resultados históricos no aparecen días después, así que reintentar
#   solo quema cuota.
#
# Sin esto, cada reinicio del backend volvía a gastar la cuota diaria
# reintentando picks irresolubles hasta el 429.
_STATE_FILE = Path(__file__).resolve().parents[3] / "provider_state.json"
_MISSED_TTL = timedelta(days=15)

_STATE: dict[str, dict[str, str]] | None = None


def _load_state() -> dict[str, dict[str, str]]:
    global _STATE
    if _STATE is None:
        try:
            _STATE = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _STATE = {}
        _STATE.setdefault("rate_limited", {})
        _STATE.setdefault("missed", {})
    return _STATE


def _save_state() -> None:
    today = date_type.today().isoformat()
    cutoff = (date_type.today() - _MISSED_TTL).isoformat()
    state = _load_state()
    # Podar entradas viejas para que el archivo no crezca sin límite.
    state["rate_limited"] = {
        k: v for k, v in state["rate_limited"].items() if v >= today
    }
    state["missed"] = {k: v for k, v in state["missed"].items() if v >= cutoff}
    try:
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        logger.warning("[RESULTS] No se pudo guardar provider_state.json: %s", exc)


def mark_rate_limited(provider_name: str) -> None:
    """Marca un proveedor como sin cuota por el resto del día."""
    _load_state()["rate_limited"][provider_name] = date_type.today().isoformat()
    _save_state()


def is_rate_limited(provider_name: str) -> bool:
    """True si el proveedor agotó su cuota hoy (429/403) y hay que saltarlo."""
    return (
        _load_state()["rate_limited"].get(provider_name)
        == date_type.today().isoformat()
    )


def mark_missed(key: str) -> None:
    """Recuerda que `key` ya se buscó sin resultado (ver _MISSED_TTL)."""
    _load_state()["missed"][key] = date_type.today().isoformat()
    _save_state()


def is_missed(key: str) -> bool:
    """True si `key` ya se buscó sin resultado hace menos de _MISSED_TTL."""
    checked_on = _load_state()["missed"].get(key)
    if checked_on is None:
        return False
    return checked_on >= (date_type.today() - _MISSED_TTL).isoformat()


def rate_limit_from(exc: BaseException) -> bool:
    """True si la excepción HTTP es un problema de cuota/acceso (403, 429)."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
        403,
        429,
    )
