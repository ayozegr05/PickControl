"""Proveedor de odds de The Odds API (the-odds-api.com).

La única fuente gratuita que publica props de jugador (goleador,
asistencias, marca-o-asiste) y escaleras alternas de córners/tarjetas
para fútbol — exactamente lo que ni Sofascore ni las páginas de
comparadores ofrecen. Restricción importante: SOLO cuotas en vivo de
eventos futuros/en juego (el histórico requiere plan de pago), así que
su papel es construir histórico propio: capturar la cuota hoy para
auditarla cuando el partido pase.

Rutas verificadas (2026-10-14):

- `GET /v4/sports/{sport_key}/events` — listado de eventos GRATIS
  (`x-requests-last: 0`). Sirve de `find_event` sin gastar créditos.
- `GET /v4/sports/{sport_key}/events/{id}/odds?regions=..&markets=..`
  — odds del evento; cuesta `mercados × regiones` créditos.

Cuota: 500 créditos/MES en el free tier; el header
`x-requests-remaining` la reporta en cada respuesta. Al llegar a 0 se
aparca el provider hasta el día 1 del mes siguiente (reset mensual).

Formato de outcomes verificado:

- Props (`player_goal_scorer_anytime`, `player_to_score_or_assist`,
  `player_assists`, `player_first/last_goal_scorer`, `player_shots*`,
  `player_fouls`): `outcome.description` trae el nombre del jugador;
  en mercados de línea (asistencias, disparos) `name` es
  "Over"/"Under" y `point` la línea.
- Alternas (`alternate_totals_corners`, `alternate_totals_cards`):
  `name` = "Over"/"Under", `point` = la línea — se traducen al
  vocabulario Sofascore ("Corners 2-Way" / "Cards in match") para que
  el comparador las case con la maquinaria O/U ya existente.

No existe `player_to_receive_card` — las tarjetas de jugador siguen
siendo territorio bet365/oddspapi.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.models.parsed_pick import ParsedPick
from app.services.odds.base import EventRef, MarketChoice
from app.services.results.base import (
    count_provider_call,
    is_missed,
    is_rate_limited,
    mark_cooldown,
    mark_missed,
    match_score,
)

logger = get_logger("app.odds.theoddsapi")

NAME = "theoddsapi"
_PREFIX = "theoddsapi:"
_BASE = "https://api.the-odds-api.com/v4"
_MIN_SCORE = 0.75
# La lista de eventos no cuesta créditos, pero tampoco repite el
# barrido de 14 ligas cada 30 min por un evento inexistente.
_MISS_TTL = timedelta(hours=12)
# Un evento que ya empezó desaparece del listado — no buscar pasados.
_LIVE_LOOKBACK = timedelta(hours=6)

# Ligas/l competitions con props en el feed (las que usa el tipster).
_SOCCER_KEYS: tuple[str, ...] = (
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_uefa_nations_league",
    "soccer_england_efl_cup",
    "soccer_fa_cup",
    "soccer_efl_champ",
    "soccer_fifa_world_cup_qualifiers_europe",
    "soccer_conmebol_copa_libertadores",
)

# Traducción mercado The Odds API -> vocabulario del comparador
# (compare.py). Las alternas de córners/tarjetas reutilizan los nombres
# de Sofascore para casar con `_OU_MARKETS_FUTBOL` sin código extra.
_MARKET_NAMES = {
    "player_goal_scorer_anytime": "Goalscorer anytime",
    "player_to_score_or_assist": "Goalscorer or assist",
    "player_assists": "Player assists",
    "player_first_goal_scorer": "First goalscorer",
    "player_last_goal_scorer": "Last goalscorer",
    "player_shots": "Player shots",
    "player_shots_on_target": "Player shots on target",
    "player_fouls": "Player fouls",
    "alternate_totals_corners": "Corners 2-Way",
    "alternate_totals_cards": "Cards in match",
    "correct_score": "Correct score",
    "double_chance": "Double chance",
    "draw_no_bet": "Draw no bet",
    "btts": "Both teams to score",
    "alternate_asian_handicap": "Asian handicap",
    "asian_handicap": "Asian handicap",
    "alternate_totals": "Match goals",
    "totals": "Match goals",
}

# Mercados cuyo outcome lleva el jugador en `description`.
_PLAYER_PROP_MARKETS = {
    "player_goal_scorer_anytime",
    "player_to_score_or_assist",
    "player_first_goal_scorer",
    "player_last_goal_scorer",
    "player_assists",
    "player_shots",
    "player_shots_on_target",
    "player_fouls",
}
# De esos, los de línea Over/Under (name="Over"/"Under" + point).
_PLAYER_OU_MARKETS = {
    "player_assists",
    "player_shots",
    "player_shots_on_target",
    "player_fouls",
}

_PROP_PICK = re.compile(
    r"jugador|amonestad|ve\s+tarjeta|recibe\s+tarjeta|marca\s+o\s+asiste|"
    r"asiste|asistencia|anota|marca\s+(gol|su\s+gol|en\s+cualquier)|"
    r"disparo|remate|shot|scorer",
    re.IGNORECASE,
)
_ALT_LINE_PICK = re.compile(
    r"c[oó]rner|corner|esquina|tarjeta|card|booking|"
    r"resultado\s+exacto|marcador\s+exacto|correct\s+score",
    re.IGNORECASE,
)


def pick_needs_theoddsapi(pick: ParsedPick) -> bool:
    """True si el pick necesita algo que solo The Odds API publica:
    prop de jugador o línea alterna de córners/tarjetas/marcador.

    Gate de cuota: un pick normal nunca dispara una descarga de
    créditos — solo los que la cadena gratuita no puede cubrir.
    """
    text = f"{pick.mercado or ''} {pick.seleccion or ''}"
    return bool(_PROP_PICK.search(text) or _ALT_LINE_PICK.search(text))


def _next_month_start() -> datetime:
    now = utc_now()
    month = now.month + 1 if now.month < 12 else 1
    year = now.year if now.month < 12 else now.year + 1
    return datetime(year, month, 1)


class TheOddsApiProvider:
    """Cliente de The Odds API para props y líneas alternas.

    `find_event` es gratis (listado de eventos no consume créditos) y
    `fetch_odds` cuesta `len(markets) × len(regions)` créditos — el
    snapshotter solo le pasa picks que realmente necesitan esos
    mercados.
    """

    NAME = NAME
    ID_PREFIX = _PREFIX
    SUPPORTED_SPORTS = frozenset({"futbol"})

    def __init__(self, api_key: str, regions: str, markets: str) -> None:
        self._api_key = api_key
        self._regions = regions
        self._markets = markets
        # Caché del listado de eventos por sport_key: es gratis pero
        # evita re-listar 14 ligas en cada ciclo del snapshotter.
        self._events_cache: dict[str, Optional[list]] = {}

    # --- HTTP ----------------------------------------------------------

    async def _get_json(self, client: httpx.AsyncClient, url: str):
        """GET con conteo y gestión de la cuota mensual (x-requests-*)."""
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("[ODDS:%s] Error de red: %s", self.NAME, exc)
            return None
        count_provider_call(self.NAME)
        remaining = response.headers.get("x-requests-remaining")
        if remaining is not None:
            try:
                if int(remaining) <= 0:
                    mark_cooldown(self.NAME, _next_month_start())
                    logger.warning(
                        "[ODDS:%s] créditos del mes agotados — aparcado " "hasta %s",
                        self.NAME,
                        _next_month_start().date(),
                    )
                    return None
            except ValueError:
                pass
        if response.status_code in (401, 403, 429):
            # 429 = sin créditos; 401/403 = key inválida o histórico no
            # permitido en el plan — en ambos casos aparcar el mes.
            mark_cooldown(self.NAME, _next_month_start())
            logger.warning(
                "[ODDS:%s] %s — aparcado hasta %s",
                self.NAME,
                response.status_code,
                _next_month_start().date(),
            )
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("[ODDS:%s] Error de API (%s)", self.NAME, exc)
            return None
        try:
            return response.json()
        except ValueError:
            return None

    # --- Resolución de evento -------------------------------------------

    async def _events(self, client: httpx.AsyncClient, sport_key: str):
        """Listado del deporte (gratis). Caché por instancia."""
        if sport_key in self._events_cache:
            return self._events_cache[sport_key]
        data = await self._get_json(
            client,
            f"{_BASE}/sports/{sport_key}/events"
            f"?apiKey={self._api_key}&dateFormat=iso",
        )
        events = data if isinstance(data, list) else None
        self._events_cache[sport_key] = events
        return events

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        """El evento en el listado gratis de las ligas con props."""
        if sport not in self.SUPPORTED_SPORTS or is_rate_limited(self.NAME):
            return None
        if date < utc_now() - _LIVE_LOOKBACK:
            # El listado solo trae futuros/en juego — un evento pasado
            # nunca aparecerá: ni lo intentamos (14 llamadas gratis
            # pero inútiles por pick).
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        day = date.strftime("%Y-%m-%d")
        miss_key = f"{self.NAME}|noevent|{day}|{hint.lower()}"
        if is_missed(miss_key, _MISS_TTL):
            return None

        best_score = 0.0
        best: Optional[dict] = None
        best_key = ""
        async with httpx.AsyncClient(timeout=20) as client:
            for key in _SOCCER_KEYS:
                events = await self._events(client, key)
                if events is None:
                    continue
                for ev in events:
                    home = ev.get("home_team") or ""
                    away = ev.get("away_team") or ""
                    if not home or not away:
                        continue
                    commence = ev.get("commence_time") or ""
                    try:
                        start = datetime.fromisoformat(
                            commence.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                    except ValueError:
                        start = None
                    if start and abs(start - date) > timedelta(days=1):
                        continue
                    score = match_score(hint, home, away)
                    if score > best_score:
                        best_score, best, best_key = score, ev, key
        if best is not None and best_score >= _MIN_SCORE:
            commence = best.get("commence_time") or ""
            try:
                start = datetime.fromisoformat(commence.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except ValueError:
                start = None
            return EventRef(
                event_ext_id=f"{_PREFIX}{best_key}:{best.get('id')}",
                home_team=best.get("home_team") or "",
                away_team=best.get("away_team") or "",
                start=start,
            )
        mark_missed(miss_key)
        return None

    # --- Descarga de odds ------------------------------------------------

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        """Los mercados pedidos del evento (`settings.odds_api_markets`),
        deduplicados entre bookies al MEJOR precio de cada opción."""
        if is_rate_limited(self.NAME):
            return None
        try:
            _, rest = event_ext_id.split(_PREFIX, 1)
            sport_key, event_id = rest.split(":", 1)
        except ValueError:
            return None
        async with httpx.AsyncClient(timeout=20) as client:
            data = await self._get_json(
                client,
                f"{_BASE}/sports/{sport_key}/events/{event_id}/odds"
                f"?apiKey={self._api_key}&regions={self._regions}"
                f"&markets={self._markets}&oddsFormat=decimal",
            )
        if not isinstance(data, dict):
            return None

        # Una opción por (mercado, nombre, línea): el mejor precio
        # entre todas las casas — para auditar "la cuota existía" la
        # referencia justa es el mejor precio disponible.
        best: dict[tuple[str, str, Optional[str]], float] = {}
        for book in data.get("bookmakers") or []:
            for market in book.get("markets") or []:
                key = market.get("key") or ""
                market_name = _MARKET_NAMES.get(key)
                if market_name is None:
                    continue
                for outcome in market.get("outcomes") or []:
                    price = outcome.get("price")
                    if not isinstance(price, (int, float)) or price <= 0:
                        continue
                    choice_name, group = self._translate_outcome(key, outcome)
                    if choice_name is None:
                        continue
                    slot = (market_name, choice_name, group)
                    if price > best.get(slot, 0.0):
                        best[slot] = float(price)
        return [
            MarketChoice(
                market_name=market_name,
                choice_name=choice_name,
                choice_group=group,
                cuota=price,
            )
            for (market_name, choice_name, group), price in best.items()
        ]

    @staticmethod
    def _translate_outcome(
        market_key: str, outcome: dict
    ) -> tuple[Optional[str], Optional[str]]:
        """(choice_name, choice_group) del outcome según el mercado.

        Props: el jugador va en `description` y pasa a `choice_name`;
        en mercados de línea el grupo es "Over 0.5" (dirección+línea).
        Alternas: "Over"/"Under" en choice_name y la línea en el
        grupo — el convenio exacto del feed Sofascore.
        """
        if market_key in _PLAYER_PROP_MARKETS:
            player = outcome.get("description") or outcome.get("name")
            if not player:
                return None, None
            point = outcome.get("point")
            if market_key in _PLAYER_OU_MARKETS:
                direction = outcome.get("name") or ""
                group = f"{direction} {point}" if point is not None else direction
                return str(player), group or None
            return str(player), None
        name = outcome.get("name")
        if not name:
            return None, None
        point = outcome.get("point")
        return str(name), str(point) if point is not None else None


def build_provider() -> Optional[TheOddsApiProvider]:
    """Instancia el provider si hay key configurada; None si no."""
    settings = get_settings()
    if not settings.odds_api_key:
        return None
    return TheOddsApiProvider(
        api_key=settings.odds_api_key,
        regions=settings.odds_api_regions,
        markets=settings.odds_api_markets,
    )
