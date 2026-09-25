"""Cliente de OddsPapi (bet36528, RapidAPI) para backfill histórico de cuotas.

A diferencia del snapshotter (capturas puntuales desde que arrancó el
backend), OddsPapi devuelve la CURVA COMPLETA de cada opción: un array
de `{createdAt, price, active}` por movimiento de precio. Cada punto
se guarda como una fila de `odds_snapshots` con `captured_at` real, así
`compare.py` obtiene la cuota exacta del mercado en el momento en que
el tipster publicó — la auditoría retroactiva que los snapshots no
pueden dar para picks anteriores a su existencia.

Rutas (verificadas 2026-10-06, plan BASIC $0 — histórico ilimitado):

- `GET /markets` -> catálogo GLOBAL de mercados (33k variantes
  mercado x línea; el parámetro sportId se ignora). Cada entrada:
  `marketId`, `marketName`, `marketType`, `period`, `handicap` (la
  línea del mercado: 2.5 en totales, -0.5 en spreads) y `outcomes`.
- `GET /fixtures?sportId&from&to` -> fixtures del rango con
  `participant1Name`/`participant2Name` ("Apellido, Nombre" en tenis),
  `startTime`, `statusName` y `externalProviders` (incluido
  `sofascoreId` — la vía de unión con `parsed_picks.odds_event_id`).
- `GET /historical-odds?fixtureId&bookmakers=bet365` ->
  `{bookmakers: {bet365: {markets: {<marketId>: {outcomes:
  {<outcomeId>: {players: {"0": [{createdAt, price, active}]}}}}}}}}`.
  404 = el fixture nunca tuvo mercados (ITF futures/UTR/exhibiciones:
  ninguna casa los cubre — resultado auditable, no un fallo).

Traducción al vocabulario Sofascore: los `marketName` de OddsPapi se
mapean a los nombres que `compare.py` ya sabe leer ("Full time",
"Match goals", "Asian handicap"...), así el comparador consume estos
snapshots sin cambios. Mercados sin equivalente (props, 1ª parte,
exactos) no se traducen — mismo criterio que `map_pick_choices`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import (
    is_rate_limited,
    log_remaining_quota,
    mark_rate_limited,
    match_score,
)

logger = get_logger("app.odds.oddspapi")

NAME = "oddspapi"
_DEFAULT_HOST = "bet36528.p.rapidapi.com"
_MIN_MATCH_SCORE = 0.6
# Tolerancia al casar el pick con el fixture (misma que el resto de
# proveedores: la fecha del tipster puede diferir ~1 día del inicio).
_DATE_TOLERANCE = timedelta(days=1)
# Tope de puntos guardados por opción: una opción muy movida (live
# incluido) puede traer miles de cambios; con muestreo uniforme +
# extremos se conserva la forma de la curva sin inflar la tabla.
_MAX_POINTS_PER_CHOICE = 300

# Deporte canónico -> sportId de OddsPapi (ver /sports).
SPORT_IDS = {"futbol": 10, "tenis": 12}

# (marketName, period) -> nombre de mercado en el vocabulario que
# `compare.py` sabe leer (el de Sofascore). Lo no traducible se omite.
_MARKET_TRANSLATION = {
    ("Full Time Result", "fulltime"): "Full time",
    ("1X2", "fulltime"): "Full time",
    ("Winner", "result"): "Full time",
    ("First Set Winner", "p1"): "First set winner",
    ("Over Under Full Time", "fulltime"): "Match goals",
    ("Total Games Over Under", "result"): "Total games won",
    ("Total Sets Over Under", "result"): "Total sets",
    ("Asian Handicap", "fulltime"): "Asian handicap",
    ("Game Handicap", "result"): "Game handicap",
    ("Set Handicap", "result"): "Set handicap",
    ("Both Teams To Score", "fulltime"): "Both teams to score",
    ("Double Chance Full Time", "fulltime"): "Double chance",
    ("Draw No Bet", "fulltime"): "Draw no bet",
    ("Corners - Over Under Full Time", "fulltime"): "Corners 2-Way",
    ("Bookings - Over Under Full Time", "fulltime"): "Cards in match",
}
_SPREAD_MARKETS = {"Asian handicap", "Game handicap", "Set handicap"}
_TOTAL_MARKETS = {
    "Match goals",
    "Total games won",
    "Total sets",
    "Corners 2-Way",
    "Cards in match",
}
# La doble oportunidad de OddsPapi usa "2X"; el comparador espera "X2".
_OUTCOME_ALIASES = {"2X": "X2"}


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    """ISO 8601 de OddsPapi -> datetime naive UTC (convención del proyecto)."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


@dataclass
class OddsPapiFixture:
    """Un fixture de OddsPapi con lo necesario para casarlo con un pick."""

    fixture_id: str
    sofascore_id: Optional[int]
    home_name: str
    away_name: str
    start: Optional[datetime]
    sport_id: int


