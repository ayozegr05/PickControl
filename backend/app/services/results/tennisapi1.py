"""Tercer proveedor de tenis: "TennisApi" en RapidAPI (datos de Sofascore).

Host: tennisapi1.p.rapidapi.com. Cubre ATP/WTA/Challenger/ITF/Copas —
misma cobertura que Sofascore. Se consulta SOLO cuando TheSportsDB y
"Tennis API - ATP WTA ITF" no encontraron el partido (su plan gratuito
son ~50 req/día, compartidos con cualquier otra API RapidAPI de la
misma cuenta solo a nivel de key — la cuota es por suscripción).

Dos vías de búsqueda (verificadas en vivo):

1. **Por jugador** (~2 llamadas): `/api/tennis/search/{nombre}` da el
   id del jugador — y resuelve nombres parciales en servidor
   ("chidek" → "Clement Chidekh") — y
   `/api/tennis/team/{id}/events/near` devuelve `previousEvent` +
   `nextEvent` con marcador y sets. Es la vía preferida: cubre los
   partidos recientes, que son justo los que el verificador resuelve.
2. **Por categoría** (fallback, hasta 9 llamadas):

    GET /api/tennis/category/{id}/events/{day}/{month}/{year}

IDs de categoría (descubiertos vía `/api/tennis/search`):
    3 ATP · 6 WTA · 72 Challenger · 785 ITF Men · 213 ITF Women
    871 WTA 125 · 76 Davis Cup · 1705 United Cup · 79 Exhibition

Para no quemar la cuota:
- Las categorías se recorren en orden de probabilidad y se sale en
  cuanto aparece el partido (lo normal: 1-3 llamadas por pick).
- Cada respuesta se cachea por (categoría, fecha): otros picks del
  mismo día reutilizan categorías ya descargadas.
- Si un partido no está en ninguna categoría, el fallo se recuerda
  ~15 días en disco (`is_missed`/`mark_missed` de base.py): los
  resultados históricos no aparecen tarde y reintentar solo quema cuota.
- Solo se consulta la fecha exacta del pick (la tolerancia ±1 día ya la
  cubre el nivel anterior, que busca en el historial del jugador): así
  el peor caso son 9 llamadas por partido no encontrado, no 27.
- Un 403/429 marca el proveedor como sin cuota hasta mañana (base.py).

Respuesta Sofascore por evento: `homeTeam`/`awayTeam` (nombre),
`homeScore.current`/`awayScore.current` = sets ganados y
`status.type` = "finished" cuando el partido terminó. Los dobles se
descartan (equipos "A / B") para no falsear picks de individuales.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from app.core.logging import get_logger
from app.services.results.api_tennis import _pair_similar
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    MatchResult,
    is_missed,
    is_rate_limited,
    mark_missed,
    mark_rate_limited,
    miss_is_provisional,
    rate_limit_from,
)
from app.services.results.response_cache import (
    event_list_covers,
    get_entity_id,
    get_event_list,
    set_entity_id,
    set_event_list,
)

logger = get_logger("app.results.tennisapi1")

_PROVIDER_NAME = "tennisapi1"
_MIN_PLAYER_SIMILARITY = 0.6
# Tolerancia de la vía por jugador: events/near trae el partido
# anterior y el siguiente del jugador — el del pick debe caer dentro.
_PLAYER_DATE_TOLERANCE = timedelta(days=1)
# Solo fecha exacta: la tolerancia ±1 día ya la da el nivel anterior
# (RapidApiTennisProvider busca en el historial del jugador). Limitarlo
# a un día acota el peor caso a len(_CATEGORIES) llamadas por pick.
_DATE_OFFSETS = (0,)
# Ordenadas por probabilidad de que un tipster publique esos partidos.
_CATEGORIES = (3, 6, 72, 785, 213, 871, 76, 1705, 79)


def _parse_event(event: dict) -> Optional[MatchResult]:
    """Evento Sofascore -> MatchResult. None si no terminó o es dobles.

    `homeScore.periodN`/`awayScore.periodN` llevan los juegos de cada
    set — se guardan en `MatchResult.sets` para mercados de juegos.
    """
    status = (event.get("status") or {}).get("type")
    home = (event.get("homeTeam") or {}).get("name") or ""
    away = (event.get("awayTeam") or {}).get("name") or ""
    # Los dobles ya no se descartan aquí: `_pair_similar` exige que la
    # pista case con TODOS los miembros de la pareja, así que un pick
    # individual nunca se resuelve contra un dobles (ni al revés).
    if not home or not away:
        return None
    if status in ("retired", "walkover"):
        # Retirada/walkover: el partido no terminó por la vía normal.
        # Marcador parcial aproximado; el verificador devuelve anulada.
        try:
            ret_home = int((event.get("homeScore") or {})["current"])
            ret_away = int((event.get("awayScore") or {})["current"])
        except (KeyError, TypeError, ValueError):
            ret_home = ret_away = 0
        return MatchResult(
            home_team=home,
            away_team=away,
            home_score=ret_home,
            away_score=ret_away,
            status=status,
        )
    if status != "finished":
        return None
    try:
        home_sets = int((event.get("homeScore") or {})["current"])
        away_sets = int((event.get("awayScore") or {})["current"])
    except (KeyError, TypeError, ValueError):
        return None

    home_score = event.get("homeScore") or {}
    away_score = event.get("awayScore") or {}
    sets: list[tuple[int, int]] = []
    for period in range(1, home_sets + away_sets + 1):
        try:
            sets.append(
                (int(home_score[f"period{period}"]), int(away_score[f"period{period}"]))
            )
        except (KeyError, TypeError, ValueError):
            break

    return MatchResult(
        home_team=home,
        away_team=away,
        home_score=home_sets,
        away_score=away_sets,
        sets=sets or None,
    )


def _event_matches_hint(event: dict, team_hint: str) -> bool:
    """True si el evento (aún sin resultado) involucra al jugador de la
    pista. Sirve para distinguir "el partido está en vivo" de "el
    partido no existe en el feed": solo lo segundo merece `missed`."""
    home = (event.get("homeTeam") or {}).get("name") or ""
    away = (event.get("awayTeam") or {}).get("name") or ""
    if not home or not away:
        return False
    return (
        max(_pair_similar(team_hint, home), _pair_similar(team_hint, away))
        >= _MIN_PLAYER_SIMILARITY
    )


