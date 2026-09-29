"""Cliente de OddsFeed (odds-feed, RapidAPI) para backfill histórico.

Sustituto de OddsPapi cuando su cuota mensual está agotada: OddsFeed
expone `markets/history` por `market_book_id` — la curva de precios
de cada mercado/casa — con el mismo fin que `/historical-odds` de
bet36528 (auditar la cuota del tipster en el momento de publicar).

Flujo por pick (cuota 500 req/mes en BASIC — se economiza):

1. `GET /api/v1/events?sport_id&start_at_min&start_at_max` — eventos
   del día (paginado a 100; se cachea por deporte+día).
2. `GET /api/v1/events/markets?event_id&placing=PREMATCH` — mercados
   del evento con `market_books` por casa (una sola llamada).
3. `GET /api/v1/markets/history?market_book_id` — curva, SOLO de los
   market_books que casan con algún pick (la casa preferida primero:
   BET365 > 1XBET > PINNACLE > ... — las casas de los tipsters).

Limitaciones del feed: solo mercados principales (1X2/HOME_AWAY,
OVER_UNDER, ASIAN_HANDICAP, BOTH_TEAMS_TO_SCORE) — sin props, córners
ni tarjetas. Esos picks siguen dependiendo de oddspapi.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from app.core.logging import get_logger
from app.services.odds.oddspapi import PricePoint
from app.services.results.base import (
    is_rate_limited,
    log_remaining_quota,
    mark_rate_limited,
    match_score,
)

logger = get_logger("app.odds.oddsfeed")

NAME = "oddsfeed"
_DEFAULT_HOST = "odds-feed.p.rapidapi.com"
_MIN_MATCH_SCORE = 0.6
_DATE_TOLERANCE = timedelta(days=1)

# Deporte canónico -> id de /api/v1/sports.
SPORT_IDS = {"futbol": 1, "tenis": 2, "baloncesto": 3}

# Casas en orden de preferencia para gastar la llamada de histórico:
# primero las que usan los tipsters, luego sharp/exchange.
_BOOK_PREFERENCE = (
    "BET365",
    "1XBET",
    "PINNACLE",
    "BETFAIR",
    "UNIBET",
    "BWIN_ES",
    "WILLIAM_HILL",
)

# (deporte, market_name OddsFeed) -> mercado en vocabulario compare.py.
_MARKET_TRANSLATION = {
    ("futbol", "1X2"): "Full time",
    ("futbol", "HOME_AWAY"): "Full time",
    ("futbol", "OVER_UNDER"): "Match goals",
    ("futbol", "ASIAN_HANDICAP"): "Asian handicap",
    ("futbol", "BOTH_TEAMS_TO_SCORE"): "Both teams to score",
    ("tenis", "HOME_AWAY"): "Full time",
    ("tenis", "OVER_UNDER"): "Total games won",
    ("tenis", "ASIAN_HANDICAP"): "Game handicap",
    ("baloncesto", "HOME_AWAY"): "Full time",
    ("baloncesto", "OVER_UNDER"): "Match goals",
}
_SPREAD = {"Asian handicap", "Game handicap"}
_TOTAL = {"Match goals", "Total games won"}
# outcome_N -> choice_name para mercados de línea simple.
_WINNER_3WAY = ("1", "X", "2")
_WINNER_2WAY = ("1", "2")
_TOTAL_CHOICES = ("Over", "Under")
_BTTS_CHOICES = ("Yes", "No")


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    """ "2026-09-28 04:00:00" de OddsFeed -> datetime naive UTC."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


@dataclass
class FeedFixture:
    """Evento de OddsFeed con lo necesario para casarlo con un pick."""

    event_id: int
    home_name: str
    away_name: str
    start: Optional[datetime]
    sport_id: int


@dataclass
class FeedMarketBook:
    """Un market_book usable: mercado traducido + casa + id para la
    curva histórica."""

    translated: str  # vocabulario compare.py
    raw_name: str  # 1X2 / OVER_UNDER / ...
    line: Optional[float]  # `value` del mercado (O/U, hándicap)
    book: str
    market_book_id: int
    # Precios actuales por outcome (fallback si no hay historial).
    current: dict[int, float]


