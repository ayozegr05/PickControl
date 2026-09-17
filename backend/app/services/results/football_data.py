"""Proveedor de resultados usando football-data.org.

Cubre las ligas top (La Liga, Champions, Premier, Serie A, Bundesliga,
Ligue 1...) de forma gratuita. Buena primera fuente por fiabilidad y
simplicidad, pero no cubre ligas menores ni otros deportes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import MatchResult, match_score

logger = get_logger("app.results.football_data")

_BASE_URL = "https://api.football-data.org/v4"
_MIN_TEAM_SIMILARITY = 0.6
# La fecha del evento a veces es solo una aproximación (día en que el
# tipster publicó el pick, no el día exacto del partido), así que
# buscamos en una pequeña ventana alrededor en vez de un único día.
_DATE_WINDOW = timedelta(days=1)


class FootballDataProvider:
    """Consulta football-data.org por partidos finalizados cerca de una fecha."""

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        # Caché de partidos por ventana de fechas (vive solo durante una
        # pasada del verificador): picks del mismo día comparten la misma
        # respuesta en vez de repetir la llamada.
        self._matches_cache: dict[tuple[str, str], list] = {}

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        date_from = (date - _DATE_WINDOW).strftime("%Y-%m-%d")
        date_to = (date + _DATE_WINDOW).strftime("%Y-%m-%d")
        cache_key = (date_from, date_to)

        if cache_key in self._matches_cache:
            matches = self._matches_cache[cache_key]
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                try:
                    response = await client.get(
                        f"{_BASE_URL}/matches",
                        params={"dateFrom": date_from, "dateTo": date_to},
                        headers={"X-Auth-Token": self._api_key},
                    )
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("[football-data.org] Error de API: %s", exc)
                    return None

            matches = response.json().get("matches", [])
            self._matches_cache[cache_key] = matches

        best_match = None
        best_score = 0.0
        best_teams: tuple[str, str] | None = None
        ambiguous = False
        for match in matches:
            if match.get("status") != "FINISHED":
                continue
            home = match["homeTeam"]["name"]
            away = match["awayTeam"]["name"]
            score = match_score(team_hint, home, away)
            if score > best_score:
                best_score = score
                best_match = (match, home, away)
                best_teams = (home, away)
                ambiguous = False
            elif (
                score == best_score
                and score >= _MIN_TEAM_SIMILARITY
                and (home, away) != best_teams
            ):
                # Dos fixtures distintos empatan (p. ej. hint "Madrid"
                # con Real Madrid y Atlético jugando el mismo día):
                # mejor no adivinar.
                ambiguous = True

        if not best_match or best_score < _MIN_TEAM_SIMILARITY or ambiguous:
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
