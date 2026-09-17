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

from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import (
    MatchEvents,
    MatchResult,
    MatchStats,
    is_rate_limited,
    mark_rate_limited,
    match_score,
)

logger = get_logger("app.results.api_football")

_MIN_TEAM_SIMILARITY = 0.6
# La fecha del evento a veces es solo una aproximación (día en que el
# tipster publicó el pick, no el día exacto del partido). El endpoint de
# fixtures solo acepta un día por petición, así que probamos el día
# indicado y el anterior/siguiente (3 peticiones en total).
_DATE_OFFSETS = (0, -1, 1)
# El plan gratis solo permite consultar fechas dentro de [ayer, mañana]
# ("Free plans do not have access to this date"). Pedir fuera de esa
# ventana quema cuota para nada (~100 req/día): se salta sin llamar.
_FREE_DATE_RADIUS = 1


def _within_free_window(day: date_type) -> bool:
    """El plan gratis solo sirve fechas en [ayer, mañana] respecto a hoy."""
    return abs((day - date_type.today()).days) <= _FREE_DATE_RADIUS


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
        # Ídem para eventos (goles con jugador/asistente, tarjetas...).
        self._events_cache: dict[int, list] = {}

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

    def _events_url(self) -> str:
        if "rapidapi" in self._api_host:
            return f"https://{self._api_host}/v3/fixtures/events"
        return f"https://{self._api_host}/fixtures/events"

    def _check_errors(self, data: dict, context: str) -> None:
        """API-Football devuelve los fallos de plan/cuota como HTTP 200
        con un objeto `errors` dentro del JSON — sin este check se
        interpretaría como "sin fixtures" y seguiría quemando llamadas."""
        errors = data.get("errors") or {}
        if not errors:
            return
        if any("request limit" in str(v).lower() for v in errors.values()):
            logger.warning(
                "[API-Football] Cuota diaria agotada (%s); se salta hasta mañana.",
                context,
            )
            mark_rate_limited("api-football")
        elif any("plan" in str(k).lower() for k in errors):
            # Fecha fuera de la ventana del plan gratis: normal, no cuota.
            logger.debug("[API-Football] %s fuera de la ventana del plan.", context)

    async def _fetch_fixtures(self, client: httpx.AsyncClient, date_str: str) -> list:
        if date_str in self._fixtures_cache:
            return self._fixtures_cache[date_str]
        if is_rate_limited("api-football"):
            return []
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
        data = response.json()
        self._check_errors(data, f"fixtures {date_str}")
        fixtures = data.get("response", [])
        self._fixtures_cache[date_str] = fixtures
        return fixtures

    async def _fetch_statistics(
        self, client: httpx.AsyncClient, fixture_id: int
    ) -> list:
        if fixture_id in self._stats_cache:
            return self._stats_cache[fixture_id]
        if is_rate_limited("api-football"):
            return []
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
        data = response.json()
        self._check_errors(data, f"stats fixture {fixture_id}")
        stats = data.get("response", [])
        self._stats_cache[fixture_id] = stats
        return stats

    async def _fetch_events(self, client: httpx.AsyncClient, fixture_id: int) -> list:
        if fixture_id in self._events_cache:
            return self._events_cache[fixture_id]
        if is_rate_limited("api-football"):
            return []
        try:
            response = await client.get(
                self._events_url(),
                params={"fixture": fixture_id},
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "[API-Football] Error de API events (fixture %s): %s",
                fixture_id,
                exc,
            )
            return []
        data = response.json()
        self._check_errors(data, f"events fixture {fixture_id}")
        events = data.get("response", [])
        self._events_cache[fixture_id] = events
        return events

    async def _find_fixture(self, date: datetime, team_hint: str) -> Optional[dict]:
        """El fixture FT que mejor casa con el hint, o None."""
        best_match = None
        best_score = 0.0
        best_teams: tuple[str, str] | None = None
        ambiguous = False

        async with httpx.AsyncClient(timeout=15) as client:
            for offset in _DATE_OFFSETS:
                day = (date + timedelta(days=offset)).date()
                if not _within_free_window(day):
                    # Fuera de la ventana del plan gratis: la API
                    # devolvería errors.plan — llamada desperdiciada.
                    continue
                date_str = day.strftime("%Y-%m-%d")
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

    async def find_match_events(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchEvents]:
        """Eventos del partido (goles con jugador/asistente, tarjetas,
        cambios): sirve para mercados de jugador. Mismo matching +
        llamada a `/fixtures/events` cacheada por fixture.
        """
        fixture = await self._find_fixture(date, team_hint)
        if fixture is None:
            return None
        fixture_id = fixture.get("fixture", {}).get("id")
        if fixture_id is None:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            raw_events = await self._fetch_events(client, fixture_id)

        scorers: list[str] = []
        assisters: list[str] = []
        booked: list[str] = []
        participants: list[str] = []
        for event in raw_events:
            player = (event.get("player") or {}).get("name")
            assist = (event.get("assist") or {}).get("name")
            for name in (player, assist):
                if name and name not in participants:
                    participants.append(name)
            etype = event.get("type")
            detail = str(event.get("detail") or "").lower()
            if etype == "Goal":
                # En propia puerta no cuenta como gol del jugador; un
                # penalti fallado tampoco es gol.
                if "own goal" in detail or "missed" in detail:
                    continue
                if player and player not in scorers:
                    scorers.append(player)
                if assist and assist not in assisters:
                    assisters.append(assist)
            elif etype == "Card" and player and player not in booked:
                booked.append(player)

        if not participants:
            return None
        return MatchEvents(
            home_team=fixture["teams"]["home"]["name"],
            away_team=fixture["teams"]["away"]["name"],
            scorers=scorers,
            assisters=assisters,
            booked=booked,
            participants=participants,
        )
