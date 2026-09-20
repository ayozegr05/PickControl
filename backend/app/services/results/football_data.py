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
from app.services.results.base import MatchResult, MatchState, match_score

logger = get_logger("app.results.football_data")

_BASE_URL = "https://api.football-data.org/v4"
_MIN_TEAM_SIMILARITY = 0.6
# La fecha del evento a veces es solo una aproximación (día en que el
# tipster publicó el pick, no el día exacto del partido), así que
# buscamos en una pequeña ventana alrededor en vez de un único día.
_DATE_WINDOW = timedelta(days=1)

# Estados que anulan la apuesta si el partido no se reprograma a tiempo:
# aplazado y cancelado. SUSPENDED/AWARDED quedan fuera a propósito:
# parado a mitad con marcador o decisión administrativa, hay mercados
# que la casa sigue pagando — esos picks se quedan pendientes.
_FINISHED_STATUS = frozenset({"FINISHED"})
_VOIDED_STATUSES = frozenset({"POSTPONED", "CANCELLED"})
_VOIDED_STATUS_NAMES = {"POSTPONED": "postponed", "CANCELLED": "cancelled"}


class FootballDataProvider:
    """Consulta football-data.org por partidos finalizados cerca de una fecha."""

    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        # Caché de partidos por ventana de fechas (vive solo durante una
        # pasada del verificador): picks del mismo día comparten la misma
        # respuesta en vez de repetir la llamada.
        self._matches_cache: dict[tuple[str, str], list] = {}

    async def _fetch_matches(self, date: datetime) -> list:
        date_from = (date - _DATE_WINDOW).strftime("%Y-%m-%d")
        date_to = (date + _DATE_WINDOW).strftime("%Y-%m-%d")
        cache_key = (date_from, date_to)

        if cache_key in self._matches_cache:
            return self._matches_cache[cache_key]

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
                return []

        matches = response.json().get("matches", [])
        self._matches_cache[cache_key] = matches
        return matches

    def _best_match(
        self, matches: list, team_hint: str, statuses: frozenset
    ) -> Optional[tuple[dict, str, str]]:
        """El partido con alguno de los estados dados que mejor casa
        con el hint; None si ninguno supera el umbral o hay empate
        ambiguo entre dos partidos distintos."""
        best_match = None
        best_score = 0.0
        best_teams: tuple[str, str] | None = None
        ambiguous = False
        for match in matches:
            if match.get("status") not in statuses:
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
        return best_match

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        matches = await self._fetch_matches(date)
        found = self._best_match(matches, team_hint, _FINISHED_STATUS)
        if found is None:
            return None

        match, home, away = found
        score = match.get("score", {})
        full_time = score.get("fullTime", {})
        home_score = full_time.get("home")
        away_score = full_time.get("away")
        if home_score is None or away_score is None:
            return None
        half_time = score.get("halfTime", {})

        return MatchResult(
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            ht_home_score=half_time.get("home"),
            ht_away_score=half_time.get("away"),
        )

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """Partido aplazado/cancelado que casa con el hint, o None.

        Sirve para anular el pick cuando el partido no se disputó en
        la ventana que da la casa. Reutiliza la misma respuesta
        cacheada que `find_match` — no cuesta llamadas extra.
        """
        matches = await self._fetch_matches(date)
        found = self._best_match(matches, team_hint, _VOIDED_STATUSES)
        if found is None:
            return None
        match, home, away = found
        return MatchState(
            home_team=home,
            away_team=away,
            status=_VOIDED_STATUS_NAMES.get(match.get("status"), "postponed"),
        )
