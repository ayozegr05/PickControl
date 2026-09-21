"""Proveedor de resultados de baloncesto usando API-Basketball.

Misma familia api-sports que API-Football pero producto y cuota propios
(100 req/día en el plan free, que cubre la temporada actual: ACB,
Supercopa, Euroliga, NBA...). No consume cuota de footapi7.

La diferencia clave con API-Football: `games?date=` devuelve TODOS los
partidos de baloncesto del día (sin filtrar por liga), así que una sola
llamada por fecha resuelve cualquier pick de basket de ese día. Las
listas de días ya terminados (todo FT/AOT/POST/CANC) son inmutables y
se persisten en `provider_cache.json`: un pick viejo de un día ya
consultado no vuelve a gastar cuota.

Soporta dos formas de acceso, según cómo te hayas registrado:
- Directo en api-basketball.com (recomendado): host
  "v1.basketball.api-sports.io", header "x-apisports-key".
- Vía RapidAPI (marketplace): host "api-basketball.p.rapidapi.com",
  autenticación con "X-RapidAPI-Key" / "X-RapidAPI-Host".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results import response_cache as rc
from app.services.results.base import (
    MatchResult,
    MatchState,
    is_rate_limited,
    mark_rate_limited,
    match_score,
    rate_limit_from,
)

logger = get_logger("app.results.api_basketball")

_MIN_TEAM_SIMILARITY = 0.6
# La fecha del evento a veces es solo el día en que el tipster publicó
# el pick. El endpoint solo acepta un día por petición: se prueba el día
# indicado y el anterior/siguiente (3 peticiones como máximo).
_DATE_OFFSETS = (0, -1, 1)

# Estados finales que resuelven la apuesta. AOT (after overtime) es un
# final normal: en basket el total de puntos ya incluye la prórroga y la
# casa liquida con él. AWD (awarded) también es final.
_FINISHED_STATUS = frozenset({"FT", "AOT", "AWD"})
# Estados que anulan la apuesta si el partido no se reprograma a tiempo.
# Los parados a mitad (SUSP) se dejan fuera a propósito: con marcador
# parcial hay mercados ya decididos que la casa paga igualmente.
_VOIDED_STATUSES = frozenset({"POST", "CANC"})
_VOIDED_STATUS_NAMES = {"POST": "postponed", "CANC": "cancelled"}
# Estados terminales (resueltos o anulados): cuando TODOS los partidos
# de un día están en uno de ellos, la lista del día es inmutable y se
# puede persistir en provider_cache.json sin riesgo de quedar vieja.
_TERMINAL_STATUS = _FINISHED_STATUS | _VOIDED_STATUSES


class ApiBasketballProvider:
    """Consulta API-Basketball por partidos finalizados de una fecha."""

    SUPPORTED_SPORTS = frozenset({"baloncesto"})

    def __init__(self, api_key: str, api_host: str) -> None:
        self._api_key = api_key
        self._api_host = api_host
        # Caché en memoria por fecha para esta pasada del verificador:
        # varios picks del mismo día reutilizan la misma respuesta.
        self._games_cache: dict[str, list] = {}

    def _headers(self) -> dict[str, str]:
        if "rapidapi" in self._api_host:
            return {
                "X-RapidAPI-Key": self._api_key,
                "X-RapidAPI-Host": self._api_host,
            }
        return {"x-apisports-key": self._api_key}

    def _games_url(self) -> str:
        # El host de RapidAPI no lleva la versión en el subdominio.
        if "rapidapi" in self._api_host:
            return f"https://{self._api_host}/v1/games"
        return f"https://{self._api_host}/games"

    def _check_errors(self, data: dict, context: str) -> None:
        """api-sports devuelve los fallos de cuota como HTTP 200 con un
        objeto `errors` dentro del JSON — sin este check se interpretaría
        como "sin partidos" y seguiría quemando llamadas."""
        errors = data.get("errors") or {}
        if not errors:
            return
        if any("request limit" in str(v).lower() for v in errors.values()):
            logger.warning(
                "[API-Basketball] Cuota diaria agotada (%s); se salta hasta mañana.",
                context,
            )
            mark_rate_limited("api-basketball")
        else:
            logger.debug("[API-Basketball] Errores de API (%s): %s", context, errors)

    async def _fetch_games(self, client: httpx.AsyncClient, date_str: str) -> list:
        if date_str in self._games_cache:
            return self._games_cache[date_str]
        # Día ya capturado con todos sus partidos terminados: inmutable,
        # se reutiliza para siempre sin llamar a la API.
        cached = rc.get_event_list(f"api-basketball|games|{date_str}")
        if cached is not None:
            games = cached.get("events") or []
            self._games_cache[date_str] = games
            return games
        if is_rate_limited("api-basketball"):
            return []
        try:
            response = await client.get(
                self._games_url(),
                params={"date": date_str},
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited("api-basketball")
            logger.warning("[API-Basketball] Error de API (%s): %s", date_str, exc)
            return []
        data = response.json()
        self._check_errors(data, f"games {date_str}")
        games = data.get("response", [])
        self._games_cache[date_str] = games
        # Persistir solo si el día ya está cerrado del todo: un partido
        # NS/en juego dejaría la lista eternamente vieja.
        if games and all(
            (g.get("status") or {}).get("short") in _TERMINAL_STATUS for g in games
        ):
            rc.set_event_list(f"api-basketball|games|{date_str}", games)
        return games

    async def _find_game(
        self,
        date: datetime,
        team_hint: str,
        statuses: frozenset = _FINISHED_STATUS,
    ) -> Optional[dict]:
        """El partido con alguno de los estados dados que mejor casa con
        el hint, o None."""
        best_match = None
        best_score = 0.0
        best_teams: tuple[str, str] | None = None
        ambiguous = False

        async with httpx.AsyncClient(timeout=20) as client:
            for offset in _DATE_OFFSETS:
                date_str = (date + timedelta(days=offset)).strftime("%Y-%m-%d")
                games = await self._fetch_games(client, date_str)

                for game in games:
                    status_short = (game.get("status") or {}).get("short")
                    if status_short not in statuses:
                        continue
                    home = game["teams"]["home"]["name"]
                    away = game["teams"]["away"]["name"]
                    score = match_score(team_hint, home, away)
                    if score > best_score:
                        best_score = score
                        best_match = game
                        best_teams = (home, away)
                        ambiguous = False
                    elif (
                        score == best_score
                        and score >= _MIN_TEAM_SIMILARITY
                        and (home, away) != best_teams
                    ):
                        # Dos partidos distintos empatan (p. ej. hint
                        # "Madrid" con Real Madrid y Estudiantes
                        # jugando): mejor no adivinar.
                        ambiguous = True

        if not best_match or best_score < _MIN_TEAM_SIMILARITY or ambiguous:
            return None
        return best_match

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        game = await self._find_game(date, team_hint)
        if game is None:
            return None

        scores = game.get("scores") or {}
        home_score = (scores.get("home") or {}).get("total")
        away_score = (scores.get("away") or {}).get("total")
        if home_score is None or away_score is None:
            return None

        # Marcador al descanso = puntos de los dos primeros cuartos
        # (habilita mercados de 1ª parte por la vía genérica `_ht_view`).
        ht_home = ht_away = None
        home_q = scores.get("home") or {}
        away_q = scores.get("away") or {}
        if all(
            side.get(q) is not None
            for side in (home_q, away_q)
            for q in ("quarter_1", "quarter_2")
        ):
            ht_home = home_q["quarter_1"] + home_q["quarter_2"]
            ht_away = away_q["quarter_1"] + away_q["quarter_2"]

        return MatchResult(
            home_team=game["teams"]["home"]["name"],
            away_team=game["teams"]["away"]["name"],
            home_score=home_score,
            away_score=away_score,
            ht_home_score=ht_home,
            ht_away_score=ht_away,
        )

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """Partido aplazado/cancelado que casa con el hint, o None."""
        game = await self._find_game(date, team_hint, statuses=_VOIDED_STATUSES)
        if game is None:
            return None
        status_short = game["status"]["short"]
        return MatchState(
            home_team=game["teams"]["home"]["name"],
            away_team=game["teams"]["away"]["name"],
            status=_VOIDED_STATUS_NAMES.get(status_short, "postponed"),
        )
