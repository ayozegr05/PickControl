"""Proveedor de resultados vía el webservice público de 365scores.

`webws.365scores.com` es el backend JSON que consume su propia web:
responde con `httpx` plano, sin API key ni Cloudflare — verificado en
vivo (2026-09-23). Patrón por FECHA, no por jugador: una sola llamada
devuelve todos los partidos del día de un deporte (~80 tenis, ~70
fútbol, ~100 basket), así que verificar N picks del mismo día cuesta
1 llamada en lugar de ~3 por jugador como en los mirrors Sofascore.

    /web/games/results/?sports={id}&startDate=D&endDate=D  -> jugados
    /web/games/current/?sports={id}&startDate=D&endDate=D  -> incluye
        Scheduled / live / Cancelled (para `find_postponed_match`)

Cobertura verificada: ATP/WTA + Challenger + ITF + dobles (Buenos
Aires, Tolentino, Porto...), ligas de fútbol menores y basket.

Formato de partido: `homeCompetitor`/`awayCompetitor` con `name`
("Arias B./Huertas Del Pino Cordova A." en dobles) y `score` (sets en
tenis, goles en fútbol, puntos en basket); `stages[]` trae el desglose
set a set solo en tenis. Estados: `Ended`/`Final`/`Just Ended`/
`After Penalties` (marcador reglamentario — correcto para 1X2) y
`WalkOver` -> anulada por convención del verificador.

Sin cuota documentada: se usa con el mismo semáforo por proveedor que
el resto y marca 403/429 igual — si algún día ponen límite, el
provider se auto-salta como los demás.

Una instancia por deporte (mismo motivo que `SofascoreBasketballProvider`:
`find_match` no recibe el deporte — el filtro es `SUPPORTED_SPORTS`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.allsports_tennis import _hint_match_score
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
from app.services.results.response_cache import get_event_list, set_event_list
from app.services.results.tennisapi1 import _MIN_PLAYER_SIMILARITY

logger = get_logger("app.results.scores365")

_PROVIDER_NAME = "scores365"
_BASE = "https://webws.365scores.com/web"
_PARAMS = "appTypeId=5&langId=1&timezoneName=Europe/Madrid&userCountryId=1"
# `games/results` solo trae terminados; `games/current` mezcla
# programados/en vivo/cancelados del día — sirve para aplazados.
_RESULTS = f"{_BASE}/games/results/?{_PARAMS}"
_CURRENT = f"{_BASE}/games/current/?{_PARAMS}"
# Deporte canónico -> sportId de 365scores (verificado en vivo).
_SPORT_IDS = {"futbol": 1, "baloncesto": 2, "tenis": 3}
# El pick puede llevar la fecha de publicación, no la del partido.
_DATE_TOLERANCE = timedelta(days=1)
# Umbral de similitud equipo (fútbol/basket); tenis usa el de players.
_MIN_TEAM_SCORE = 0.6
# Estados del feed que significan "partido terminado".
_FINISHED_STATUSES = {
    "ended",
    "final",
    "just ended",
    "after penalties",
    "final result only",
    "final/ot",
    "walkover",
}
# `statusText` -> status de MatchState (aplazado/cancelado).
_STATE_MAP = {"cancelled": "cancelled", "postponed": "postponed"}


def _competitor(game: dict, key: str) -> tuple[str, Optional[float], bool]:
    """(nombre, score, isWinner) del lado home/away de un partido."""
    comp = game.get(key) or {}
    return (
        comp.get("name") or "",
        comp.get("score"),
        bool(comp.get("isWinner")),
    )


def _tennis_sets(game: dict) -> Optional[list[tuple[int, int]]]:
    """Juegos por set desde `stages` ("Set 1".."Set 5"), o None."""
    stages = game.get("stages") or []
    pairs: list[tuple[int, int]] = []
    for stage in stages:
        name = stage.get("name") or ""
        if not name.lower().startswith("set"):
            continue
        home = stage.get("homeCompetitorScore")
        away = stage.get("awayCompetitorScore")
        if home is None or away is None:
            continue
        pairs.append((int(home), int(away)))
    return pairs or None


def _game_score(game: dict, sport: str, hint: str) -> float:
    """Confianza de que `game` es el partido del hint, por deporte."""
    home, _, _ = _competitor(game, "homeCompetitor")
    away, _, _ = _competitor(game, "awayCompetitor")
    if not home or not away:
        return 0.0
    if sport == "tenis":
        # Conserva parejas de dobles ("A. / B."): mismo scoring que
        # allsports_tennis, ya que los nombres vienen igual ("Arias
        # B./Huertas Del Pino Cordova A.").
        return _hint_match_score(hint, home, away)
    return match_score(hint, home, away)


def _to_match_result(game: dict, sport: str) -> Optional[MatchResult]:
    """Partido terminado del feed -> MatchResult normalizado."""
    status_text = (game.get("statusText") or "").lower()
    if status_text not in _FINISHED_STATUSES:
        return None
    home, home_score, _ = _competitor(game, "homeCompetitor")
    away, away_score, _ = _competitor(game, "awayCompetitor")
    if home_score is None or away_score is None:
        return None
    status = "walkover" if status_text == "walkover" else None
    return MatchResult(
        home_team=home,
        away_team=away,
        home_score=int(home_score),
        away_score=int(away_score),
        sets=_tennis_sets(game) if sport == "tenis" else None,
        status=status,
    )


class Scores365Provider:
    """Resultados por fecha vía el webservice público de 365scores.

    Una instancia por deporte: `find_match` no recibe el deporte del
    pick — la selección la hace `SUPPORTED_SPORTS` en el verificador.
    """

    NAME = _PROVIDER_NAME

    def __init__(self, sport: str) -> None:
        self.SUPPORTED_SPORTS = frozenset({sport})
        self._sport = sport
        self._sport_id = _SPORT_IDS[sport]
        # (start_iso, end_iso) -> lista de partidos o None (error).
        self._feed_cache: dict[tuple[str, str], Optional[list]] = {}

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> Optional[dict]:
        try:
            response = await client.get(url, timeout=15)
            count_provider_call(_PROVIDER_NAME)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(_PROVIDER_NAME, exc.response)
                logger.warning("[365SCORES] Rate-limit; se omite hasta mañana")
            else:
                logger.warning("[365SCORES] Error de API: %s", exc)
            return None

    async def _games(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        date: datetime,
        cacheable: bool,
    ) -> Optional[list]:
        """Partidos del día `date` ±1 (una llamada cubre la tolerancia).

        `cacheable=True` (feed de resultados, inmutable una vez pasado
        el día) persiste en provider_cache.json para no repetir la
        llamada entre pasadas del verificador.
        """
        start = (date - _DATE_TOLERANCE).strftime("%Y-%m-%d")
        end = (date + _DATE_TOLERANCE).strftime("%Y-%m-%d")
        key = (start, end, endpoint)
        if key in self._feed_cache:
            return self._feed_cache[key]
        cache_key = f"{_PROVIDER_NAME}|{self._sport}|{start}|{end}"
        if cacheable:
            cached = get_event_list(cache_key)
            # El feed solo es definitivo si se capturó tras el fin del
            # rango: una captura a media jornada no trae los partidos
            # que acabaron después y el miss quedaría congelado.
            boundary = datetime.fromisoformat(f"{end}T23:59:59+00:00") + timedelta(
                hours=4
            )
            if cached is not None:
                captured_raw = cached.get("captured_at") or ""
                try:
                    captured = datetime.fromisoformat(captured_raw)
                except ValueError:
                    captured = None
                if captured is not None and captured > boundary:
                    games = cached["events"]
                    self._feed_cache[key] = games
                    return games
        data = await self._get_json(
            client,
            f"{endpoint}&sports={self._sport_id}&startDate={start}&endDate={end}",
        )
        if data is None:
            self._feed_cache[key] = None
            return None
        games = data.get("games") or []
        self._feed_cache[key] = games
        if cacheable and games:
            set_event_list(cache_key, games)
        return games

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Mejor partido terminado del día (±1) que casa con el hint."""
        if is_rate_limited(_PROVIDER_NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{_PROVIDER_NAME}|results|{self._sport}|{date.strftime('%Y-%m-%d')}"
            f"|{hint.lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        best: Optional[MatchResult] = None
        best_score = 0.0
        saw_unfinished = False
        async with httpx.AsyncClient() as client:
            games = await self._games(client, _RESULTS, date, cacheable=True)
        if games is None:
            return None  # error transitorio: no se marca missed
        for game in games:
            score = _game_score(game, self._sport, hint)
            if score < _MIN_PLAYER_SIMILARITY:
                continue
            match = _to_match_result(game, self._sport)
            if match is None:
                saw_unfinished = True
                continue
            if score > best_score:
                best, best_score = match, score

        if best is None:
            # El partido correcto aún en juego/programado no es un miss:
            # se reintenta en la siguiente pasada.
            if not saw_unfinished:
                mark_missed(miss_key)
            return None
        return best

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """Partido cancelado/aplazado del día en el feed `current`.

        `games/results` excluye los no disputados; `games/current` sí
        los lista con `statusText` Cancelled/Postponed.
        """
        if is_rate_limited(_PROVIDER_NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        async with httpx.AsyncClient() as client:
            games = await self._games(client, _CURRENT, date, cacheable=False)
        if not games:
            return None
        for game in games:
            state = _STATE_MAP.get((game.get("statusText") or "").lower())
            if state is None:
                continue
            if _game_score(game, self._sport, hint) < _MIN_PLAYER_SIMILARITY:
                continue
            home, _, _ = _competitor(game, "homeCompetitor")
            away, _, _ = _competitor(game, "awayCompetitor")
            return MatchState(home_team=home, away_team=away, status=state)
        return None
