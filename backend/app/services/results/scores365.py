"""Proveedor de resultados vía el webservice público de 365scores.

`webws.365scores.com` es el backend JSON que consume su propia web:
responde con `httpx` plano, sin API key ni Cloudflare — verificado en
vivo (2026-09-23). El endpoint por fecha dejó de honrar
`startDate`/`endDate` (~2026-09-26): siempre devuelve solo los últimos
~2 días. El histórico se recorre por la paginación por cursor que el
propio feed expone (`paging.previousPage` -> `aftergame=<id>
&direction=-1`, ~1 día por página). Las páginas son historia
inmutable: cada día visto se persiste en `provider_cache.json` por DÍA
real del partido, así que una búsqueda que retrocede N días pavimenta
la caché de todos los días intermedios y las siguientes verificaciones
de esos días no gastan llamadas.

El feed paginado trae TODOS los estados del día: `Ended`/`Final`/
`Just Ended`/`After Penalties` (marcador reglamentario — correcto para
1X2), `WalkOver` -> anulada por convención del verificador,
`Cancelled`/`Postponed` -> `find_postponed_match`, y programados/en
vivo (no resuelven pero impiden marcar el día como miss).

Cobertura verificada: ATP/WTA + Challenger + ITF + dobles (Buenos
Aires, Tolentino, Porto, Szczecin...), ligas de fútbol menores y
basket.

Formato de partido: `homeCompetitor`/`awayCompetitor` con `name`
("Arias B./Huertas Del Pino Cordova A." en dobles) y `score` (sets en
tenis, goles en fútbol, puntos en basket); `stages[]` trae el desglose
set a set solo en tenis.

Sin cuota documentada: se usa con el mismo semáforo por proveedor que
el resto y marca 403/429 igual — si algún día ponen límite, el
provider se auto-salta como los demás.

Una instancia por deporte (mismo motivo que `SofascoreBasketballProvider`:
`find_match` no recibe el deporte — el filtro es `SUPPORTED_SPORTS`).
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta, timezone
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
_HOST = "https://webws.365scores.com"
_PARAMS = "appTypeId=5&langId=1&timezoneName=Europe/Madrid&userCountryId=1"
# Primera página del feed (últimos ~2 días); el histórico se alcanza
# siguiendo `paging.previousPage` hacia atrás.
_RESULTS = f"{_BASE}/games/results/?{_PARAMS}"
# Deporte canónico -> sportId de 365scores (verificado en vivo).
_SPORT_IDS = {"futbol": 1, "baloncesto": 2, "tenis": 3}
# El pick puede llevar la fecha de publicación, no la del partido.
_DATE_TOLERANCE = timedelta(days=1)
# Tope de páginas hacia atrás por búsqueda (~días de antigüedad).
_MAX_PAGES = 45
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


def _game_day(game: dict) -> Optional[date_type]:
    """Día de inicio del partido (`startTime` ISO); None si no trae."""
    raw = game.get("startTime") or ""
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _day_boundary(day: date_type) -> datetime:
    """Instante en que el día queda definitivo (fin del día +4h)."""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(
        days=1, hours=4
    )


def _absolutize(path: Optional[str], sport_id: int) -> Optional[str]:
    """`paging.previousPage` viene sin host y puede perder `sports`."""
    if not path:
        return None
    url = path if path.startswith("http") else f"{_HOST}{path}"
    if f"sports={sport_id}" not in url:
        url += f"&sports={sport_id}"
    return url


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
        # Día real del partido -> partidos (memoria de la pasada:
        # picks del mismo día comparten las páginas ya caminadas).
        self._day_cache: dict[date_type, list] = {}

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> Optional[dict]:
        # Las páginas pesan ~100-200 KB; con varios picks en paralelo el
        # endpoint responde lento — timeout holgado + un reintento.
        response: Optional[httpx.Response] = None
        for _ in range(2):
            try:
                response = await client.get(url, timeout=30)
                count_provider_call(_PROVIDER_NAME)
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else None
            except httpx.HTTPError as exc:
                if rate_limit_from(exc):
                    mark_rate_limited(_PROVIDER_NAME, exc.response)
                    logger.warning("[365SCORES] Rate-limit; se omite hasta mañana")
                    return None
                logger.warning("[365SCORES] Error de API: %r", exc)
        return None

    def _cached_day(self, day: date_type) -> Optional[list]:
        """Partidos del día desde la caché persistente, si cerró."""
        cached = get_event_list(f"{_PROVIDER_NAME}|{self._sport}|day|{day.isoformat()}")
        if cached is None:
            return None
        try:
            captured = datetime.fromisoformat(cached.get("captured_at") or "")
        except ValueError:
            return None
        # El día solo es definitivo si se capturó ya cerrado: una
        # captura a media jornada no trae lo que acabó después.
        if captured <= _day_boundary(day):
            return None
        return cached["events"]

    def _store_day(self, day: date_type, games: list) -> None:
        """Persiste el día en provider_cache.json si ya es historia."""
        if datetime.now(timezone.utc) > _day_boundary(day):
            set_event_list(
                f"{_PROVIDER_NAME}|{self._sport}|day|{day.isoformat()}",
                games,
            )

    async def _games(self, client: httpx.AsyncClient, date: datetime) -> Optional[list]:
        """Partidos del día `date` ±1 paginando `results` hacia atrás.

        Las fechas en la query no se honran: la primera página trae los
        últimos ~2 días y `paging.previousPage` retrocede ~1 día por
        página. El paseo para al cubrir el primer día buscado; cada día
        visto se guarda en memoria y en disco (si el día ya cerró).
        """
        start = (date - _DATE_TOLERANCE).date()
        wanted = [start + timedelta(days=i) for i in range(3)]
        for day in wanted:
            if day not in self._day_cache:
                cached = self._cached_day(day)
                if cached is not None:
                    self._day_cache[day] = cached
        if all(day in self._day_cache for day in wanted):
            return [g for day in wanted for g in self._day_cache[day]]

        url: Optional[str] = f"{_RESULTS}&sports={self._sport_id}"
        for _ in range(_MAX_PAGES):
            if url is None:
                break
            data = await self._get_json(client, url)
            if data is None:
                return None  # error transitorio: no se marca missed
            by_day: dict[date_type, list] = {}
            for game in data.get("games") or []:
                day = _game_day(game)
                if day is not None:
                    by_day.setdefault(day, []).append(game)
            for day, games in by_day.items():
                self._day_cache.setdefault(day, games)
                self._store_day(day, games)
            oldest = min(by_day) if by_day else None
            if oldest is not None and oldest <= start:
                break
            url = _absolutize(
                (data.get("paging") or {}).get("previousPage"), self._sport_id
            )
        return [g for day in wanted for g in self._day_cache.get(day, [])]

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
            games = await self._games(client, date)
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
        """Partido cancelado/aplazado del día en el feed paginado.

        `results` lista también los `Cancelled`/`Postponed` del día:
        el mismo histórico de `find_match`, sin coste extra — así un
        cancelado de hace días se detecta igual que uno de hoy.
        """
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
            state = _STATE_MAP.get((game.get("statusText") or "").lower())
            if state is None:
                continue
            if _game_score(game, self._sport, hint) < _MIN_PLAYER_SIMILARITY:
                continue
            home, _, _ = _competitor(game, "homeCompetitor")
            away, _, _ = _competitor(game, "awayCompetitor")
            return MatchState(home_team=home, away_team=away, status=state)
        return None
