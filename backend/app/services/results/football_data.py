"""Proveedor de resultados usando football-data.org.

Cubre las ligas top (La Liga, Champions, Premier, Serie A, Bundesliga,
Ligue 1...) de forma gratuita. Buena primera fuente por fiabilidad y
simplicidad, pero no cubre ligas menores ni otros deportes.
"""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import MatchResult

logger = get_logger("app.results.football_data")

_BASE_URL = "https://api.football-data.org/v4"
_MIN_TEAM_SIMILARITY = 0.6


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class FootballDataProvider:
    """Consulta football-data.org por partidos finalizados de una fecha."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        date_str = date.strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                response = await client.get(
                    f"{_BASE_URL}/matches",
                    params={"dateFrom": date_str, "dateTo": date_str},
                    headers={"X-Auth-Token": self._api_key},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("[football-data.org] Error de API: %s", exc)
                return None

        matches = response.json().get("matches", [])

        best_match = None
        best_score = 0.0
        for match in matches:
            if match.get("status") != "FINISHED":
                continue
            home = match["homeTeam"]["name"]
            away = match["awayTeam"]["name"]
            score = max(_similar(team_hint, home), _similar(team_hint, away))
            if score > best_score:
                best_score = score
                best_match = (match, home, away)

        if not best_match or best_score < _MIN_TEAM_SIMILARITY:
            return None

        match, home, away = best_match
        full_time = match.get("score", {}).get("fullTime", {})
        home_score = full_time.get("home")
        away_score = full_time.get("away")
        if home_score is None or away_score is None:
            return None

        return MatchResult(
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
        )