def parse_event(raw: dict, sport_id: int) -> Optional[FeedFixture]:
    """Evento del payload /events; None si le falta lo básico."""
    event_id = raw.get("id")
    home = (raw.get("team_home") or {}).get("name") or ""
    away = (raw.get("team_away") or {}).get("name") or ""
    if not event_id or not home or not away:
        return None
    return FeedFixture(
        event_id=int(event_id),
        home_name=home,
        away_name=away,
        start=_parse_dt(raw.get("start_at")),
        sport_id=sport_id,
    )


def event_ext_id(fixture: FeedFixture) -> str:
    return f"oddsfeed:{fixture.event_id}"


def match_fixture(
    fixtures: list[FeedFixture], hint: str, date: datetime
) -> Optional[FeedFixture]:
    """El evento del pick por similitud de nombres (±1 día). OddsFeed
    no expone ids de Sofascore — solo casa por nombre + fecha."""
    best: Optional[FeedFixture] = None
    best_score = 0.0
    for fixture in fixtures:
        if fixture.start and abs(fixture.start - date) > _DATE_TOLERANCE:
            continue
        score = match_score(hint, fixture.home_name, fixture.away_name)
        if score > best_score:
            best, best_score = fixture, score
    return best if best_score >= _MIN_MATCH_SCORE else None


def _fmt_line(value: float) -> str:
    return f"{value:g}"


def _choice_defs(
    book: FeedMarketBook, home: str, away: str
) -> list[tuple[int, str, Optional[str]]]:
    """(outcome_idx, choice_name, choice_group) por opción del mercado,
    en el formato que `compare.py` espera."""
    if book.translated in _SPREAD:
        line = book.line or 0.0
        return [
            (0, f"({_fmt_line(line)}) {home}", _fmt_line(line)),
            (1, f"({_fmt_line(-line)}) {away}", _fmt_line(-line)),
        ]
    if book.translated in _TOTAL:
        group = _fmt_line(book.line) if book.line is not None else None
        return [(i, name, group) for i, name in enumerate(_TOTAL_CHOICES)]
    if book.translated == "Both teams to score":
        return [(i, name, None) for i, name in enumerate(_BTTS_CHOICES)]
    names = _WINNER_3WAY if book.raw_name == "1X2" else _WINNER_2WAY
    return [(i, name, None) for i, name in enumerate(names)]


