"""Interfaz común para proveedores de cuotas de mercado (odds).

Cada proveedor sabe resolver el evento de un pick en su propia API y
descargar sus mercados de odds, sin conocer nada de `ParsedPick`.
El estado de cuota (rate_limited / missed) se reutiliza de
`app.services.results.base` — misma semántica, mismo archivo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


@dataclass
class EventRef:
    """Evento localizado en un proveedor de odds.

    `event_ext_id` va namespaced por familia de ids
    ("sofascore:17058707"): varios proveedores RapidAPI comparten el
    backend de Sofascore y por tanto el mismo espacio de ids, así un
    evento resuelto por allsportsapi2 lo puede consultar tennisapi1 y
    viceversa.
    """

    event_ext_id: str
    home_team: str
    away_team: str
    start: Optional[datetime] = None


@dataclass
class MarketChoice:
    """Una opción de un mercado tal cual la devuelve el proveedor.

    `cuota`/`cuota_apertura` ya vienen en decimal (conversión desde el
    fraccional en el proveedor). `choice_group` es la línea del
    mercado cuando aplica (over/under "22.5", hándicap "+0.75").
    """

    market_name: str
    choice_name: str
    cuota: Optional[float]
    cuota_apertura: Optional[float] = None
    choice_group: Optional[str] = None
    is_live: bool = False
    suspended: bool = False


class OddsProvider(Protocol):
    """Un proveedor de cuotas de mercado (allsportsapi2, tennisapi1...)."""

    # Nombre para provider_state.json (rate_limited / missed).
    NAME: str
    # Deportes canónicos que sabe resolver ("tenis", "futbol") — mismo
    # criterio que `ResultsProvider.SUPPORTED_SPORTS`.
    SUPPORTED_SPORTS: frozenset

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        """Localiza el evento del pick (equipo/jugador parecido a
        `team_hint` en la fecha dada, tolerancia ±1 día).
        None si no se encuentra o hay error de API."""
        ...

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        """Todas las opciones de todos los mercados del evento.
        None si la llamada falla (no confundir con lista vacía:
        evento sin cuotas publicadas)."""
        ...


_FRACTION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")


def odds_to_decimal(raw: object) -> Optional[float]:
    """Convierte una cuota del proveedor a decimal.

    Acepta fraccional tipo bet365 ("6/5" -> 2.20), decimal en string
    ("2.20") y numérico directo. Devuelve None si no es interpretable
    ("SP", "", None...) — la fila se guarda igualmente con cuota NULL
    para no perder la traza, pero no contamina comparaciones.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    text = str(raw).strip()
    if not text:
        return None
    m = _FRACTION.match(text)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        if den == 0:
            return None
        return round(1.0 + num / den, 4)
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None