@dataclass
class MarketDef:
    """Entrada del catálogo /markets que define una variante de mercado."""

    market_name: str
    market_type: str
    period: str
    handicap: float
    outcomes: dict[int, str]  # outcomeId -> outcomeName


@dataclass
class PricePoint:
    """Un movimiento de precio de una opción (un snapshot histórico)."""

    captured_at: datetime
    cuota: float
    suspended: bool = False


@dataclass
class HistoricalChoice:
    """Una opción de mercado traducida, con su curva de precios."""

    market_name: str  # vocabulario Sofascore (compare.py)
    choice_name: str
    choice_group: Optional[str]
    points: list[PricePoint]

    @property
    def cuota_apertura(self) -> Optional[float]:
        return self.points[0].cuota if self.points else None


def parse_fixture(raw: dict, sport_id: int) -> Optional[OddsPapiFixture]:
    """Fixture desde el payload de /fixtures; None si le falta lo básico."""
    fixture_id = raw.get("fixtureId")
    home = raw.get("participant1Name") or ""
    away = raw.get("participant2Name") or ""
    if not fixture_id or not home or not away:
        return None
    return OddsPapiFixture(
        fixture_id=str(fixture_id),
        sofascore_id=(raw.get("externalProviders") or {}).get("sofascoreId"),
        home_name=home,
        away_name=away,
        start=_parse_dt(raw.get("startTime")),
        sport_id=sport_id,
    )


def event_ext_id(fixture: OddsPapiFixture) -> str:
    """Id namespaced del evento: reutiliza el espacio sofascore cuando
    existe (enlaza con `odds_event_id` ya resueltos), si no, el propio."""
    if fixture.sofascore_id:
        return f"sofascore:{fixture.sofascore_id}"
    return f"oddspapi:{fixture.fixture_id}"


def match_fixture(
    fixtures: list[OddsPapiFixture],
    hint: str,
    date: datetime,
    sofascore_id: Optional[int] = None,
) -> Optional[OddsPapiFixture]:
    """El fixture del pick: por `sofascoreId` exacto si lo tenemos, si
    no por similitud de nombres contra participant1/2 (±1 día)."""
    if sofascore_id is not None:
        for fixture in fixtures:
            if fixture.sofascore_id == sofascore_id:
                return fixture
        # Id conocido pero no está en el rango: no se casa por nombre
        # (el pick ya estaba enlazado a otro evento concreto).
        return None
    best: Optional[OddsPapiFixture] = None
    best_score = 0.0
    for fixture in fixtures:
        if fixture.start and abs(fixture.start - date) > _DATE_TOLERANCE:
            continue
        score = match_score(hint, fixture.home_name, fixture.away_name)
        if score > best_score:
            best, best_score = fixture, score
    return best if best_score >= _MIN_MATCH_SCORE else None


def _fmt_line(value: float) -> str:
    """Línea como la escribe Sofascore en `choice_group` ("2.5", "-0.5")."""
    return f"{value:g}"


def _choice_label(
    translated: str, outcome_name: str, handicap: float, home: str, away: str
) -> tuple[str, Optional[str]]:
    """(choice_name, choice_group) en formato Sofascore.

    - Spreads: "(hc) HomeTeam" / "(-hc) AwayTeam" con la línea en
      `choice_group` (la convención `handicap` del mercado es la línea
      del participante 1).
    - Totales: "Over"/"Under" con la línea en `choice_group`.
    - Resto: nombre de la opción tal cual ("1", "X", "Yes", "1X"...).
    """
    if translated in _SPREAD_MARKETS:
        if outcome_name == "1":
            return f"({_fmt_line(handicap)}) {home}", _fmt_line(handicap)
        return f"({_fmt_line(-handicap)}) {away}", _fmt_line(-handicap)
    name = _OUTCOME_ALIASES.get(outcome_name, outcome_name)
    if translated in _TOTAL_MARKETS:
        return name, _fmt_line(handicap)
    return name, None


def translate_history(
    payload: dict, markets: dict[int, MarketDef], home: str, away: str
) -> list[HistoricalChoice]:
    """Payload de /historical-odds -> opciones traducidas con su curva.

    Solo mercados con equivalente en el vocabulario del comparador y
    solo precios no-prop (`players["0"]` — los props de jugador no los
    mapea `compare.py` tampoco).
    """
    book = (payload.get("bookmakers") or {}).get("bet365") or {}
    raw_markets = book.get("markets") or {}
    choices: list[HistoricalChoice] = []
    for market_id_str, market in raw_markets.items():
        try:
            market_def = markets.get(int(market_id_str))
        except (TypeError, ValueError):
            continue
        if market_def is None:
            continue
        translated = _MARKET_TRANSLATION.get(
            (market_def.market_name, market_def.period)
        )
        if translated is None:
            continue
        for outcome_id_str, outcome in (market.get("outcomes") or {}).items():
            try:
                outcome_name = market_def.outcomes.get(int(outcome_id_str))
            except (TypeError, ValueError):
                continue
            if outcome_name is None:
                continue
            points: list[PricePoint] = []
            for point in (outcome.get("players") or {}).get("0") or []:
                price = point.get("price")
                captured = _parse_dt(point.get("createdAt"))
                if not isinstance(price, (int, float)) or price <= 0 or not captured:
                    continue
                points.append(
                    PricePoint(
                        captured_at=captured,
                        cuota=float(price),
                        suspended=not point.get("active", True),
                    )
                )
            if not points:
                continue
            points.sort(key=lambda p: p.captured_at)
            choice_name, choice_group = _choice_label(
                translated, outcome_name, market_def.handicap, home, away
            )
            choices.append(
                HistoricalChoice(
                    market_name=translated,
                    choice_name=choice_name,
                    choice_group=choice_group,
                    points=points,
                )
            )
    return choices