def translate_market_books(
    markets: list[dict], sport: str, home: str, away: str
) -> list[tuple[FeedMarketBook, list[tuple[int, str, Optional[str]]]]]:
    """Mercados PREMATCH del evento -> market_books elegibles con sus
    opciones ya traducidas. Por mercado se elige la casa preferida."""
    books: list[tuple[FeedMarketBook, list[tuple[int, str, Optional[str]]]]] = []
    for market in markets:
        # Solo tiempo reglamentario completo — un 1X2 de 1ª parte
        # traducido a "Full time" sería un dato erróneo.
        if market.get("period") not in (None, "FULL_TIME", "FULL_TIME_AND_OT"):
            continue
        translated = _MARKET_TRANSLATION.get((sport, market.get("market_name")))
        if translated is None:
            continue
        market_books = market.get("market_books") or []
        if not market_books:
            continue
        raw_line = market.get("value")
        try:
            line = float(raw_line) if raw_line is not None else None
        except (TypeError, ValueError):
            line = None
        ordered = sorted(
            market_books,
            key=lambda mb: (
                _BOOK_PREFERENCE.index(mb.get("book"))
                if mb.get("book") in _BOOK_PREFERENCE
                else len(_BOOK_PREFERENCE)
            ),
        )
        for mb in ordered:
            mb_id = mb.get("market_book_id")
            if mb_id is None:
                continue
            book = FeedMarketBook(
                translated=translated,
                raw_name=str(market.get("market_name") or ""),
                line=line,
                book=str(mb.get("book") or ""),
                market_book_id=int(mb_id),
                current={
                    i: float(mb[f"outcome_{i}"])
                    for i in range(3)
                    if isinstance(mb.get(f"outcome_{i}"), (int, float))
                },
            )
            books.append((book, _choice_defs(book, home, away)))
    # Solo la casa preferida de cada mercado — una curva por
    # (mercado, casa) basta y cada una cuesta una llamada. `ordered`
    # ya va de más a menos preferida, así que el primero gana.
    seen: set[tuple[str, Optional[float]]] = set()
    deduped: list[tuple[FeedMarketBook, list[tuple[int, str, Optional[str]]]]] = []
    for book, defs in books:
        key = (book.translated, book.line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((book, defs))
    return deduped


def points_from_history(history: list[dict], outcome_idx: int) -> list[PricePoint]:
    """Curva de una opción: `outcome_{idx}` a lo largo del tiempo."""
    points: list[PricePoint] = []
    for row in history:
        price = row.get(f"outcome_{outcome_idx}")
        captured = _parse_dt(row.get("change_at"))
        if not isinstance(price, (int, float)) or price <= 0 or not captured:
            continue
        points.append(
            PricePoint(
                captured_at=captured,
                cuota=float(price),
                suspended=not row.get("is_open", True),
            )
        )
    points.sort(key=lambda p: p.captured_at)
    return points


class OddsFeedClient:
    """Cliente HTTP de odds-feed con el manejo de cuota del proyecto.

    `_calls` cuenta las llamadas de la ejecución para el presupuesto
    diario del backfill (500 req/mes no da para descuidos).
    """

    def __init__(self, api_key: str, api_host: str = _DEFAULT_HOST) -> None:
        self.NAME = NAME
        self._api_key = api_key
        self._api_host = api_host
        self._events_cache: dict[tuple, Optional[list[FeedFixture]]] = {}
        self.calls = 0

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._api_host,
        }

    async def _get(
        self, client: httpx.AsyncClient, path: str, params: Optional[dict] = None
    ) -> tuple[int, Any]:
        """GET -> (status, json). status 0 = red o rate-limited; en
        403/429 marca el proveedor (reset mensual vía `mark_rate_limited`)."""
        if is_rate_limited(self.NAME):
            return 0, None
        try:
            response = await client.get(
                f"https://{self._api_host}{path}",
                headers=self._headers(),
                params=params,
            )
        except httpx.HTTPError as exc:
            logger.warning("[ODDS:%s] Error de red en %s: %s", self.NAME, path, exc)
            return 0, None
        self.calls += 1
        log_remaining_quota(self.NAME, response)
        if response.status_code in (403, 429):
            mark_rate_limited(self.NAME, response)
            logger.warning("[ODDS:%s] Cuota agotada; se omite", self.NAME)
            return response.status_code, None
        if response.status_code >= 400:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    async def events(
        self, client: httpx.AsyncClient, sport_id: int, day: date
    ) -> Optional[list[FeedFixture]]:
        """Eventos del día (paginado; cacheado por deporte+día)."""
        key = (sport_id, day)
        if key in self._events_cache:
            return self._events_cache[key]
        fixtures: list[FeedFixture] = []
        page = 0
        while True:
            status, data = await self._get(
                client,
                "/api/v1/events",
                params={
                    "sport_id": sport_id,
                    "start_at_min": f"{day.isoformat()} 00:00:00",
                    "start_at_max": f"{day.isoformat()} 23:59:59",
                    "page": page,
                },
            )
            if status != 200 or not isinstance(data, dict):
                self._events_cache[key] = None
                return None
            for raw in data.get("data") or []:
                fixture = parse_event(raw, sport_id)
                if fixture is not None:
                    fixtures.append(fixture)
            if page >= int(data.get("last_page") or 0):
                break
            page += 1
            if page > 10:  # cortafuegos anti-bucle (1000 eventos/día/deporte)
                break
        self._events_cache[key] = fixtures
        return fixtures

    async def event_markets(
        self, client: httpx.AsyncClient, event_id: int
    ) -> Optional[list[dict]]:
        """Mercados PREMATCH del evento con sus market_books."""
        status, data = await self._get(
            client,
            "/api/v1/events/markets",
            params={"event_id": event_id, "placing": "PREMATCH"},
        )
        if status == 404:
            return []
        if status != 200 or not isinstance(data, dict):
            return None
        return data.get("data") or []

    async def market_history(
        self, client: httpx.AsyncClient, market_book_id: int
    ) -> Optional[list[dict]]:
        """Curva de precios del market_book (o [] si no hubo cambios)."""
        status, data = await self._get(
            client,
            "/api/v1/markets/history",
            params={"market_book_id": market_book_id},
        )
        if status == 404:
            return []
        if status != 200 or not isinstance(data, dict):
            return None
        return data.get("data") or []
