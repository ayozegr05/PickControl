"""Resultados de Fórmula 1 vía Jolpica (gratis, sin key).

Jolpica-F1 es el sucesor comunitario de Ergast: API REST con el
calendario completo desde 1950 y resultados de carrera oficiales
(posición, piloto y `status` — Finished / Lapped / Retired / DNS...).

Segundo provider determinista del deporte `automovilismo`, detrás de
ESPN (`espn_f1.py`): hereda TODO el matching de GPs (tokens de
localización + aliases + tolerancia de fecha) y solo reimplementa los
hooks de fetch/parseo.

Diferencia de semántica con ESPN: aquí "clasificado" =
`positionText` numérico (la clasificación oficial de FIA — incluye
pilotos doblados "Lapped" y excluye R=retirado, W=DNS/withdrawn,
D=DSQ, N=NC, E=excluded). ESPN llega a los mismos por estado, pero a
veces marca STATUS_CLASSIFIED a un DNS — cuando discrepan, Jolpica
es la lectura oficial.

Endpoints usados:
- `GET /ergast/f1/{año}/races/`           — calendario del año (1 llamada, cacheada por instancia)
- `GET /ergast/f1/{año}/{ronda}/results/` — resultado oficial de la carrera
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.core.logging import get_logger
from app.services.results.espn_f1 import EspnF1Provider

logger = get_logger("app.results.jolpica_f1")

_API = "https://api.jolpi.ca/ergast/f1"


def _driver_name(result: dict) -> str:
    """ "Max Verstappen" a partir del Driver de Ergast."""
    driver = result.get("Driver") or {}
    return f"{driver.get('givenName') or ''} {driver.get('familyName') or ''}".strip()


class JolpicaF1Provider(EspnF1Provider):
    """Segundo provider F1: calendario + resultados oficiales Ergast."""

    NAME = "jolpica"

    def __init__(self) -> None:
        self._season_cache: dict[int, list[dict]] = {}
        self._results_cache: dict[tuple[str, str], Optional[list[dict]]] = {}

    # --- Fetch -----------------------------------------------------------

    async def _season_races(self, year: int) -> list[dict]:
        """Calendario completo del año (cacheado: 1 llamada por año)."""
        if year not in self._season_cache:
            data = await self._get_json(f"{_API}/{year}/races/", "f1", {})
            table = ((data or {}).get("MRData") or {}).get("RaceTable") or {}
            self._season_cache[year] = table.get("Races") or []
        return self._season_cache[year]

    async def _fetch_races(self, year: int, month: int) -> list[dict]:
        """Carreras del mes del calendario Ergast."""
        return [
            race
            for race in await self._season_races(year)
            if (d := self._race_date(race)) is not None and d.month == month
        ]

    async def _results(self, race: dict) -> Optional[list[dict]]:
        """Resultados oficiales de la carrera (cacheados por ronda)."""
        season, rnd = race.get("season"), race.get("round")
        if not season or not rnd:
            return None
        key = (str(season), str(rnd))
        if key not in self._results_cache:
            data = await self._get_json(f"{_API}/{season}/{rnd}/results/", "f1", {})
            table = ((data or {}).get("MRData") or {}).get("RaceTable") or {}
            races = table.get("Races") or []
            self._results_cache[key] = (
                (races[0].get("Results") or []) if races else None
            )
        return self._results_cache[key]

    # --- Hooks sobre el matching heredado ---------------------------------

    def _race_name(self, race: dict) -> str:
        """ "Italian Grand Prix Monza" — la localidad ayuda a los aliases
        de circuito (monza→italia, imola...)."""
        location = ((race.get("Circuit") or {}).get("Location") or {}).get(
            "locality"
        ) or ""
        return f"{race.get('raceName') or ''} {location}".strip()

    def _race_date(self, race: dict) -> Optional[datetime]:
        raw_date = race.get("date")
        if not raw_date:
            return None
        raw_time = (race.get("time") or "00:00:00Z").replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(f"{raw_date}T{raw_time}")
        except ValueError:
            return None
        # Misma convención que `_competition_date` de espn.py: UTC naive.
        return parsed.replace(tzinfo=None)

    async def _race_completed(self, race: dict) -> bool:
        """La carrera consta como disputada solo si hay resultados."""
        return bool(await self._results(race))

    def _race_postponed_status(self, race: dict) -> Optional[str]:
        """Ergast solo lista carreras celebradas — no expone aplazados."""
        return None

    async def _race_result(self, race: dict) -> Optional[tuple[str, str]]:
        """(ganador, segundo) según la clasificación oficial."""
        results = await self._results(race)
        if not results or len(results) < 2:
            return None
        ordered = sorted(results, key=lambda r: int(r.get("position") or 999))
        return _driver_name(ordered[0]), _driver_name(ordered[1])

    async def _classified_count(self, race: dict) -> Optional[int]:
        """Clasificados oficiales = `positionText` numérico.

        Excluye R (retirado), W (DNS/withdrawn), D (DSQ), N (NC) y E
        (excluded). None si la carrera no tiene resultados — nunca se
        inventa un conteo parcial.
        """
        results = await self._results(race)
        if not results:
            return None
        return sum(1 for r in results if str(r.get("positionText") or "").isdigit())
