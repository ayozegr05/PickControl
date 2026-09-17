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
from difflib import SequenceMatcher
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import MatchResult

logger = get_logger("app.results.api_football")

_MIN_TEAM_SIMILARITY = 0.6
# La fecha del evento a veces es solo una aproximación (día en que el
# tipster publicó el pick, no el día exacto del partido). El endpoint de
# fixtures solo acepta un día por petición, así que probamos el día
# indicado y el anterior/siguiente (3 peticiones en total).
_DATE_OFFSETS = (0, -1, 1)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


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

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        best_match = None
        best_score = 0.0

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
                    score = max(_similar(team_hint, home), _similar(team_hint, away))
                    if score > best_score:
                        best_score = score
                        best_match = (fixture, home, away)

        if not best_match or best_score < _MIN_TEAM_SIMILARITY:
            return None

        fixture, home, away = best_match
        goals = fixture.get("goals", {})
        home_score = goals.get("home")
        away_score = goals.get("away")
        if home_score is None or away_score is None:
            return None

        return MatchResult(
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
        )
