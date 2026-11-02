"""Resultados de hockey NHL vía la API pública oficial de la NHL.

`api-web.nhle.com` es el backend JSON que consume nhl.com: gratis,
sin API key ni Cloudflare — verificado en vivo (2026-11-02). Cualquier
wrapper de RapidAPI ("nhl-api5" y similares) es un proxy de pago
sobre estos mismos datos.

Endpoint usado:
- `GET /v1/score/{YYYY-MM-DD}` — todos los partidos del día con
  marcador y estado (`gameState`: FUT/PRE/LIVE/CRIT/OVER/OFF/FINAL;
  `gameScheduleState`: "PPD" cuando el partido se aplaza).

Los nombres vienen como apodo + ciudad separados: `name.default`
="Bruins" y `placeName.default`="Boston" cuando está. El hint del
tipster ("Detroit Red Wings") ya contiene el apodo, así que basta
componer "Boston Bruins" cuando haya `placeName` y el apodo a secas
cuando no — `match_score` resuelve el subset de tokens en ambos casos.

Solo cubre NHL: la KHL (Spartak, Severstal...) la resuelve el mirror
Sofascore con `SUPPORTED_SPORTS={"hockey"}` — esta API no la lista.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    MatchResult,
    MatchState,
    count_provider_call,
    is_missed,
    is_rate_limited,
    mark_missed,
    mark_rate_limited,
    match_score,
    miss_is_provisional,
    rate_limit_from,
)

logger = get_logger("app.results.nhl")

_PROVIDER_NAME = "nhl"
_BASE = "https://api-web.nhle.com/v1"
# El pick puede llevar la fecha de publicación, no la del partido.
_DATE_TOLERANCE = timedelta(days=1)
# `gameState` de partido terminado ("OFF" = final oficial, "FINAL"
# aparece brevemente antes). "OVER" es cuasi-final — se acepta porque
# el marcador ya es definitivo para liquidar.
_FINISHED_STATES = {"off", "final", "over"}
# `gameScheduleState` -> status de MatchState (aplazado/cancelado).
_STATE_MAP = {"ppd": "postponed"}
_MIN_TEAM_SCORE = 0.6


def _team_name(team: dict) -> str:
    """ "Boston Bruins" si hay placeName; "Bruins" si solo el apodo."""
    name = ((team.get("name") or {}).get("default") or "").strip()
    place = ((team.get("placeName") or {}).get("default") or "").strip()
    return f"{place} {name}".strip() if place else name


def _game_score(game: dict, hint: str) -> float:
    home = _team_name(game.get("homeTeam") or {})
    away = _team_name(game.get("awayTeam") or {})
    if not home or not away:
        return 0.0
    return match_score(hint, home, away)


def _to_match_result(game: dict) -> Optional[MatchResult]:
    if (game.get("gameState") or "").lower() not in _FINISHED_STATES:
        return None
    home_team = game.get("homeTeam") or {}
    away_team = game.get("awayTeam") or {}
    try:
        home_score = int(home_team["score"])
        away_score = int(away_team["score"])
    except (KeyError, TypeError, ValueError):
        return None
    start = game.get("startTimeUTC") or ""
    try:
        event_date = datetime.fromisoformat(start.replace("Z", "+00:00"))
        event_date = event_date.replace(tzinfo=None)
    except ValueError:
        event_date = None
    return MatchResult(
        home_team=_team_name(home_team),
        away_team=_team_name(away_team),
        home_score=home_score,
        away_score=away_score,
        event_date=event_date,
    )


class NhlProvider:
    """Resultados NHL por día vía la API pública de nhl.com."""

    NAME = _PROVIDER_NAME
    SUPPORTED_SPORTS = frozenset({"hockey"})

    def __init__(self) -> None:
        # Día -> games: los picks del mismo día comparten la llamada.
        self._day_cache: dict[date_type, list] = {}

    async def _day_games(
        self, client: httpx.AsyncClient, day: date_type
    ) -> Optional[list]:
        """Partidos del día (`games` del scoreboard); None si error."""
        if day in self._day_cache:
            return self._day_cache[day]
        url = f"{_BASE}/score/{day.isoformat()}"
        try:
            response = await client.get(url, timeout=20)
            count_provider_call(_PROVIDER_NAME)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(_PROVIDER_NAME, exc.response)
                logger.warning("[NHL] Rate-limit; se omite hasta mañana")
            else:
                logger.warning("[NHL] Error de API: %r", exc)
            return None
        games = data.get("games") or [] if isinstance(data, dict) else []
        self._day_cache[day] = games
        return games

    async def _games(self, client: httpx.AsyncClient, date: datetime) -> Optional[list]:
        """Partidos del día `date` ±1 (el hint puede traer otra fecha)."""
        out: list = []
        start = (date - _DATE_TOLERANCE).date()
        for i in range(3):
            games = await self._day_games(client, start + timedelta(days=i))
            if games is None:
                return None  # error transitorio: no se marca missed
            out.extend(games)
        return out

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Mejor partido terminado del día (±1) que casa con el hint."""
        if is_rate_limited(_PROVIDER_NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{_PROVIDER_NAME}|results|{date.strftime('%Y-%m-%d')}|{hint.lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        async with httpx.AsyncClient() as client:
            games = await self._games(client, date)
        if games is None:
            return None
        best: Optional[MatchResult] = None
        best_score = 0.0
        saw_unfinished = False
        for game in games:
            if _game_score(game, hint) < _MIN_TEAM_SCORE:
                continue
            match = _to_match_result(game)
            if match is None:
                saw_unfinished = True
                continue
            score = _game_score(game, hint)
            if score > best_score:
                best, best_score = match, score
        if best is None:
            # Partido correcto aún en juego/programado no es miss.
            if not saw_unfinished:
                mark_missed(miss_key)
            return None
        return best

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """Partido NHL aplazado/cancelado del día (PPD y similares)."""
        if is_rate_limited(_PROVIDER_NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        async with httpx.AsyncClient() as client:
            games = await self._games(client, date)
        if not games:
            return None
        for game in games:
            state = _STATE_MAP.get((game.get("gameScheduleState") or "").lower())
            if state is None:
                continue
            if _game_score(game, hint) < _MIN_TEAM_SCORE:
                continue
            return MatchState(
                home_team=_team_name(game.get("homeTeam") or {}),
                away_team=_team_name(game.get("awayTeam") or {}),
                status=state,
            )
        return None