def downsample(points: list[PricePoint]) -> list[PricePoint]:
    """Recorta la curva a `_MAX_POINTS_PER_CHOICE` conservando extremos."""
    if len(points) <= _MAX_POINTS_PER_CHOICE:
        return points
    step = (len(points) - 1) / (_MAX_POINTS_PER_CHOICE - 1)
    return [points[round(i * step)] for i in range(_MAX_POINTS_PER_CHOICE)]


class OddsPapiClient:
    """Cliente HTTP de bet36528 con el manejo de cuota del proyecto."""

    def __init__(self, api_key: str, api_host: str = _DEFAULT_HOST) -> None:
        self.NAME = NAME
        self._api_key = api_key
        self._api_host = api_host
        self._markets: Optional[dict[int, MarketDef]] = None
        self._fixture_cache: dict[tuple, Optional[list[OddsPapiFixture]]] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._api_host,
        }

    async def _get(
        self, client: httpx.AsyncClient, path: str, params: Optional[dict] = None
    ) -> tuple[int, Any]:
        """GET -> (status, json). status 0 = error de red; en 403/429
        marca el proveedor sin cuota. 404 devuelve (404, None): para
        historical-odds significa "fixture sin mercados", no un fallo."""
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
        # Cuenta la llamada y captura el límite diario observado (si
        # RapidAPI lo reporta) antes de decidir qué hacer con el status
        # — en un 429 es justo esa respuesta la que trae el header.
        log_remaining_quota(self.NAME, response)
        if response.status_code in (403, 429):
            mark_rate_limited(self.NAME, response)
            logger.warning("[ODDS:%s] Cuota agotada; se omite hasta mañana", self.NAME)
            return response.status_code, None
        if response.status_code >= 400:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    async def markets(
        self, client: httpx.AsyncClient
    ) -> Optional[dict[int, MarketDef]]:
        """Catálogo global de mercados (una llamada por ejecución)."""
        if self._markets is not None:
            return self._markets
        status, data = await self._get(client, "/markets")
        if status != 200 or not isinstance(data, list):
            return None
        catalog: dict[int, MarketDef] = {}
        for raw in data:
            try:
                outcomes = {
                    int(o["outcomeId"]): str(o["outcomeName"])
                    for o in raw.get("outcomes") or []
                }
                catalog[int(raw["marketId"])] = MarketDef(
                    market_name=str(raw.get("marketName") or ""),
                    market_type=str(raw.get("marketType") or ""),
                    period=str(raw.get("period") or ""),
                    handicap=float(raw.get("handicap") or 0.0),
                    outcomes=outcomes,
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._markets = catalog
        return catalog

    async def fixtures(
        self,
        client: httpx.AsyncClient,
        sport_id: int,
        date_from: date,
        date_to: date,
    ) -> Optional[list[OddsPapiFixture]]:
        """Fixtures del rango de fechas (cacheado por deporte+fechas)."""
        key = (sport_id, date_from, date_to)
        if key in self._fixture_cache:
            return self._fixture_cache[key]
        status, data = await self._get(
            client,
            "/fixtures",
            params={
                "sportId": sport_id,
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
            },
        )
        if status != 200 or not isinstance(data, list):
            self._fixture_cache[key] = [] if status == 404 else None
            return self._fixture_cache[key]
        parsed = [f for f in (parse_fixture(r, sport_id) for r in data) if f]
        self._fixture_cache[key] = parsed
        return parsed

    async def historical_odds(
        self, client: httpx.AsyncClient, fixture_id: str
    ) -> tuple[int, Optional[dict]]:
        """(status, payload) de la curva completa del fixture.

        - (200, dict): historial.
        - (404, None): el fixture NUNCA tuvo mercados (ITF/UTR/
          exhibiciones) — definitivo, marcable como miss.
        - Otro status o payload no-dict: error/cuota — NO definitivo,
          el llamador reintenta en otra pasada.
        """
        return await self._get(
            client,
            "/historical-odds",
            params={"fixtureId": fixture_id, "bookmakers": "bet365"},
        )