def _searchable_name(team_hint: str) -> str:
    """Primer nombre de la pista para `/search`: "Cecchinato" de
    "Cecchinato vs Moeller", "Alcaraz" de "Alcaraz / Munar"."""
    return (
        re.split(r"[/+&]|\s+-\s+|\s+vs\.?\s+", team_hint, maxsplit=1)[0].strip()
        or team_hint
    )


def _best_player_id(name: str, results: list) -> Optional[int]:
    """Id del mejor jugador de tenis en los resultados de /search.

    La API ya ordena por relevancia y resuelve nombres parciales
    ("chidek" -> "Clement Chidekh"); aun así se exige similitud mínima
    para no agarrar un jugador distinto si la búsqueda devolvió ruido.
    """
    best_id: Optional[int] = None
    best_score = 0.0
    for res in results:
        entity = res.get("entity") or {}
        if (entity.get("sport") or {}).get("slug") != "tennis":
            continue
        score = _pair_similar(name, entity.get("name") or "")
        if score > best_score:
            best_score = score
            best_id = entity.get("id")
    return best_id if best_score >= _MIN_PLAYER_SIMILARITY else None


class TennisApi1Provider:
    """Consulta tennisapi1 (Sofascore) por categoría y fecha."""

    SUPPORTED_SPORTS = frozenset({"tenis"})

    def __init__(self, api_key: str, api_host: str) -> None:
        self._api_key = api_key
        self._api_host = api_host
        # Eventos por (categoría, fecha): varios picks del mismo día
        # reutilizan las categorías ya descargadas. None = fallo de API.
        self._events_cache: dict[tuple[int, str], Optional[list]] = {}
        # Búsqueda por jugador: resultados de /search por nombre y
        # eventos de events/near por id. Mismo criterio: None = fallo.
        self._search_cache: dict[str, Optional[list]] = {}
        self._near_cache: dict[int, Optional[list]] = {}

    async def _fetch_events(
        self, client: httpx.AsyncClient, category_id: int, day: datetime
    ) -> Optional[list]:
        """Eventos de la categoría/día, [] si vacío y None si la llamada
        falló (error transitorio: el caller NO debe marcar `missed`,
        porque la ausencia no es evidencia real)."""
        cache_key = (category_id, day.strftime("%Y-%m-%d"))
        if cache_key in self._events_cache:
            return self._events_cache[cache_key]
        # Los eventos de un día ya terminado son inmutables: se persisten
        # en provider_cache.json y se reutilizan entre pasadas.
        past_day = day.date() < datetime.now(timezone.utc).date()
        persist_key = f"{_PROVIDER_NAME}|cat|{category_id}|{day.strftime('%Y-%m-%d')}"
        if past_day:
            cached = get_event_list(persist_key)
            if cached is not None:
                self._events_cache[cache_key] = cached["events"]
                return cached["events"]
        url = (
            f"https://{self._api_host}/api/tennis/category/{category_id}"
            f"/events/{day.day}/{day.month}/{day.year}"
        )
        try:
            response = await client.get(
                url,
                headers={
                    "X-RapidAPI-Key": self._api_key,
                    "X-RapidAPI-Host": self._api_host,
                },
            )
            response.raise_for_status()
            events: Optional[list] = response.json().get("events") or []
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(_PROVIDER_NAME)
                logger.warning("[TennisApi1] Cuota agotada; se omite hasta mañana")
            else:
                logger.warning("[TennisApi1] Error de API (%s): %s", cache_key, exc)
            events = None
        # Los errores también se cachean dentro de la instancia: un
        # fallo transitorio no debe reintentarse con cada pick del ciclo.
        self._events_cache[cache_key] = events
        if past_day and events:
            set_event_list(persist_key, events)
        return events

    async def _fetch_search(
        self, client: httpx.AsyncClient, name: str
    ) -> Optional[list]:
        """Resultados de `/api/tennis/search/{nombre}`; None si falla."""
        key = name.strip().lower()
        if key in self._search_cache:
            return self._search_cache[key]
        try:
            response = await client.get(
                f"https://{self._api_host}/api/tennis/search/{quote(key)}",
                headers={
                    "X-RapidAPI-Key": self._api_key,
                    "X-RapidAPI-Host": self._api_host,
                },
            )
            response.raise_for_status()
            results: Optional[list] = response.json().get("results") or []
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(_PROVIDER_NAME)
                logger.warning("[TennisApi1] Cuota agotada; se omite hasta mañana")
            else:
                logger.warning("[TennisApi1] Error en search (%s): %s", key, exc)
            results = None
        self._search_cache[key] = results
        return results

    async def _fetch_near(
        self, client: httpx.AsyncClient, player_id: int, pick_date: datetime
    ) -> Optional[list]:
        """`previousEvent` + `nextEvent` del jugador; None si falla.

        El `previousEvent` (último jugado) se persiste en disco: si cubre
        la fecha del pick se reutiliza sin llamar. `nextEvent` NO se
        persiste — es un partido futuro cuyo startTimestamp volvería la
        lista "fresca" eternamente.
        """
        if player_id in self._near_cache:
            return self._near_cache[player_id]
        persist_key = f"{_PROVIDER_NAME}|near|{player_id}"
        cached = get_event_list(persist_key)
        if cached is not None and event_list_covers(cached, pick_date):
            self._near_cache[player_id] = cached["events"]
            return cached["events"]
        prev_event: Optional[dict] = None
        try:
            response = await client.get(
                f"https://{self._api_host}/api/tennis/team/{player_id}" "/events/near",
                headers={
                    "X-RapidAPI-Key": self._api_key,
                    "X-RapidAPI-Host": self._api_host,
                },
            )
            response.raise_for_status()
            data = response.json()
            prev_event = data.get("previousEvent")
            events: Optional[list] = [
                ev for ev in (prev_event, data.get("nextEvent")) if ev
            ]
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(_PROVIDER_NAME)
                logger.warning("[TennisApi1] Cuota agotada; se omite hasta mañana")
            else:
                logger.warning(
                    "[TennisApi1] Error en events/near (%s): %s", player_id, exc
                )
            events = None
        self._near_cache[player_id] = events
        if prev_event:
            set_event_list(persist_key, [prev_event])
        return events

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        if is_rate_limited(_PROVIDER_NAME):
            return None
        # Evento reciente: la ausencia puede ser "aún no terminó" ->
        # fallo provisional (TTL de horas, clave "|prov"). Evento ya
        # pasado: definitivo (15 días).
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{_PROVIDER_NAME}|{date.strftime('%Y-%m-%d')}|{team_hint.strip().lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, None if not provisional else MISSED_TTL_PROVISIONAL):
            return None

        best_match: Optional[MatchResult] = None
        best_score = 0.0
        # Si el evento se ve en el feed pero aún no terminó, la ausencia
        # de resultado NO es un fallo de búsqueda: no se marca missed y
        # el siguiente ciclo del verificador lo reintenta.
        saw_unfinished = False
        saw_error = False

        async with httpx.AsyncClient(timeout=15) as client:
            # 1) Vía por jugador (~2 llamadas): /search resuelve el id —
            #    incluidos nombres parciales ("chidek" -> "Chidekh") — y
            #    events/near trae su partido anterior y siguiente.
            name = _searchable_name(team_hint)
            player_id = get_entity_id(_PROVIDER_NAME, name)
            if player_id is None:
                search = await self._fetch_search(client, name)
                if search is None:
                    return None  # error de API: no se marca missed
                player_id = _best_player_id(name, search)
                if player_id is not None:
                    set_entity_id(_PROVIDER_NAME, name, player_id)
            if player_id is not None:
                near = await self._fetch_near(client, player_id, date)
                if near is None:
                    return None
                for event in near:
                    ts = event.get("startTimestamp")
                    if ts:
                        played = datetime.fromtimestamp(ts, timezone.utc).replace(
                            tzinfo=None
                        )
                        if abs(played - date) > _PLAYER_DATE_TOLERANCE:
                            continue
                    match = _parse_event(event)
                    if match is None:
                        if _event_matches_hint(event, team_hint):
                            saw_unfinished = True
                        continue
                    score = max(
                        _pair_similar(team_hint, match.home_team),
                        _pair_similar(team_hint, match.away_team),
                    )
                    if score > best_score:
                        best_score = score
                        best_match = match
                if best_match and best_score >= _MIN_PLAYER_SIMILARITY:
                    return best_match
                if saw_unfinished:
                    # Su partido está en near pero en vivo: no se marca
                    # missed ni se sigue gastando en el barrido.
                    return None

            # 2) Barrido por categorías (fallback: jugador no
            #    encontrado o sus eventos near no cubren la fecha).
            for offset in _DATE_OFFSETS:
                day = date + timedelta(days=offset)
                for category_id in _CATEGORIES:
                    if is_rate_limited(_PROVIDER_NAME):
                        return None
                    events = await self._fetch_events(client, category_id, day)
                    if events is None:
                        saw_error = True
                        continue
                    for event in events:
                        match = _parse_event(event)
                        if match is None:
                            if _event_matches_hint(event, team_hint):
                                saw_unfinished = True
                            continue
                        score = max(
                            _pair_similar(team_hint, match.home_team),
                            _pair_similar(team_hint, match.away_team),
                        )
                        if score > best_score:
                            best_score = score
                            best_match = match
                    if best_match and best_score >= _MIN_PLAYER_SIMILARITY:
                        return best_match  # early-exit: no seguir gastando

        if not best_match or best_score < _MIN_PLAYER_SIMILARITY:
            if not saw_unfinished and not saw_error:
                mark_missed(miss_key)
            return None
        return best_match
