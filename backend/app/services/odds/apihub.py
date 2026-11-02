"""Proveedores de cuotas vía APIHub (proxy.apihub.io): 1xBet y FlashScore.

APIHub normaliza el feed codificado de 1xBet en pestañas y mercados
ETIQUETADOS ("Corners" -> "3Way Total" -> "Over (8)") — es la única
fuente que publica el mercado exacto del tipster (totales a 3 vías en
líneas enteras: Más de / Exactamente / Menos de), además de la
escalera completa de córners, tarjetas y props que bet365 no lista.

Verificado (2026-10):
- `GET /1xbet/v1/general/search?text=<equipo>` -> partidos por
  torneo (`tournament.id` = championshipId; sin matchId ni fecha).
- `GET /1xbet/v1/prematch/championship-matches?sport_id=N&
  championshipId=ID` -> partidos con `id` (matchId), `startTimestamp`
  y `team1`/`team2`.
- `GET /1xbet/v1/match/odds?matchId=ID` -> [{name: tab, odds:
  [{name, outcomes: [{name, parameter, odd}]}]}]. Outcomes tipo
  "Over (8.5)"/"Exactly (8)"/"W1"/"1 (-4.5)": la línea va en
  `parameter`; la dirección se parsea del nombre.
- `GET /flashscore/v2/matches/list-by-date?date=YYYY-MM-DD&
  sport_id=N` -> torneos -> matches (match_id, timestamp, equipos).
  SOLO ventana de los últimos ~7 días — es respaldo vivo, no
  histórico.
- `GET /flashscore/v2/matches/odds?match_id=ID` -> [{name: bookie,
  odds: [{bettingType, bettingScope, odds: [{selection, handicap:
  {value}, value, opening, eventParticipantId}]}]}]. `value` es el
  precio actual y `opening` el de apertura — dos puntos por opción.

Cuota: 100 llamadas gratis/mes por servicio; después ~0.001$/llamada.
Un 402/403 marca cooldown hasta el día 1 del mes siguiente (mismo
convenio que The Odds API). El snapshotter solo ruta a estos
providers picks que necesitan sus mercados (líneas enteras/props) o
eventos que la cadena gratuita no resolvió.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.services.odds.base import EventRef, MarketChoice
from app.services.results.base import (
    count_provider_call,
    is_missed,
    is_rate_limited,
    mark_cooldown,
    mark_missed,
    match_score,
)

logger = get_logger("app.odds.apihub")

_BASE = "https://proxy.apihub.io"
_MIN_SCORE = 0.75
_MISS_TTL = timedelta(hours=12)
# Presupuesto de cortesía por ciclo del snapshotter: la cuota real la
# corta el 402/cooldown; este tope evita que un backlog de eventos
# nuevos queme la cuota de golpe en una sola pasada.
MAX_CALLS_PER_RUN = 8
# FlashScore no sirve fuera de su ventana de ~7 días.
_FLASHSCORE_WINDOW = timedelta(days=6)

NAME_1XBET = "apihub1xbet"
NAME_FLASHSCORE = "apihubflashscore"
PREFIX_1XBET = "1xbet:"
PREFIX_FLASHSCORE = "flashscore:"

# Sport ids 1xBet (feed oficial): 1 fútbol, 3 baloncesto, 4 tenis.
_XBET_SPORT_IDS = {"futbol": 1, "baloncesto": 3, "tenis": 4}
# Sport ids FlashScore: 1 soccer, 2 tennis, 3 basketball.
_FS_SPORT_IDS = {"futbol": 1, "tenis": 2, "baloncesto": 3}


def _next_month_start() -> datetime:
    now = utc_now()
    month = now.month + 1 if now.month < 12 else 1
    year = now.year if now.month < 12 else now.year + 1
    return datetime(year, month, 1)


class ApiHubClient:
    """Cliente compartido del proxy APIHub con conteo de llamadas."""

    def __init__(self, api_key: str, name: str) -> None:
        self._api_key = api_key
        self.name = name
        self.calls = 0

    def over_budget(self) -> bool:
        return self.calls >= MAX_CALLS_PER_RUN

    async def get(self, http: httpx.AsyncClient, service: str, path: str, params: dict):
        try:
            response = await http.get(
                f"{_BASE}/{service}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("[ODDS:%s] Error de red: %s", self.name, exc)
            return None
        self.calls += 1
        count_provider_call(self.name)
        if response.status_code in (401, 402, 403, 429):
            mark_cooldown(self.name, _next_month_start())
            logger.warning(
                "[ODDS:%s] %s — cuota/key agotada, aparcado hasta %s",
                self.name,
                response.status_code,
                _next_month_start().date(),
            )
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "[ODDS:%s] %s en %s%s",
                self.name,
                response.status_code,
                service,
                path,
            )
            return None
        try:
            return response.json()
        except ValueError:
            return None


def _hint_teams(hint: str) -> tuple[str, Optional[str]]:
    """("A vs B"/"A - B"/"A") -> nombres para la búsqueda."""
    for sep in (" vs ", " VS ", " Vs ", " - ", " v "):
        if sep in hint:
            home, away = hint.split(sep, 1)
            return home.strip(), away.strip() or None
    return hint.strip(), None


def _outcome(name: str, parameter) -> tuple[Optional[str], Optional[str]]:
    """Outcome 1xBet -> (choice_name, choice_group) del comparador.

    - "W1"/"W2"/"X" -> "1"/"2"/"X"
    - "Over (8.5)"/"Under (8)"/"Exactly (8)"/"(8.5) Over" ->
      ("Over"/"Under"/"Exactly", "8.5")
    - "1 (-4.5)"/"2 (1.5)" -> ("1"/"2", "-4.5") — hándicap con signo
    - "Yes"/"No", "1X"/"12"/"2X", nombres de jugador -> tal cual
    """
    if not name:
        return None, None
    group = str(parameter) if parameter is not None else None
    low = name.lower()
    if low.startswith("w1") or name == "1":
        return "1", group
    if low.startswith("w2") or name == "2":
        return "2", group
    if low.startswith("draw") or name == "X":
        return "X", group
    if re.match(r"^\(?\s*over", low) or re.search(r"\bover\s*\)?\s*$", low):
        return "Over", group
    if re.match(r"^\(?\s*under", low) or re.search(r"\bunder\s*\)?\s*$", low):
        return "Under", group
    if low.startswith("exactly") or low.startswith("exact"):
        return "Exactly", group
    if re.match(r"^[12]\s*\(", name):
        return name[0], group
    return name, group


# Traducción (tab, mercado) 1xBet -> market_name del comparador. Los
# mercados no listados conservan "Tab: Mercado" — quedan guardados
# como evidencia sin casar con picks (compare los ignora).
_XBET_MARKETS: dict[tuple[str, str], str] = {
    ("Regular time", "1X2"): "Full time",
    ("Regular time", "Double Chance"): "Double chance",
    ("Regular time", "Both Teams To Score"): "Both teams to score",
    ("Regular time", "Total"): "Match goals",
    ("Regular time", "Asian Total"): "Match goals",
    ("Regular time", "Handicap"): "Asian handicap",
    ("Regular time", "Asian Handicap"): "Asian handicap",
    ("Corners", "Total"): "Corners 2-Way",
    ("Corners", "Asian Total"): "Corners 2-Way",
    # El mercado EXACTO del tipster: totales de córners a 3 vías en
    # líneas enteras (Más de / Exactamente / Menos de — el "Exactly"
    # liquida distinto que un push asiático, ver compare.py).
    ("Corners", "3Way Total"): "Corners 3-Way",
    ("Corners", "Handicap"): "Corners handicap",
    ("Corners", "Total 1"): "Team 1 corners",
    ("Corners", "Total 2"): "Team 2 corners",
    ("Cards", "Total"): "Cards in match",
    ("Cards", "3Way Total"): "Cards 3-Way",
    ("Bookings", "Total"): "Cards in match",
    ("Bookings", "3Way Total"): "Cards 3-Way",
    # Totales a tiempo completo en tenis/basket (mismo esquema).
    ("Regular time", "Games"): "Total games won",
    ("Regular time", "Total games"): "Total games won",
    ("Sets", "Total"): "Total sets",
    ("Regular time", "Points"): "Match points",
    # Doble oportunidad con nombre en otros tabs.
    ("Corners", "Double Chance"): "Corners double chance",
}


def translate_onexbet_odds(payload: list) -> list[MarketChoice]:
    """Tabs etiquetadas de /1xbet/v1/match/odds -> MarketChoice."""
    choices: list[MarketChoice] = []
    for tab in payload or []:
        tab_name = tab.get("name") or ""
        for market in tab.get("odds") or []:
            market_raw = market.get("name") or ""
            market_name = _XBET_MARKETS.get((tab_name, market_raw)) or (
                f"{tab_name}: {market_raw}" if tab_name else market_raw
            )
            for outcome in market.get("outcomes") or []:
                price = outcome.get("odd")
                if not isinstance(price, (int, float)) or price <= 0:
                    continue
                choice_name, group = _outcome(
                    outcome.get("name") or "", outcome.get("parameter")
                )
                if choice_name is None:
                    continue
                choices.append(
                    MarketChoice(
                        market_name=market_name,
                        choice_name=choice_name,
                        choice_group=group,
                        cuota=float(price),
                    )
                )
    return choices


class OneXBetProvider:
    """1xBet vía APIHub: mercados etiquetados profundos (3-way
    corners/cards, props, totales de equipo) para fútbol/tenis/basket."""

    NAME = NAME_1XBET
    ID_PREFIX = PREFIX_1XBET
    SUPPORTED_SPORTS = frozenset(_XBET_SPORT_IDS)

    def __init__(self, client: ApiHubClient) -> None:
        self._client = client
        # Cachés por instancia: búsqueda de torneos por texto y
        # listados por campeonato (cada uno es una llamada de cuota).
        self._search_cache: dict[str, Optional[list]] = {}
        self._champ_cache: dict[tuple[int, int], Optional[list]] = {}
        self._match_cache: dict[int, dict] = {}

    async def _search(self, http: httpx.AsyncClient, text: str):
        if text in self._search_cache:
            return self._search_cache[text]
        data = await self._client.get(
            http, "1xbet", "/v1/general/search", {"text": text}
        )
        result = data if isinstance(data, list) else None
        self._search_cache[text] = result
        return result

    async def _champ_matches(self, http, sport_id: int, champ_id: int):
        key = (sport_id, champ_id)
        if key in self._champ_cache:
            return self._champ_cache[key]
        data = await self._client.get(
            http,
            "1xbet",
            "/v1/prematch/championship-matches",
            {"sport_id": sport_id, "championshipId": champ_id},
        )
        result = data if isinstance(data, list) else None
        self._champ_cache[key] = result
        return result

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        if is_rate_limited(self.NAME) or self._client.over_budget():
            return None
        sport_id = _XBET_SPORT_IDS.get(sport)
        if sport_id is None:
            return None
        home_hint, _ = _hint_teams(team_hint.strip())
        if not home_hint:
            return None
        day = date.strftime("%Y-%m-%d")
        miss_key = f"{self.NAME}|noevent|{day}|{home_hint.lower()}"
        if is_missed(miss_key, _MISS_TTL):
            return None
        async with httpx.AsyncClient(timeout=20) as http:
            found = await self._search(http, home_hint)
            if found is None:
                return None
            champ_ids: list[int] = []
            for item in found:
                if (item.get("sport") or {}).get("id") != sport_id:
                    continue
                # La búsqueda solo trae torneos del equipo buscado —
                # filtramos por nombre para no sondear torneos ajenos.
                names = (
                    (item.get("opponent1") or {}).get("name") or "",
                    (item.get("opponent2") or {}).get("name") or "",
                )
                if match_score(home_hint, *names) < 0.5:
                    continue
                champ_id = (item.get("tournament") or {}).get("id")
                if champ_id and champ_id not in champ_ids:
                    champ_ids.append(champ_id)
            best, best_score = None, 0.0
            for champ_id in champ_ids:
                if self._client.over_budget():
                    break
                matches = await self._champ_matches(http, sport_id, champ_id)
                if not matches:
                    continue
                for match in matches:
                    ts = match.get("startTimestamp")
                    start = (
                        datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
                        if isinstance(ts, (int, float))
                        else None
                    )
                    if start and abs(start - date) > timedelta(days=1):
                        continue
                    home = (match.get("team1") or {}).get("name") or ""
                    away = (match.get("team2") or {}).get("name") or ""
                    score = match_score(team_hint, home, away)
                    if score > best_score:
                        best, best_score = match, score
            if best is None or best_score < _MIN_SCORE:
                mark_missed(miss_key)
                return None
            match_id = int(best.get("id") or 0)
            start_ts = best.get("startTimestamp")
            start = (
                datetime.fromtimestamp(start_ts, tz=timezone.utc).replace(tzinfo=None)
                if isinstance(start_ts, (int, float))
                else None
            )
            home = (best.get("team1") or {}).get("name") or ""
            away = (best.get("team2") or {}).get("name") or ""
            self._match_cache[match_id] = {
                "home": home,
                "away": away,
                "start": start,
            }
            return EventRef(
                event_ext_id=f"{self.ID_PREFIX}{match_id}",
                home_team=home,
                away_team=away,
                start=start,
            )

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        if is_rate_limited(self.NAME) or self._client.over_budget():
            return None
        try:
            match_id = int(event_ext_id.split(":", 1)[1])
        except (IndexError, ValueError):
            return None
        async with httpx.AsyncClient(timeout=20) as http:
            data = await self._client.get(
                http, "1xbet", "/v1/match/odds", {"matchId": match_id}
            )
        if not isinstance(data, list):
            return None
        return translate_onexbet_odds(data)


# --- FlashScore (respaldo vivo, ventana ~7 días) -------------------------

_FS_TYPES = {
    ("HOME_DRAW_AWAY", "FULL_TIME"): "Full time",
    ("DOUBLE_CHANCE", "FULL_TIME"): "Double chance",
    ("BOTH_TEAMS_TO_SCORE", "FULL_TIME"): "Both teams to score",
    ("OVER_UNDER", "FULL_TIME"): "Match goals",
    ("ASIAN_HANDICAP", "FULL_TIME"): "Asian handicap",
}
_FS_SELECTIONS = {
    "OVER": ("Over", None),
    "UNDER": ("Under", None),
    "YES": ("Yes", None),
    "NO": ("No", None),
    "1X": ("1X", None),
    "12": ("12", None),
    "X2": ("X2", None),
}


def translate_flashscore_odds(
    payload: list, home_pid: Optional[str], away_pid: Optional[str]
) -> list[MarketChoice]:
    """Odds multi-casa de FlashScore -> MarketChoice, mejor precio
    por opción (misma convención que theoddsapi: para auditar que la
    cuota existía la referencia justa es el mejor precio disponible)."""
    best: dict[tuple[str, str, Optional[str]], dict] = {}
    for book in payload or []:
        for market in book.get("odds") or []:
            market_name = _FS_TYPES.get(
                (market.get("bettingType"), market.get("bettingScope"))
            )
            if market_name is None:
                continue
            for outcome in market.get("odds") or []:
                price = outcome.get("value")
                try:
                    cuota = float(price)
                except (TypeError, ValueError):
                    continue
                if cuota <= 0:
                    continue
                selection = outcome.get("selection")
                handicap = (outcome.get("handicap") or {}).get("value")
                if selection in _FS_SELECTIONS:
                    choice_name, _ = _FS_SELECTIONS[selection]
                    group = handicap if market_name != "Both teams to score" else None
                elif market_name == "Full time":
                    pid = outcome.get("eventParticipantId")
                    if pid == home_pid:
                        choice_name, group = "1", None
                    elif pid == away_pid:
                        choice_name, group = "2", None
                    elif pid is None:
                        choice_name, group = "X", None
                    else:
                        continue
                elif market_name in ("Asian handicap", "Double chance"):
                    # El hándicap/selection sin mapeo claro se salta:
                    # mejor perder una opción que maltraducirla.
                    if selection not in ("1", "2"):
                        continue
                    choice_name, group = selection, handicap
                else:
                    continue
                slot = (market_name, choice_name, group)
                current = best.get(slot)
                if current is None or cuota > current["cuota"]:
                    opening = outcome.get("opening")
                    try:
                        apertura = float(opening) if opening else None
                    except (TypeError, ValueError):
                        apertura = None
                    best[slot] = {"cuota": cuota, "apertura": apertura}
    return [
        MarketChoice(
            market_name=market_name,
            choice_name=choice_name,
            choice_group=group,
            cuota=slot["cuota"],
            cuota_apertura=slot["apertura"],
        )
        for (market_name, choice_name, group), slot in best.items()
    ]


class FlashScoreProvider:
    """FlashScore vía APIHub: respaldo vivo multi-casa (~7 días)."""

    NAME = NAME_FLASHSCORE
    ID_PREFIX = PREFIX_FLASHSCORE
    SUPPORTED_SPORTS = frozenset(_FS_SPORT_IDS)

    def __init__(self, client: ApiHubClient) -> None:
        self._client = client
        self._day_cache: dict[tuple[int, str], Optional[list]] = {}
        self._match_meta: dict[str, dict] = {}

    async def _day_matches(self, http, sport_id: int, day: date):
        key = (sport_id, day.isoformat())
        if key in self._day_cache:
            return self._day_cache[key]
        data = await self._client.get(
            http,
            "flashscore",
            "/v2/matches/list-by-date",
            {"date": day.isoformat(), "sport_id": sport_id},
        )
        matches: Optional[list] = None
        if isinstance(data, list):
            matches = []
            for tournament in data:
                for match in tournament.get("matches") or []:
                    matches.append(match)
        self._day_cache[key] = matches
        return matches

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        if is_rate_limited(self.NAME) or self._client.over_budget():
            return None
        sport_id = _FS_SPORT_IDS.get(sport)
        if sport_id is None:
            return None
        # Fuera de la ventana ~7 días no hay nada que buscar (la API
        # responde "older than 7 days"): ni se gasta la llamada.
        if abs(utc_now() - date) > _FLASHSCORE_WINDOW:
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        async with httpx.AsyncClient(timeout=20) as http:
            best, best_score = None, 0.0
            for delta in (0, -1, 1):
                day = (date + timedelta(days=delta)).date()
                matches = await self._day_matches(http, sport_id, day)
                if not matches:
                    continue
                for match in matches:
                    ts = match.get("timestamp")
                    start = (
                        datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
                        if isinstance(ts, (int, float))
                        else None
                    )
                    if start and abs(start - date) > timedelta(days=1):
                        continue
                    home = (match.get("home_team") or {}).get("name") or ""
                    away = (match.get("away_team") or {}).get("name") or ""
                    score = match_score(hint, home, away)
                    if score > best_score:
                        best, best_score = match, score
            if best is None or best_score < _MIN_SCORE:
                return None
            match_id = str(best.get("match_id") or "")
            home = (best.get("home_team") or {}).get("name") or ""
            away = (best.get("away_team") or {}).get("name") or ""
            ts = best.get("timestamp")
            start = (
                datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
                if isinstance(ts, (int, float))
                else None
            )
            # Los participant ids permiten mapear 1/X/2 en odds.
            self._match_meta[match_id] = {
                "home_pid": (best.get("home_team") or {}).get("event_participant_id"),
                "away_pid": (best.get("away_team") or {}).get("event_participant_id"),
            }
            return EventRef(
                event_ext_id=f"{self.ID_PREFIX}{match_id}",
                home_team=home,
                away_team=away,
                start=start,
            )

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        if is_rate_limited(self.NAME) or self._client.over_budget():
            return None
        try:
            match_id = event_ext_id.split(":", 1)[1]
        except IndexError:
            return None
        async with httpx.AsyncClient(timeout=20) as http:
            data = await self._client.get(
                http, "flashscore", "/v2/matches/odds", {"match_id": match_id}
            )
        if not isinstance(data, list):
            return None
        meta = self._match_meta.get(match_id) or {}
        return translate_flashscore_odds(
            data, meta.get("home_pid"), meta.get("away_pid")
        )


def build_providers() -> list:
    """Instancia los providers APIHub si hay key configurada."""
    settings = get_settings()
    key = getattr(settings, "apihub_key", None)
    if not key:
        return []
    return [
        OneXBetProvider(ApiHubClient(key, NAME_1XBET)),
        FlashScoreProvider(ApiHubClient(key, NAME_FLASHSCORE)),
    ]
