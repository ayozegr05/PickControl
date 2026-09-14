"""Interfaz común para proveedores de resultados deportivos.

Cada proveedor sabe consultar su propia API y devolver el resultado de
un partido concreto, sin conocer nada de `ParsedPick` ni de picks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


@dataclass
class MatchResult:
    """Resultado final de un partido, ya normalizado entre proveedores."""

    home_team: str
    away_team: str
    home_score: int
    away_score: int


class ResultsProvider(Protocol):
    """Un proveedor de resultados deportivos (football-data.org, API-Football...)."""

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Busca un partido finalizado en la fecha dada que involucre a
        un equipo parecido a `team_hint`. Devuelve None si no lo
        encuentra (partido no cubierto, aún no jugado, o error de API)."""
        ...
