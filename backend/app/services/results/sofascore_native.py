"""Providers sobre la API nativa de Sofascore (`/api/v1/...`).

Los mirrors RapidAPI (allsportsapi2, tennisapi1, footapi7) son proxies
de la API que usa la propia web de Sofascore. Esa API responde en
`www.sofascore.com/api/v1/` con el MISMO formato JSON, pero protegida
por Cloudflare con fingerprinting TLS: `httpx`/`curl` reciben 403,
`curl_cffi` con `impersonate="chrome"` atraviesa (verificado en vivo
2026-09-23: search, events/last, event y odds/1/all -> 200).

Dos transportes sobre las mismas rutas:

- `direct`:  www.sofascore.com via curl_cffi — cuota ILIMITADA, sin
             key. Riesgo: API interna — Cloudflare puede endurecerse o
             banear la IP si el volumen levanta sospechas. Por eso va
             como ÚLTIMO recurso en resultados Y en odds: las cuotas
             renovables de RapidAPI se gastan primero y el directo
             solo absorbe el desbordamiento. Ante un desafío no se
             insiste: se marca sin cuota y se reintenta mañana.
- `rapidapi`: espejos que exponen las rutas nativas bajo RapidAPI
             (sportapi7). Misma key de cuenta; suscripción aparte de
             100/día que el usuario activa a mano. Si no está
             suscrito, el primer 403 lo marca sin cuota y se salta.

Las entidades/eventos son el espacio de ids de Sofascore en ambos
transportes: el caché `provider_cache.json` se comparte bajo el
namespace "sofascore" — un id resuelto por un transporte sirve a
todos (y a los mirrors wrapper que usan los mismos ids de evento).

Rutas nativas (mapeo de las del wrapper allsportsapi2):

    /api/tennis/search/{n}              -> /api/v1/search/all?q={n}
    /api/search/{n}                     -> /api/v1/search/all?q={n}
    /api/tennis/team/{id}/events/previous/{p} -> /api/v1/team/{id}/events/last/{p}
    /api/tennis/event/{id}/odds         -> /api/v1/event/{id}/odds/1/all
    /api/match/{id}/odds                -> /api/v1/event/{id}/odds/1/all

`events/next/{p}` devuelve 404 cuando la entidad no tiene próximos
partidos — se traduce a lista vacía, no a error.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol
from urllib.parse import quote

import httpx
from curl_cffi import requests as cffi_requests

from app.core.logging import get_logger
from app.services.odds.base import EventRef, MarketChoice, odds_to_decimal
from app.services.results.allsports_tennis import _hint_sides, _side_score
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    SOFASCORE_VOIDED_STATUSES,
    MatchResult,
    MatchState,
    is_missed,
    is_rate_limited,
    log_remaining_quota,
    mark_missed,
    mark_rate_limited,
    match_score,
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

logger = get_logger("app.results.sofascore_native")

_BASE = "https://www.sofascore.com"
_SPORT_SLUGS = {"tenis": "tennis", "futbol": "football", "baloncesto": "basketball"}
_DATE_TOLERANCE = timedelta(days=1)
# Páginas de events/last (~30 eventos cada una) — igual que allsports.
_MAX_PAGES = 2
# Namespace de caché compartido entre transportes: los ids son los
# mismos en Sofascore lo llame quien lo llame.
_CACHE_NS = "sofascore"
# Timeout algo generoso: Cloudflare a veces desafía antes de servir.
_TIMEOUT = 20


# --- Transportes ----------------------------------------------------------


class _Transport(Protocol):
    """GET(path) -> dict JSON o None. Los errores de cuota/baneo se
    marcan por nombre de suscripción (rate_limit) como en el resto."""

    name: str

    async def get_json(self, path: str) -> Optional[dict]: ...


class _DirectTransport:
    """www.sofascore.com vía curl_cffi (TLS fingerprint de Chrome).

    Cuidados anti-baneo, deliberados:

    - Sesión persistente: conserva las cookies `cf_clearance` que
      Cloudflare emite tras un desafío superado — sin ellas cada
      llamada volvería a desafiar y la ráfaga de desafíos es justo
      lo que escala a baneo de IP.
    - Headers de navegador (Referer/Accept): la API espera venir del
      frontend; pedir "a pelo" destaca el fingerprint.
    - Paso de cortesía: mínimo `_MIN_INTERVAL` segundos entre llamadas
      — un navegador real no dispara peticiones a ráfaga; las ráfagas
      son lo que marca el fingerprint de bot.
    - Un 200 con HTML (página de desafío, no JSON) marca rate-limited:
      seguir llamando a través de un challenge es la forma rápida de
      caer en la lista negra. Se reintenta mañana.
    """

    name = "sofascore_direct"
    _session: Optional[object] = None

    # Segundos mínimos entre llamadas directas — anti-ráfaga.
    # Conservador a propósito: el directo solo ve desbordamiento de
    # las cuotas, así que ir lento no cuesta nada y reduce el
    # fingerprint de bot ante Cloudflare.
    _MIN_INTERVAL = 1.5
    _pace_lock = asyncio.Lock()
    _last_call = 0.0

    _HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.sofascore.com/",
    }

    # Dedup de llamadas EN VUELO: si dos picks concurrentes piden la
    # misma ruta con la caché fría, comparten UNA petición en vez de
    # disparar dos idénticas — menos volumen ante Cloudflare.
    _inflight: dict = {}

    @classmethod
    def _get_session(cls):
        if cls._session is None:
            cls._session = cffi_requests.Session(impersonate="chrome")
        return cls._session

    async def get_json(self, path: str) -> Optional[dict]:
        if is_rate_limited(self.name):
            return None
        task = self._inflight.get(path)
        if task is not None:
            return await task
        task = asyncio.ensure_future(self._fetch_once(path))
        self._inflight[path] = task
        try:
            return await task
        finally:
            self._inflight.pop(path, None)

    async def _fetch_once(self, path: str) -> Optional[dict]:
        async with self._pace_lock:
            wait = self._MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            type(self)._last_call = time.monotonic()
        try:
            session = self._get_session()
            response = await asyncio.to_thread(
                session.get,
                f"{_BASE}{path}",
                headers=self._HEADERS,
                timeout=_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 — curl_cffi lanza varios
            logger.warning("[SOFASCORE-DIRECT] Error de red (%s): %s", path, exc)
            return None
        if response.status_code == 404:
            return {}  # página vacía (p. ej. events/next sin próximos)
        if response.status_code in (401, 403, 429):
            mark_rate_limited(self.name)
            logger.warning(
                "[SOFASCORE-DIRECT] Bloqueado por Cloudflare (%s); "
                "se omite hasta mañana",
                response.status_code,
            )
            return None
        if response.status_code != 200:
            logger.warning(
                "[SOFASCORE-DIRECT] HTTP %s en %s", response.status_code, path
            )
            return None
        try:
            data = response.json()
        except ValueError:
            # 200 pero HTML: página de desafío de Cloudflare. No
            # insistir — marca sin cuota hasta mañana igual que un 403.
            mark_rate_limited(self.name)
            logger.warning(
                "[SOFASCORE-DIRECT] Respuesta no-JSON (desafío "
                "Cloudflare); se omite hasta mañana"
            )
            return None
        return data if isinstance(data, dict) else None


class _RapidApiTransport:
    """Espejo RapidAPI que expone rutas nativas `/api/v1` (sportapi7).

    Sin suscripción el primer 403 lo marca sin cuota hasta mañana —
    basta con activar el plan BASIC gratis en RapidAPI para que entre
    en la cadena sin tocar código.
    """

    def __init__(self, name: str, host: str, api_key: str) -> None:
        self.name = name
        self._host = host
        self._headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": host,
        }

    async def get_json(self, path: str) -> Optional[dict]:
        if is_rate_limited(self.name):
            return None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(
                    f"https://{self._host}{path}", headers=self._headers
                )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            log_remaining_quota(self.name, response)
            data = response.json()
            return data if isinstance(data, dict) else None
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(self.name)
                logger.warning(
                    "[SOFASCORE:%s] Cuota agotada o sin suscripción; "
                    "se omite hasta mañana",
                    self.name,
                )
            else:
                logger.warning(
                    "[SOFASCORE:%s] Error de API (%s): %s", self.name, path, exc
                )
            return None


class _SofaScore6Transport:
    """Espejo RapidAPI `sofascore6`: mismos ids de Sofascore pero rutas
    y formas de respuesta propias (verificado en vivo 2026-09-23).

    Traduce las rutas nativas `/api/v1` que emite el provider a su
    esquema real y reenvuelve la respuesta al formato nativo — el
    provider no nota la diferencia:

        /api/v1/search/all?q={n}              -> /api/sofascore/v1/search/all?q={n}
            respuesta: lista -> {"results": [...]}
        /api/v1/team/{id}/events/last/{p}     -> /api/sofascore/v1/team/matches/finished?team_id=&page=
        /api/v1/team/{id}/events/next/{p}     -> /api/sofascore/v1/team/matches/upcoming?team_id=&page=
            respuesta: {"matches": [...]} -> {"events": [...]} con
            `timestamp` renombrado a `startTimestamp`
        /api/v1/event/{id}/odds/1/all         -> /api/sofascore/v1/match/odds?match_id=
            respuesta: lista de mercados -> {"markets": [...]} con
            `name` -> marketName y `value.decimal` -> fractionalValue
            (odds_to_decimal acepta el decimal directo)
    """

    def __init__(self, host: str, api_key: str, name: str = "sofascore6") -> None:
        self.name = name
        self._host = host
        self._headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": host,
        }

    @staticmethod
    def _map_path(path: str) -> Optional[str]:
        """Ruta nativa -> ruta sofascore6, o None si no hay equivalente."""
        import re

        m = re.match(r"^/api/v1/search/all\?q=(.+)$", path)
        if m:
            return f"/api/sofascore/v1/search/all?q={m.group(1)}"
        m = re.match(r"^/api/v1/team/(\d+)/events/(last|next)/(\d+)$", path)
        if m:
            kind = "finished" if m.group(2) == "last" else "upcoming"
            return (
                f"/api/sofascore/v1/team/matches/{kind}"
                f"?team_id={m.group(1)}&page={m.group(3)}"
            )
        m = re.match(r"^/api/v1/event/(\d+)/odds/1/all$", path)
        if m:
            return f"/api/sofascore/v1/match/odds?match_id={m.group(1)}"
        return None

    @staticmethod
    def _normalize(path: str, data: object) -> Optional[dict]:
        """Respuesta sofascore6 -> forma nativa que espera el provider."""
        if "/search/all" in path:
            return {"results": data if isinstance(data, list) else []}
        if "/events/" in path:
            if not isinstance(data, dict):
                return None
            events = []
            for ev in data.get("matches") or []:
                if isinstance(ev, dict) and "timestamp" in ev:
                    ev["startTimestamp"] = ev["timestamp"]
                events.append(ev)
            return {"events": events}
        if "/odds/" in path:
            markets = []
            for mkt in data if isinstance(data, list) else []:
                choices = []
                for ch in mkt.get("choices") or []:
                    choices.append(
                        {
                            "name": ch.get("name"),
                            # odds_to_decimal acepta numérico directo
                            "fractionalValue": (ch.get("value") or {}).get("decimal"),
                            "initialFractionalValue": (
                                ch.get("initialValue") or {}
                            ).get("decimal"),
                        }
                    )
                markets.append(
                    {
                        "marketName": mkt.get("name"),
                        "choiceGroup": mkt.get("choiceGroup"),
                        "isLive": mkt.get("isLive"),
                        "suspended": mkt.get("suspended"),
                        "choices": choices,
                    }
                )
            return {"markets": markets}
        return data if isinstance(data, dict) else None

    async def get_json(self, path: str) -> Optional[dict]:
        if is_rate_limited(self.name):
            return None
        mapped = self._map_path(path)
        if mapped is None:
            logger.warning("[SOFASCORE6] Ruta nativa sin equivalente: %s", path)
            return {}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(
                    f"https://{self._host}{mapped}", headers=self._headers
                )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            log_remaining_quota(self.name, response)
            return self._normalize(path, response.json())
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(self.name)
                logger.warning(
                    "[SOFASCORE:%s] Cuota agotada o sin suscripción; "
                    "se omite hasta mañana",
                    self.name,
                )
            else:
                logger.warning(
                    "[SOFASCORE:%s] Error de API (%s): %s", self.name, path, exc
                )
            return None


def rapidapi_transport(name: str, host: str, api_key: str) -> _RapidApiTransport:
    """Factoría pública para registrar espejos desde la configuración."""
    return _RapidApiTransport(name=name, host=host, api_key=api_key)


def sofascore6_transport(host: str, api_key: str) -> _SofaScore6Transport:
    """Factoría para el espejo sofascore6 (rutas propias)."""
    return _SofaScore6Transport(host=host, api_key=api_key)


def direct_transport() -> _DirectTransport:
    return _DirectTransport()


# --- Helpers compartidos ---------------------------------------------------


def _best_entity_id(side: str, sport_slug: str, results: list) -> Optional[int]:
    """Id de la mejor entidad de `results` del deporte pedido."""
    best_id: Optional[int] = None
    best_score = 0.0
    for res in results:
        entity = res.get("entity") or {}
        if (entity.get("sport") or {}).get("slug") != sport_slug:
            continue
        score = _side_score(side, entity.get("name") or "")
        if score > best_score:
            best_score = score
            best_id = entity.get("id")
    return best_id if best_score >= _MIN_PLAYER_SIMILARITY else None


def _event_date(event: dict) -> Optional[datetime]:
    ts = event.get("startTimestamp")
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _event_score(sport: str, team_hint: str, event: dict) -> float:
    """Confianza de que el evento es el del hint, por deporte."""
    home = (event.get("homeTeam") or {}).get("name") or ""
    away = (event.get("awayTeam") or {}).get("name") or ""
    if not home or not away:
        return 0.0
    if sport == "tenis":
        return max(_side_score(team_hint, home), _side_score(team_hint, away))
    return match_score(team_hint, home, away)


def _finished_result(sport: str, event: dict) -> Optional[MatchResult]:
    """Evento terminado -> MatchResult para fútbol/basket (tenis usa
    `_parse_event` de tennisapi1, que además extrae sets)."""
    if sport == "tenis":
        return _parse_event(event)
    status = (event.get("status") or {}).get("type")
    if status != "finished":
        return None
    try:
        home_score = int((event.get("homeScore") or {})["current"])
        away_score = int((event.get("awayScore") or {})["current"])
    except (KeyError, TypeError, ValueError):
        return None
    return MatchResult(
        home_team=(event.get("homeTeam") or {}).get("name") or "",
        away_team=(event.get("awayTeam") or {}).get("name") or "",
        home_score=home_score,
        away_score=away_score,
    )


# --- Provider de resultados -------------------------------------------------


class SofaScoreNativeResultsProvider:
    """Resultados por historial paginado (`events/last`) — una
    instancia por deporte y por transporte (NAME separa cuotas)."""

    def __init__(self, transport: _Transport, sport: str) -> None:
        self.NAME = transport.name
        self.SUPPORTED_SPORTS = frozenset({sport})
        self._transport = transport
        self._sport = sport
        self._slug = _SPORT_SLUGS[sport]

    async def _search(self, name: str) -> Optional[list]:
        data = await self._transport.get_json(
            f"/api/v1/search/all?q={quote(name.strip().lower())}"
        )
        if data is None:
            return None
        return data.get("results") or []

    async def _previous_events(
        self, team_id: int, need_date: datetime
    ) -> Optional[list]:
        cache_key = f"{_CACHE_NS}|{team_id}"
        cached = get_event_list(cache_key)
        if cached is not None and event_list_covers(cached, need_date):
            return cached["events"]
        events: list = []
        for page in range(_MAX_PAGES):
            data = await self._transport.get_json(
                f"/api/v1/team/{team_id}/events/last/{page}"
            )
            if data is None:
                return None
            batch = data.get("events") or []
            events.extend(batch)
            if len(batch) < 30:
                break
        if events:
            set_event_list(cache_key, events)
        return events

    async def _events_for_hint(self, hint: str, date: datetime) -> tuple[list, bool]:
        """(eventos candidatos, hubo_error): búsqueda por cada lado del
        cruce como en allsports_tennis."""
        events: list = []
        saw_error = False
        seen: set[int] = set()
        for side in _hint_sides(hint)[:2]:
            if is_rate_limited(self.NAME):
                break
            name = _searchable_name(side)
            entity_id = get_entity_id(_CACHE_NS, name)
            if entity_id is None:
                results = await self._search(name)
                if results is None:
                    saw_error = True
                    continue
                entity_id = _best_entity_id(side, self._slug, results)
                if entity_id is not None:
                    set_entity_id(_CACHE_NS, name, entity_id)
            if entity_id is None or entity_id in seen:
                continue
            seen.add(entity_id)
            found = await self._previous_events(entity_id, date)
            if found is None:
                saw_error = True
                continue
            events.extend(found)
            if events:
                break  # early-exit: un lado basta si tiene historial
        return events, saw_error

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        if is_rate_limited(self.NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{self.NAME}|results|{self._sport}|{date.strftime('%Y-%m-%d')}"
            f"|{hint.lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        events, saw_error = await self._events_for_hint(hint, date)
        best: Optional[MatchResult] = None
        best_score = 0.0
        saw_unfinished = False
        for event in events:
            played = _event_date(event)
            if played and abs(played - date) > _DATE_TOLERANCE:
                continue
            match = _finished_result(self._sport, event)
            if match is None:
                if _event_score(self._sport, hint, event) >= _MIN_PLAYER_SIMILARITY:
                    saw_unfinished = True
                continue
            score = _event_score(self._sport, hint, event)
            if score > best_score:
                best, best_score = match, score

        if best is None or best_score < _MIN_PLAYER_SIMILARITY:
            if not saw_unfinished and not saw_error:
                mark_missed(miss_key)
            return None
        return best

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        if is_rate_limited(self.NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        events, _ = await self._events_for_hint(hint, date)
        for event in events:
            played = _event_date(event)
            if played and abs(played - date) > _DATE_TOLERANCE:
                continue
            status = (event.get("status") or {}).get("type")
            if status not in SOFASCORE_VOIDED_STATUSES:
                continue
            if _event_score(self._sport, hint, event) < _MIN_PLAYER_SIMILARITY:
                continue
            return MatchState(
                home_team=(event.get("homeTeam") or {}).get("name") or "",
                away_team=(event.get("awayTeam") or {}).get("name") or "",
                status=SOFASCORE_VOIDED_STATUSES[status],
            )
        return None


# --- Provider de odds --------------------------------------------------------


class SofaScoreNativeOddsProvider:
    """Odds de mercado por rutas nativas (search -> last/next -> odds).

    `find_event` compone prev/next desde `events/last/0` +
    `events/next/0` (los eventos cercanos de la entidad, tolerancia
    ±1 día como el wrapper).
    """

    def __init__(self, transport: _Transport, sports: frozenset) -> None:
        self.NAME = transport.name
        self.SUPPORTED_SPORTS = sports
        self._transport = transport

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        import re

        if sport not in self.SUPPORTED_SPORTS or is_rate_limited(self.NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"odds|{self.NAME}|{sport}|{date.strftime('%Y-%m-%d')}|{hint.lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, None if not provisional else MISSED_TTL_PROVISIONAL):
            return None

        slug = _SPORT_SLUGS[sport]
        side = (
            re.split(r"[/+&]|\s+-\s+|\s+vs\.?\s+", hint, maxsplit=1)[0].strip() or hint
        )
        name = _searchable_name(side)
        found: Optional[EventRef] = None
        entity_id = get_entity_id(_CACHE_NS, name)
        if entity_id is None:
            data = await self._transport.get_json(
                f"/api/v1/search/all?q={quote(name.strip().lower())}"
            )
            if data is None:
                return None
            entity_id = _best_entity_id(side, slug, data.get("results") or [])
            if entity_id is not None:
                set_entity_id(_CACHE_NS, name, entity_id)
        if entity_id is not None:
            candidates: list = []
            for path in (
                f"/api/v1/team/{entity_id}/events/last/0",
                f"/api/v1/team/{entity_id}/events/next/0",
            ):
                data = await self._transport.get_json(path)
                if data is not None:
                    candidates.extend(data.get("events") or [])
            best_score = 0.0
            best_event: Optional[dict] = None
            for event in candidates:
                played = _event_date(event)
                if played and abs(played - date) > _DATE_TOLERANCE:
                    continue
                score = _event_score(sport, hint, event)
                if score > best_score:
                    best_score = score
                    best_event = event
            if best_event is not None and best_score >= _MIN_PLAYER_SIMILARITY:
                found = EventRef(
                    event_ext_id=f"sofascore:{best_event.get('id')}",
                    home_team=(best_event.get("homeTeam") or {}).get("name") or "",
                    away_team=(best_event.get("awayTeam") or {}).get("name") or "",
                    start=_event_date(best_event),
                )

        if found is None:
            mark_missed(miss_key)
        return found

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        """Todos los mercados del evento (mismo payload que el wrapper)."""
        if is_rate_limited(self.NAME):
            return None
        try:
            _, raw_id = event_ext_id.split(":", 1)
        except ValueError:
            return None
        data = await self._transport.get_json(f"/api/v1/event/{raw_id}/odds/1/all")
        if data is None:
            return None
        choices: list[MarketChoice] = []
        for market in data.get("markets") or []:
            market_name = market.get("marketName") or ""
            if not market_name:
                continue
            for choice in market.get("choices") or []:
                choices.append(
                    MarketChoice(
                        market_name=market_name,
                        choice_name=choice.get("name") or "",
                        cuota=odds_to_decimal(choice.get("fractionalValue")),
                        cuota_apertura=odds_to_decimal(
                            choice.get("initialFractionalValue")
                        ),
                        choice_group=market.get("choiceGroup"),
                        is_live=bool(market.get("isLive")),
                        suspended=bool(market.get("suspended")),
                    )
                )
        return choices
