"""Cuarto proveedor de tenis: allsportsapi2 (Sofascore vía RapidAPI).

Host: allsportsapi2.p.rapidapi.com — mismo backend Sofascore que
tennisapi1 pero con una ruta que tennisapi1 no expone igual:

    /api/tennis/search/{nombre}                 -> id de jugador/pareja
    /api/tennis/team/{id}/events/previous/{pag} -> ~30 eventos jugados
                                                  por página

Por qué existe: tennisapi1 resuelve por jugador solo con `events/near`
(anterior + siguiente): un pick de hace días ya no cae en esa ventana y
el barrido por categorías solo consulta la fecha exacta del pick.
`events/previous` pagina TODO el historial del jugador (~1-2 meses por
página), así que los partidos de Challenger/ITF/dobles que el tipster
publicó la semana pasada siguen localizables.

Cadena de tenis: TheSportsDB -> ATP-WTA-ITF -> tennisapi1 -> allsportsapi2.
Solo se gasta cuando los tres anteriores fallan — que es justo el hueco
de tenis menor.

Cuota: comparte la suscripción `allsportsapi2` con el snapshotter de
cuotas (`app/services/odds`), así que el NAME es el mismo a propósito:
si las cuotas agotan la cuota diaria, este proveedor también se salta
(y viceversa). Las claves de `missed` van namespacetas con `results|`
para no colisionar con las `odds|allsportsapi2|...` de la otra.

Respuesta verificada en vivo (2026-09-20, plan BASIC): el primer evento
de `previous` de Carreño Busta era un ITF ("Montemar, Spain") con
`status.type="finished"`, `homeScore.current` = sets ganados y
`periodN` = juegos por set — el desglose completo que el verificador
necesita para mercados de juegos/sets.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from app.core.logging import get_logger
from app.services.results.api_tennis import _player_similar
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    SOFASCORE_VOIDED_STATUSES,
    MatchResult,
    MatchState,
    fold_name,
    is_missed,
    is_rate_limited,
    log_remaining_quota,
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
from app.services.results.tennisapi1 import (
    _MIN_PLAYER_SIMILARITY,
    _parse_event,
    _searchable_name,
)

logger = get_logger("app.results.allsports_tennis")

# Mismo nombre que el snapshotter de cuotas: la suscripción RapidAPI es
# la misma y el rate-limit de `provider_state.json` debe ser compartido.
_PROVIDER_NAME = "allsportsapi2"
# El pick puede llevar el día de publicación, no el del partido.
_DATE_TOLERANCE = timedelta(days=1)
# Páginas de events/previous (~30 eventos cada una). Con 2 páginas se
# cubren ~2 meses de un jugador que compite cada semana; los pendientes
# de verificación no pasan de ~2 semanas.
_MAX_PAGES = 2
# Lados del cruce SIN romper las parejas de dobles ("Arends / Pel"):
# solo separa en " - " y " vs ".
_SIDE_SEPARATOR = re.compile(r"\s+-\s+|\s+vs\.?\s+", re.IGNORECASE)


def _hint_sides(team_hint: str) -> list[str]:
    """Lados del cruce: ["Arends / Pel", "Rojer / Tecau"] conserva las
    parejas; ["Alcaraz", "Sinner"] para individuales."""
    sides = [s.strip() for s in _SIDE_SEPARATOR.split(team_hint) if s.strip()]
    return sides or [team_hint.strip()]


_PAIR_SPLIT = re.compile(r"\s*[/+&]\s*")


def _side_score(side: str, entity_name: str) -> float:
    """Similitud lado-del-cruce vs nombre de entidad/equipo.

    `_pair_similar` delega en `_player_similar`, que ignora tokens de
    <4 letras — un apellido corto ("Pel", "Ion") nunca casa y los
    dobles se perdían. Aquí una pareja casa si cada miembro aporta
    algún token >=3 letras presente en el nombre real, y se exige el
    mismo número de miembros en ambos lados: un individual nunca casa
    con un dobles ni al revés.
    """
    side_members = [m.strip() for m in _PAIR_SPLIT.split(side) if m.strip()]
    entity_members = [m.strip() for m in _PAIR_SPLIT.split(entity_name) if m.strip()]
    if len(side_members) != len(entity_members):
        return 0.0
    if len(entity_members) == 1:
        return _player_similar(side, entity_name)
    entity_tokens = set(re.findall(r"\w+", fold_name(entity_name)))
    matched = sum(
        1
        for member in side_members
        if any(
            token in entity_tokens
            for token in fold_name(member).split()
            if len(token) >= 3
        )
    )
    return 0.9 if matched == len(side_members) else 0.0


def _best_entity_id(side: str, results: list) -> Optional[int]:
    """Id de la mejor entidad de tenis para un LADO del cruce
    (jugador individual o pareja de dobles)."""
    best_id: Optional[int] = None
    best_score = 0.0
    for res in results:
        entity = res.get("entity") or {}
        if (entity.get("sport") or {}).get("slug") != "tennis":
            continue
        score = _side_score(side, entity.get("name") or "")
        if score > best_score:
            best_score = score
            best_id = entity.get("id")
    return best_id if best_score >= _MIN_PLAYER_SIMILARITY else None


def _hint_match_score(team_hint: str, home: str, away: str) -> float:
    """Confianza de que el evento (home vs away) es el del hint.

    Igual que `match_score` de base.py pero separando por `_hint_sides`
    — que conserva las parejas — y puntuando con `_side_score`, así
    "Arends / Pel - Rojer / Tecau" empareja cada pareja con su equipo
    en lugar de romper los nombres.
    """
    sides = _hint_sides(team_hint)
    if len(sides) == 1:
        return max(_side_score(sides[0], home), _side_score(sides[0], away))
    direct = min(_side_score(sides[0], home), _side_score(sides[1], away))
    cross = min(_side_score(sides[0], away), _side_score(sides[1], home))
    return max(direct, cross)


class AllSportsTennisProvider:
    """Resultados de tenis vía allsportsapi2 (historial paginado)."""

    NAME = _PROVIDER_NAME
    SUPPORTED_SPORTS = frozenset({"tenis"})

    def __init__(self, api_key: str, api_host: str) -> None:
        self._api_key = api_key
        self._api_host = api_host
        self._search_cache: dict[str, Optional[list]] = {}
        self._events_cache: dict[int, Optional[list]] = {}

    # --- HTTP ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._api_host,
        }

    async def _get_json(self, client: httpx.AsyncClient, path: str) -> Optional[dict]:
        """GET con gestión de cuota unificada: 403/429 marca la
        suscripción sin cuota hasta mañana (compartida con odds)."""
        try:
            response = await client.get(
                f"https://{self._api_host}{path}", headers=self._headers()
            )
            response.raise_for_status()
            log_remaining_quota(_PROVIDER_NAME, response)
            data = response.json()
            return data if isinstance(data, dict) else None
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(_PROVIDER_NAME, exc.response)
                logger.warning(
                    "[ALLSPORTS-TENNIS] Cuota agotada; se omite hasta mañana"
                )
            else:
                logger.warning("[ALLSPORTS-TENNIS] Error de API (%s): %s", path, exc)
            return None

    async def _search(self, client: httpx.AsyncClient, name: str) -> Optional[list]:
        """Entidades candidatas por nombre; None si la llamada falló."""
        key = name.strip().lower()
        if key in self._search_cache:
            return self._search_cache[key]
        data = await self._get_json(client, f"/api/tennis/search/{quote(key)}")
        results = None if data is None else (data.get("results") or [])
        self._search_cache[key] = results
        return results

    async def _previous_events(
        self, client: httpx.AsyncClient, team_id: int, need_date: datetime
    ) -> Optional[list]:
        """Historial de partidos del jugador/pareja paginado; None si
        alguna llamada falló (error transitorio -> no se marca missed).

        Reutiliza la lista persistida en `provider_cache.json` cuando
        cubre `need_date` (ver `event_list_covers`); si el partido pudo
        jugarse tras la captura se refetchea y se sobrescribe.
        """
        if team_id in self._events_cache:
            return self._events_cache[team_id]
        cache_key = f"{self.NAME}|{team_id}"
        cached = get_event_list(cache_key)
        if cached is not None and event_list_covers(cached, need_date):
            self._events_cache[team_id] = cached["events"]
            return cached["events"]
        events: list = []
        for page in range(_MAX_PAGES):
            data = await self._get_json(
                client, f"/api/tennis/team/{team_id}/events/previous/{page}"
            )
            if data is None:
                self._events_cache[team_id] = None
                return None
            batch = data.get("events") or []
            events.extend(batch)
            if len(batch) < 30:  # última página
                break
        self._events_cache[team_id] = events
        if events:
            set_event_list(cache_key, events)
        return events

    # --- Interfaz ResultsProvider -----------------------------------------

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """El partido finalizado que mejor casa con la pista en la fecha
        (±1 día), buscado en el historial paginado del jugador/pareja."""
        if is_rate_limited(_PROVIDER_NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{_PROVIDER_NAME}|results|tenis|{date.strftime('%Y-%m-%d')}"
            f"|{hint.lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        best_match: Optional[MatchResult] = None
        best_score = 0.0
        saw_unfinished = False
        saw_error = False
        seen_ids: set[int] = set()

        async with httpx.AsyncClient(timeout=15) as client:
            # Búsqueda perezosa por cada LADO del cruce (parejas de
            # dobles intactas): con "A vs B" solo se consulta al segundo
            # si el historial del primero no bastó.
            for side in _hint_sides(hint)[:2]:
                if is_rate_limited(_PROVIDER_NAME):
                    return None
                name = _searchable_name(side)
                entity_id = get_entity_id(self.NAME, name)
                if entity_id is None:
                    results = await self._search(client, name)
                    if results is None:
                        saw_error = True
                        continue
                    entity_id = _best_entity_id(side, results)
                    if entity_id is not None:
                        set_entity_id(self.NAME, name, entity_id)
                if entity_id is None or entity_id in seen_ids:
                    continue
                seen_ids.add(entity_id)
                events = await self._previous_events(client, entity_id, date)
                if events is None:
                    saw_error = True
                    continue
                for event in events:
                    ts = event.get("startTimestamp")
                    if ts:
                        played = datetime.fromtimestamp(ts, timezone.utc).replace(
                            tzinfo=None
                        )
                        if abs(played - date) > _DATE_TOLERANCE:
                            continue
                    match = _parse_event(event)
                    if match is None:
                        if (
                            _hint_match_score(
                                hint,
                                (event.get("homeTeam") or {}).get("name") or "",
                                (event.get("awayTeam") or {}).get("name") or "",
                            )
                            >= _MIN_PLAYER_SIMILARITY
                        ):
                            saw_unfinished = True
                        continue
                    score = _hint_match_score(hint, match.home_team, match.away_team)
                    if score > best_score:
                        best_score = score
                        best_match = match
                if best_match and best_score >= _MIN_PLAYER_SIMILARITY:
                    return best_match  # early-exit: no seguir gastando

        if best_match is None or best_score < _MIN_PLAYER_SIMILARITY:
            # El partido visto pero en vivo no es fallo de búsqueda: se
            # reintenta en el siguiente ciclo, no se marca missed.
            if not saw_unfinished and not saw_error:
                mark_missed(miss_key)
            return None
        return best_match

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """Partido aplazado/cancelado en el historial del jugador/pareja.
        Reusa las cachés de `find_match` (search + previous): corre tras
        la cadena de resultados y casi nunca cuesta llamadas extra."""
        if is_rate_limited(_PROVIDER_NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            for side in _hint_sides(hint)[:2]:
                if is_rate_limited(_PROVIDER_NAME):
                    return None
                name = _searchable_name(side)
                entity_id = get_entity_id(self.NAME, name)
                if entity_id is None:
                    results = await self._search(client, name)
                    if results is None:
                        continue
                    entity_id = _best_entity_id(side, results)
                    if entity_id is not None:
                        set_entity_id(self.NAME, name, entity_id)
                if entity_id is None:
                    continue
                events = await self._previous_events(client, entity_id, date)
                if events is None:
                    continue
                for event in events:
                    ts = event.get("startTimestamp")
                    if ts:
                        played = datetime.fromtimestamp(ts, timezone.utc).replace(
                            tzinfo=None
                        )
                        if abs(played - date) > _DATE_TOLERANCE:
                            continue
                    status = (event.get("status") or {}).get("type")
                    if status not in SOFASCORE_VOIDED_STATUSES:
                        continue
                    home = (event.get("homeTeam") or {}).get("name") or ""
                    away = (event.get("awayTeam") or {}).get("name") or ""
                    if _hint_match_score(hint, home, away) < _MIN_PLAYER_SIMILARITY:
                        continue
                    return MatchState(
                        home_team=home,
                        away_team=away,
                        status=SOFASCORE_VOIDED_STATUSES[status],
                    )
        return None
