"""Proveedor de odds de la familia Sofascore vía RapidAPI.

Sirve para los dos hosts ya verificados en vivo:

- `allsportsapi2.p.rapidapi.com` (AllSportsApi, BASIC $0): tenis y
  fútbol. Es la fuente dedicada de odds — su cuota diaria es propia y
  no compite con la verificación de resultados.
- `tennisapi1.p.rapidapi.com` (TennisApi): solo tenis. Mismo espacio
  de ids de evento (backend Sofascore común), así un evento resuelto
  por allsportsapi2 lo puede consultar tennisapi1 — funciona como
  scavenger cuando la dedicada agota su cuota.

Rutas (verificadas 2026-09-18):

- Tenis:  `/api/tennis/search/{nombre}` -> id de jugador
          `/api/tennis/team/{id}/events/near` -> prev/next del jugador
          `/api/tennis/event/{id}/odds` -> mercados
- Fútbol: `/api/search/{equipo}` -> id de equipo (búsqueda global)
          `/api/team/{id}/matches/near` -> prev/next del equipo
          `/api/match/{id}/odds` -> mercados

Respuesta de odds: `markets[]` con `marketName`, `choiceGroup`
(la línea en mercados de línea: "22.5", "+0.75"), `isLive`,
`suspended` y `choices[]` con `name`, `fractionalValue` (actual) e
`initialFractionalValue` (apertura — gratis en cada llamada). Las
cuotas son fraccionales tipo bet365 ("6/5") -> decimal en `base.py`.

Los eventos ya terminados siguen devolviendo sus mercados: un fetch
post-partido recupera apertura + cierre — es el "mini-backfill"
gratis que cubre huecos por caídas cortas del backend.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from app.core.logging import get_logger
from app.services.odds.base import EventRef, MarketChoice, odds_to_decimal
from app.services.results.api_tennis import _pair_similar
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    is_missed,
    is_rate_limited,
    log_remaining_quota,
    mark_missed,
    mark_rate_limited,
    match_score,
    miss_is_provisional,
    rate_limit_from,
)

logger = get_logger("app.odds.sofascore")

_FAMILY = "sofascore"
_MIN_SIMILARITY = 0.6
# El near trae el partido anterior y el siguiente: el del pick debe
# caer dentro (misma tolerancia que el proveedor de resultados).
_DATE_TOLERANCE = timedelta(days=1)


class SofaScoreOddsProvider:
    # Prefijo del espacio de ids de la familia Sofascore — el
    # snapshotter salta este provider con ids de otra familia (espn:*).
    ID_PREFIX = "sofascore:"
    """Cliente de odds para hosts de la familia Sofascore.

    Una instancia por suscripción RapidAPI: el `name` va a
    `provider_state.json` (cuotas independientes por suscripción) y
    `sports` limita lo que el host sabe resolver (allsportsapi2:
    tenis+fútbol; tennisapi1: solo tenis).
    """

    def __init__(
        self,
        name: str,
        api_key: str,
        api_host: str,
        sports: frozenset = frozenset({"tenis", "futbol"}),
    ) -> None:
        self.NAME = name
        self.SUPPORTED_SPORTS = sports
        self._api_key = api_key
        self._api_host = api_host
        # Cachés por instancia: un fallo de API no se reintenta dentro
        # del mismo ciclo (None = error, distinto de "no encontrado").
        self._search_cache: dict[tuple[str, str], Optional[list]] = {}
        self._near_cache: dict[tuple[str, int], Optional[list]] = {}

    # --- HTTP ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._api_host,
        }

    async def _get_json(self, client: httpx.AsyncClient, path: str) -> Optional[dict]:
        """GET con manejo de cuota unificado: 403/429 marca el
        proveedor sin cuota hasta mañana y devuelve None (como los
        proveedores de resultados). Otros errores también -> None."""
        try:
            response = await client.get(
                f"https://{self._api_host}{path}", headers=self._headers()
            )
            # Antes del raise: también cuenta la llamada y captura el
            # límite aunque sea 429 (sin esto el panel solo veía las
            # llamadas del lado de resultados, no las del snapshotter).
            log_remaining_quota(self.NAME, response)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(self.NAME, exc.response)
                logger.warning(
                    "[ODDS:%s] Cuota agotada; se omite hasta mañana", self.NAME
                )
            else:
                logger.warning("[ODDS:%s] Error de API (%s): %s", self.NAME, path, exc)
            return None

    # --- Resolución de evento --------------------------------------------

    async def _search(
        self, client: httpx.AsyncClient, sport: str, name: str
    ) -> Optional[list]:
        """Entidades candidatas por nombre; None si la llamada falló."""
        key = (sport, name.strip().lower())
        if key in self._search_cache:
            return self._search_cache[key]
        path = (
            f"/api/tennis/search/{quote(key[1])}"
            if sport == "tenis"
            else f"/api/search/{quote(key[1])}"
        )
        data = await self._get_json(client, path)
        results = None if data is None else (data.get("results") or [])
        self._search_cache[key] = results
        return results

    async def _near(
        self, client: httpx.AsyncClient, sport: str, entity_id: int
    ) -> Optional[list]:
        """`previousEvent` + `nextEvent` de la entidad; None si falla."""
        key = (sport, entity_id)
        if key in self._near_cache:
            return self._near_cache[key]
        path = (
            f"/api/tennis/team/{entity_id}/events/near"
            if sport == "tenis"
            else f"/api/team/{entity_id}/matches/near"
        )
        data = await self._get_json(client, path)
        events = (
            None
            if data is None
            else [ev for ev in (data.get("previousEvent"), data.get("nextEvent")) if ev]
        )
        self._near_cache[key] = events
        return events

    @staticmethod
    def _entity_id(sport: str, name: str, results: list) -> Optional[int]:
        """Id de la mejor entidad del deporte pedido en /search."""
        slug = "tennis" if sport == "tenis" else "football"
        best_id: Optional[int] = None
        best_score = 0.0
        for res in results:
            entity = res.get("entity") or {}
            if (entity.get("sport") or {}).get("slug") != slug:
                continue
            score = _pair_similar(name, entity.get("name") or "")
            if score > best_score:
                best_score = score
                best_id = entity.get("id")
        return best_id if best_score >= _MIN_SIMILARITY else None

    @staticmethod
    def _event_score(sport: str, team_hint: str, event: dict) -> float:
        home = (event.get("homeTeam") or {}).get("name") or ""
        away = (event.get("awayTeam") or {}).get("name") or ""
        if not home or not away:
            return 0.0
        if sport == "tenis":
            return max(_pair_similar(team_hint, home), _pair_similar(team_hint, away))
        return match_score(team_hint, home, away)

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        """search -> mejor entidad -> matches/events near -> evento."""
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

        # Nombre buscable: primera parte del enfrentamiento
        # ("Cecchinato" de "Cecchinato vs Moeller", "Alavés" de
        # "Alavés - Valencia").
        name = (
            re.split(r"[/+&]|\s+-\s+|\s+vs\.?\s+", hint, maxsplit=1)[0].strip() or hint
        )
        found: Optional[EventRef] = None
        async with httpx.AsyncClient(timeout=15) as client:
            results = await self._search(client, sport, name)
            if results is None:
                return None  # error de API: no se marca missed
            entity_id = self._entity_id(sport, name, results)
            if entity_id is not None:
                events = await self._near(client, sport, entity_id)
                if events is None:
                    return None
                best_score = 0.0
                best_event: Optional[dict] = None
                for event in events:
                    ts = event.get("startTimestamp")
                    if ts:
                        played = datetime.fromtimestamp(ts, timezone.utc).replace(
                            tzinfo=None
                        )
                        if abs(played - date) > _DATE_TOLERANCE:
                            continue
                    score = self._event_score(sport, hint, event)
                    if score > best_score:
                        best_score = score
                        best_event = event
                if best_event is not None and best_score >= _MIN_SIMILARITY:
                    ts = best_event.get("startTimestamp")
                    start = (
                        datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
                        if ts
                        else None
                    )
                    found = EventRef(
                        event_ext_id=f"{_FAMILY}:{best_event.get('id')}",
                        home_team=(best_event.get("homeTeam") or {}).get("name") or "",
                        away_team=(best_event.get("awayTeam") or {}).get("name") or "",
                        start=start,
                    )

        if found is None:
            mark_missed(miss_key)
        return found

    # --- Descarga de odds -------------------------------------------------

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        """Todas las opciones de todos los mercados del evento."""
        if is_rate_limited(self.NAME):
            return None
        try:
            _, raw_id = event_ext_id.split(":", 1)
        except ValueError:
            return None
        path = (
            f"/api/tennis/event/{raw_id}/odds"
            if sport == "tenis"
            else f"/api/match/{raw_id}/odds"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            data = await self._get_json(client, path)
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
