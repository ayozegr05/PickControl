"""Proveedor de resultados usando API-Football.

Cubre muchas más ligas (incluidas menores) que football-data.org, pero
con un límite gratuito más bajo (100 peticiones/día). Se usa como
fallback cuando football-data.org no encuentra el partido.

Soporta dos formas de acceso, según cómo te hayas registrado:
- Directo en api-football.com (recomendado, más simple): host
  "v3.football.api-sports.io", autenticación con el header
  "x-apisports-key".
- Vía RapidAPI (marketplace): host "api-football-v1.p.rapidapi.com",
  autenticación con "X-RapidAPI-Key" / "X-RapidAPI-Host".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import MatchResult, MatchStats, match_score

logger = get_logger("app.results.api_football")

_MIN_TEAM_SIMILARITY = 0.6
# La fecha del evento a veces es solo una aproximación (día en que el
# tipster publicó el pick, no el día exacto del partido). El endpoint de
# fixtures solo acepta un día por petición, así que probamos el día
# indicado y el anterior/siguiente (3 peticiones en total).
_DATE_OFFSETS = (0, -1, 1)


def _stat_int(value) -> Optional[int]:
    """Normaliza un valor de estadística: int, str numérico o None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


class ApiFootballProvider:
    """Consulta API-Football por partidos finalizados de una fecha."""

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, api_key: str, api_host: str) -> None:
        self._api_key = api_key
        self._api_host = api_host
        # Caché de fixtures por fecha (vive solo durante una pasada del
        # verificador): varios picks del mismo día reutilizan la misma
        # respuesta en vez de repetir la llamada. El plan gratuito da
        # 100 req/día y antes se repetía la descarga por cada pick.
        self._fixtures_cache: dict[str, list] = {}
        # Caché de estadísticas por fixture id: varios picks del mismo
        # partido (córners + tarjetas + tiros...) comparten la llamada.
        self._stats_cache: dict[int, list] = {}

    def _headers(self) -> dict[str, str]:
        if "rapidapi" in self._api_host:
            return {
                "X-RapidAPI-Key": self._api_key,
                "X-RapidAPI-Host": self._api_host,
            }
        # Acceso directo en api-football.com (api-sports.io).
        return {"x-apisports-key": self._api_key}

    def _fixtures_url(self) -> str:
        # El host de RapidAPI ("api-football-v1.p.rapidapi.com") no
        # incluye la versión de la API en el propio host, así que hay
        # que añadir "/v3" al path. El host directo de api-sports.io
        # ("v3.football.api-sports.io") ya la incluye en el subdominio.
        if "rapidapi" in self._api_host:
            return f"https://{self._api_host}/v3/fixtures"
        return f"https://{self._api_host}/fixtures"

    def _statistics_url(self) -> str:
        if "rapidapi" in self._api_host:
            return f"https://{self._api_host}/v3/fixtures/statistics"
        return f"https://{self._api_host}/fixtures/statistics"

    async def _fetch_fixtures(self, client: httpx.AsyncClient, date_str: str) -> list:
        if date_str in self._fixtures_cache:
            return self._fixtures_cache[date_str]
        try:
            response = await client.get(
                self._fixtures_url(),
                params={"date": date_str},
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("[API-Football] Error de API (%s): %s", date_str, exc)
            return []
        fixtures = response.json().get("response", [])
        self._fixtures_cache[date_str] = fixtures
        return fixtures

    async def _fetch_statistics(
        self, client: httpx.AsyncClient, fixture_id: int
    ) -> list:
        if fixture_id in self._stats_cache:
            return self._stats_cache[fixture_id]
        try:
            response = await client.get(
                self._statistics_url(),
                params={"fixture": fixture_id},
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "[API-Football] Error de API stats (fixture %s): %s",
                fixture_id,
                exc,
            )
            return []
        stats = response.json().get("response", [])
        self._stats_cache[fixture_id] = stats
        return stats

    async def _find_fixture(self, date: datetime, team_hint: str) -> Optional[dict]:
        """El fixture FT que mejor casa con el hint, o None."""
        best_match = None
        best_score = 0.0
        best_teams: tuple[str, str] | None = None
        ambiguous = False

        async with httpx.AsyncClient(timeout=15) as client:
            for offset in _DATE_OFFSETS:
                date_str = (date + timedelta(days=offset)).strftime("%Y-%m-%d")
                fixtures = await self._fetch_fixtures(client, date_str)

                for fixture in fixtures:
                    status_short = (
                        fixture.get("fixture", {}).get("status", {}).get("short")
                    )
                    if status_short != "FT":
                        continue
                    home = fixture["teams"]["home"]["name"]
                    away = fixture["teams"]["away"]["name"]
                    score = match_score(team_hint, home, away)
                    if score > best_score:
                        best_score = score
                        best_match = fixture
                        best_teams = (home, away)
                        ambiguous = False
                    elif (
                        score == best_score
                        and score >= _MIN_TEAM_SIMILARITY
                        and (home, away) != best_teams
                    ):
                        # Dos fixtures distintos empatan (p. ej. hint
                        # "Madrid" con Real Madrid y Atlético jugando en
                        # la ventana de fechas): mejor no adivinar.
                        ambiguous = True

        if not best_match or best_score < _MIN_TEAM_SIMILARITY or ambiguous:
            return None
        return best_match

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        fixture = await self._find_fixture(date, team_hint)
        if fixture is None:
            return None

        goals = fixture.get("goals", {})
        home_score = goals.get("home")
        away_score = goals.get("away")
        if home_score is None or away_score is None:
            return None

        return MatchResult(
            home_team=fixture["teams"]["home"]["name"],
            away_team=fixture["teams"]["away"]["name"],
            home_score=home_score,
            away_score=away_score,
        )

    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        """Estadísticas del partido (córners, tarjetas, tiros...).

        Mismo matching que `find_match`, más una llamada a
        `/fixtures/statistics` por el id del fixture (cacheada: varios
        picks del mismo partido comparten la respuesta).
        """
        fixture = await self._find_fixture(date, team_hint)
        if fixture is None:
            return None
        fixture_id = fixture.get("fixture", {}).get("id")
        home_id = fixture["teams"]["home"].get("id")
        if fixture_id is None or home_id is None:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            raw_stats = await self._fetch_statistics(client, fixture_id)

        home_values: dict[str, int] = {}
        away_values: dict[str, int] = {}
        for entry in raw_stats:
            target = (
                home_values
                if entry.get("team", {}).get("id") == home_id
                else away_values
            )
            for item in entry.get("statistics", []):
                value = _stat_int(item.get("value"))
                if value is not None:
                    target[item.get("type")] = value

        if not home_values and not away_values:
            return None

        values = {
            key: (home_values.get(key, 0), away_values.get(key, 0))
            for key in home_values.keys() | away_values.keys()
        }
        return MatchStats(
            home_team=fixture["teams"]["home"]["name"],
            away_team=fixture["teams"]["away"]["name"],
            values=values,
        )
