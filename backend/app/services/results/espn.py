"""Proveedor gratuito de resultados y estadísticas de fútbol vía ESPN.

Usa el endpoint JSON interno de espn.com (`site.api.espn.com`): no
documentado pero estable desde hace años, sin API key ni cuota — por eso
va PRIMERO en la cascada de fútbol, descargando a football-data (10
req/min) y a los mirrors RapidAPI de cuota diaria.

Una llamada por `scoreboard` devuelve TODOS los partidos de una liga en
un día, con marcador FT/HT, estadísticas por equipo (córners, faltas,
tiros) y eventos (goles, tarjetas con jugador). Cubre solo ligas top
(`_LEAGUES`); lo que no esté ahí cae en cascada a los siguientes
providers.

No implementa `find_match_events`/`find_match_players` a propósito: su
lista `details` solo incluye jugadores CON evento (goleadores,
amonestados...), así que `MatchEvents.participants` quedaría incompleta
y el verificador dejaría props de jugador en pendiente sin cascar a
footapi7 (que sí tiene alineaciones completas).

Salud: ESPN puede cambiar el formato sin aviso. Si N llamadas
consecutivas fallan (HTTP o payload sin `events`), el provider se aparca
el día con `mark_rate_limited` — eso dispara el push a admins igual que
una cuota agotada.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    MatchResult,
    MatchState,
    MatchStats,
    count_provider_call,
    is_missed,
    is_rate_limited,
    mark_missed,
    mark_rate_limited,
    match_score,
    miss_is_provisional,
)

logger = get_logger("app.results.espn")

_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
_MIN_TEAM_SIMILARITY = 0.6
# El pick puede llevar el día de publicación, no el del partido.
_DATE_WINDOW = timedelta(days=1)
# Fallos consecutivos (HTTP o formato roto) que declaran la API caída.
_MAX_CONSECUTIVE_FAILURES = 5

# Ligas consultadas, ordenadas por relevancia para los tipsters que se
# siguen (España y grandes ligas primero). ESPN no expone un scoreboard
# global de fútbol: hay que iterar por liga.
_LEAGUES = (
    "esp.1",
    "esp.2",
    "esp.copa_del_rey",
    "eng.1",
    "eng.2",
    "eng.fa",
    "eng.league_cup",
    "ita.1",
    "ita.2",
    "ger.1",
    "ger.2",
    "fra.1",
    "ned.1",
    "por.1",
    "tur.1",
    "uefa.champions",
    "uefa.europa",
    "uefa.europa.conf",
    "arg.1",
    "bra.1",
    "usa.1",
    "mex.1",
    "fifa.world",
    "uefa.euro",
    "uefa.nations",
)

# `name` de la estadística ESPN -> clave canónica que ya consume el
# verificador (mismas que devuelve API-Football `/fixtures/statistics`).
_STAT_NAME_MAP = {
    "wonCorners": "Corner Kicks",
    "foulsCommitted": "Fouls",
    "totalShots": "Total Shots",
    "shotsOnTarget": "Shots on Goal",
}

# `status.type.name` de ESPN que anula la apuesta.
_VOIDED_STATUSES = {
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "cancelled",
    "STATUS_CANCELLED": "cancelled",
}


def _team_name(team: dict) -> str:
    """El mejor nombre disponible del equipo en el payload ESPN."""
    return (
        team.get("displayName")
        or team.get("name")
        or team.get("shortDisplayName")
        or ""
    )


def _competitors(competition: dict) -> tuple[Optional[dict], Optional[dict]]:
    """(competidor local, competidor visitante) del evento."""
    home = away = None
    for comp in competition.get("competitors") or []:
        if comp.get("homeAway") == "home":
            home = comp
        elif comp.get("homeAway") == "away":
            away = comp
    return home, away


def _stat_int(competitor: dict, name: str) -> Optional[int]:
    """Valor entero de una estadística de equipo ESPN (displayValue)."""
    for stat in competitor.get("statistics") or []:
        if stat.get("name") != name:
            continue
        try:
            return int(float(stat.get("displayValue") or ""))
        except (TypeError, ValueError):
            return None
    return None


class EspnProvider:
    """Resultados y stats de fútbol consultando scoreboards de ESPN."""

    NAME = "espn"
    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self) -> None:
        # Scoreboards por (liga, día): una pasada comparte respuestas
        # entre picks de la misma fecha. Los fallos cachean None.
        self._boards: dict[tuple[str, str], Optional[dict]] = {}
        self._consecutive_failures = 0

    # --- HTTP + salud -------------------------------------------------

    def _register_failure(self, league: str, exc: Exception) -> None:
        self._consecutive_failures += 1
        logger.warning(
            "[ESPN] Error en scoreboard %s (%d consecutivos): %s",
            league,
            self._consecutive_failures,
            exc,
        )
        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            # Sin response no hay cooldown corto: aparca el día entero y
            # dispara el push "cuota agotada" a admins — aquí significa
            # "API rota o caída, revisar".
            mark_rate_limited(self.NAME)
            logger.error(
                "[ESPN] %d fallos consecutivos — API probablemente rota o "
                "caída; aparcada hasta mañana y notificado a admins",
                self._consecutive_failures,
            )

    async def _scoreboard(
        self, client: httpx.AsyncClient, league: str, day: str
    ) -> Optional[dict]:
        """Payload del scoreboard de una liga en un día (yyyymmdd)."""
        key = (league, day)
        if key in self._boards:
            return self._boards[key]
        if is_rate_limited(self.NAME):
            self._boards[key] = None
            return None
        count_provider_call(self.NAME)
        try:
            response = await client.get(
                f"{_BASE_URL}/{league}/scoreboard",
                params={"dates": day, "limit": 100},
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # httpx + json decode
            self._register_failure(league, exc)
            self._boards[key] = None
            return None
        if not isinstance(data, dict) or "events" not in data:
            self._register_failure(league, ValueError("payload sin 'events'"))
            self._boards[key] = None
            return None
        self._consecutive_failures = 0
        self._boards[key] = data
        return data

    # --- Resolución de evento ------------------------------------------

    async def _find_event(
        self, date: datetime, team_hint: str
    ) -> Optional[tuple[dict, dict]]:
        """(evento, competition) que mejor casa con el hint, o None.

        Recorre las ligas del día del pick ±1. Un miss es definitivo:
        el scoreboard enumera todos los partidos de la liga ese día.
        """
        hint = team_hint.strip()
        if not hint or is_rate_limited(self.NAME):
            return None
        provisional = miss_is_provisional(date)
        miss_key = f"{self.NAME}|futbol|{date.strftime('%Y-%m-%d')}|{hint.lower()}"
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        best = None
        best_score = 0.0
        best_teams: tuple[str, str] | None = None
        ambiguous = False
        async with httpx.AsyncClient(timeout=15) as client:
            for offset in (0, -1, 1):
                day = (date + timedelta(days=offset)).strftime("%Y%m%d")
                for league in _LEAGUES:
                    board = await self._scoreboard(client, league, day)
                    if board is None:
                        continue
                    for event in board.get("events") or []:
                        competitions = event.get("competitions") or []
                        if not competitions:
                            continue
                        competition = competitions[0]
                        home, away = _competitors(competition)
                        if home is None or away is None:
                            continue
                        home_name = _team_name(home.get("team") or {})
                        away_name = _team_name(away.get("team") or {})
                        score = match_score(hint, home_name, away_name)
                        if score > best_score:
                            best_score = score
                            best = (event, competition)
                            best_teams = (home_name, away_name)
                            ambiguous = False
                        elif (
                            score == best_score
                            and score >= _MIN_TEAM_SIMILARITY
                            and (home_name, away_name) != best_teams
                        ):
                            ambiguous = True

        if best is None or best_score < _MIN_TEAM_SIMILARITY or ambiguous:
            mark_missed(miss_key)
            return None
        return best

    # --- Interfaz ResultsProvider --------------------------------------

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Marcador final del partido (incluye marcador al descanso si
        ESPN publica `linescores`)."""
        found = await self._find_event(date, team_hint)
        if found is None:
            return None
        event, competition = found
        status = (competition.get("status") or {}).get("type") or {}
        if not status.get("completed"):
            return None
        home, away = _competitors(competition)
        if home is None or away is None:
            return None
        try:
            home_score = int(home.get("score"))
            away_score = int(away.get("score"))
        except (TypeError, ValueError):
            return None
        ht_home = ht_away = None
        home_lines = home.get("linescores") or []
        away_lines = away.get("linescores") or []
        if home_lines and away_lines:
            try:
                ht_home = int(home_lines[0].get("value"))
                ht_away = int(away_lines[0].get("value"))
            except (TypeError, ValueError, IndexError):
                pass
        return MatchResult(
            home_team=_team_name(home.get("team") or {}),
            away_team=_team_name(away.get("team") or {}),
            home_score=home_score,
            away_score=away_score,
            ht_home_score=ht_home,
            ht_away_score=ht_away,
        )

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """Partido aplazado/cancelado. Reusa los scoreboards cacheados
        — no cuesta llamadas extra."""
        found = await self._find_event(date, team_hint)
        if found is None:
            return None
        _, competition = found
        status_name = ((competition.get("status") or {}).get("type") or {}).get("name")
        status = _VOIDED_STATUSES.get(status_name or "")
        if status is None:
            return None
        home, away = _competitors(competition)
        return MatchState(
            home_team=_team_name((home or {}).get("team") or {}),
            away_team=_team_name((away or {}).get("team") or {}),
            status=status,
        )

    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        """Estadísticas del partido completo: córners, tarjetas, tiros,
        faltas — gratis, sin la ventana ±1 día del plan free de
        API-Football ni la cuota diaria de footapi7.

        Las tarjetas no vienen en `statistics`: se cuentan de
        `details[]` (eventos "Yellow Card"/"Red Card" por equipo). Una
        "Yellow - Red Card" cuenta como ambas — la casa paga la doble
        amarilla como amarilla + roja.
        """
        found = await self._find_event(date, team_hint)
        if found is None:
            return None
        _, competition = found
        status = (competition.get("status") or {}).get("type") or {}
        if not status.get("completed"):
            return None
        home, away = _competitors(competition)
        if home is None or away is None:
            return None

        values: dict[str, tuple[int, int]] = {}
        for espn_name, canonical in _STAT_NAME_MAP.items():
            home_val = _stat_int(home, espn_name)
            away_val = _stat_int(away, espn_name)
            if home_val is not None and away_val is not None:
                values[canonical] = (home_val, away_val)

        home_id = (home.get("team") or {}).get("id")
        away_id = (away.get("team") or {}).get("id")
        yellow = {"home": 0, "away": 0}
        red = {"home": 0, "away": 0}
        saw_card_details = False
        for detail in competition.get("details") or []:
            text = (detail.get("type") or {}).get("text") or ""
            if "Card" not in text:
                continue
            saw_card_details = True
            side = None
            team_id = (detail.get("team") or {}).get("id")
            if team_id == home_id:
                side = "home"
            elif team_id == away_id:
                side = "away"
            if side is None:
                continue
            if "Yellow" in text:
                yellow[side] += 1
            if "Red" in text:
                red[side] += 1
        if saw_card_details:
            values["Yellow Cards"] = (yellow["home"], yellow["away"])
            values["Red Cards"] = (red["home"], red["away"])

        if not values:
            return None
        return MatchStats(
            home_team=_team_name(home.get("team") or {}),
            away_team=_team_name(away.get("team") or {}),
            values=values,
        )
