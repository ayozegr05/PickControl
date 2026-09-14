"""Proveedor de resultados usando API-Football (vía RapidAPI).

Cubre muchas más ligas (incluidas menores) que football-data.org, pero
con un límite gratuito más bajo (100 peticiones/día). Se usa como
fallback cuando football-data.org no encuentra el partido.
"""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import MatchResult

logger = get_logger("app.results.api_football")

_MIN_TEAM_SIMILARITY = 0.6


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class ApiFootballProvider:
    """Consulta API-Football (RapidAPI) por partidos finalizados de una fecha."""

    def __init__(self, api_key: str, api_host: str) -> None:
        self._api_key = api_key
        self._api_host = api_host

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        date_str = date.strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                response = await client.get(
                    f"https://{self._api_host}/v3/fixtures",
                    params={"date": date_str},
                    headers={
                        "X-RapidAPI-Key": self._api_key,
                        "X-RapidAPI-Host": self._api_host,
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("[API-Football] Error de API: %s", exc)
                return None

        fixtures = response.json().get("response", [])

        best_match = None
        best_score = 0.0
        for fixture in fixtures:
            status_short = fixture.get("fixture", {}).get("status", {}).get("short")
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
