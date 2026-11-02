"""Cliente de OddsPapi (bet36528, RapidAPI) para backfill histórico de cuotas.

A diferencia del snapshotter (capturas puntuales desde que arrancó el
backend), OddsPapi devuelve la CURVA COMPLETA de cada opción: un array
de `{createdAt, price, active}` por movimiento de precio. Cada punto
se guarda como una fila de `odds_snapshots` con `captured_at` real, así
`compare.py` obtiene la cuota exacta del mercado en el momento en que
el tipster publicó — la auditoría retroactiva que los snapshots no
pueden dar para picks anteriores a su existencia.

Canal directo (`direct=True`, api.oddspapi.io, verificado 2026-10-23):
misma data pero `/v4/historical-odds` NO consume cuota — solo
`/v4/fixtures` y `/v4/markets` cuentan contra las ~250 req/mes del
plan free. Auth por query param `apiKey`; cooldowns documentados por
endpoint (histórico 5 s, resto 2 s) que el cliente respeta él mismo.
OJO: si la cuota billable llega a 0, el endpoint gratis TAMBIÉN da
429 hasta el reset — conviene reservar siempre margen billable.

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

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

from app.core.dates import utc_now
from app.core.logging import get_logger
from app.services.results.base import (
    _STATE_FILE,
    _load_state,
    _save_state,
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
# Segunda pasada de matching: la `fecha_evento` de muchos picks es la
# del repost en Telegram, no la del partido (Champions a mitad de
# semana publicada el finde, etc.). Con el pool de fixtures servido a
# ±4 días la ventana ancha rescata esos cruces, pero exige más
# similitud de nombre: un fixture equivocado envenena la auditoría,
# peor que un miss honesto.
_WIDE_DATE_TOLERANCE = timedelta(days=4)
_WIDE_MIN_SCORE = 0.75
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
    ("Second Set Winner", "p2"): "Second set winner",
    ("Third Set Winner", "p3"): "Third set winner",
    ("Over Under Full Time", "fulltime"): "Match goals",
    ("Total Games Over Under", "result"): "Total games won",
    ("Total Sets Over Under", "result"): "Total sets",
    ("Asian Handicap", "fulltime"): "Asian handicap",
    ("Game Handicap", "result"): "Game handicap",
    # Hándicap de juegos dentro de un set ("Alcaraz -1.5 juegos 1er
    # set"): mercado propio, distinto del hándicap del partido.
    ("Game Handicap First Set", "p1"): "1st set game handicap",
    ("Game Handicap Second Set", "p2"): "2nd set game handicap",
    ("Game Handicap Third Set", "p3"): "3rd set game handicap",
    ("Set Handicap", "result"): "Set handicap",
    ("Both Teams To Score", "fulltime"): "Both teams to score",
    ("Double Chance Full Time", "fulltime"): "Double chance",
    ("Draw No Bet", "fulltime"): "Draw no bet",
    ("Correct Score Full Time", "fulltime"): "Correct score",
    # Sets ganados / sets exactos / paridad / marcadores de tenis.
    # "Correct Score" [result] es el marcador EN SETS (2:0, 2:1...);
    # los Correct Score por set son en JUEGOS (6:4...). Los "After
    # Four/Six Games" son mercados en vivo que ningún pick pide.
    ("Participant 1 To Win a Set", "result"): "Player 1 to win a set",
    ("Participant 2 To Win a Set", "result"): "Player 2 to win a set",
    ("Both Participants To Win a Set", "result"): "Both to win a set",
    ("Exact Sets", "result"): "Exact sets",
    ("Odd Even Games", "result"): "Games odd/even",
    ("Correct Score", "result"): "Correct score",
    ("Correct Score First Set", "p1"): "1st set correct score",
    ("Correct Score Second Set", "p2"): "2nd set correct score",
    ("Correct Score Third Set", "p3"): "3rd set correct score",
    # Tenis: juegos del primer set — el comparador los busca como
    # "1st set games" (mismo nombre que publica Sofascore).
    ("Total Games First Set", "p1"): "1st set games",
    ("Total Games Second Set", "p2"): "2nd set games",
    ("Total Games Third Set", "p3"): "3rd set games",
    # Stats de tenis a nivel partido (los picks "25+ aces" existen:
    # el mercado está en el payload, solo faltaba traducirlo).
    # Total de juegos ganados por cada jugador ("Alcaraz más de 12.5
    # juegos"): Participant N = local/visitante del fixture, como
    # "Team N" en fútbol.
    ("Participant 1 Total Games", "result"): "Player 1 games",
    ("Participant 2 Total Games", "result"): "Player 2 games",
    ("Total Aces", "result"): "Total aces",
    ("Total Double Faults", "result"): "Total double faults",
    ("Total Breaks", "result"): "Total breaks",
    ("Total Tie Breaks", "result"): "Total tie breaks",
    ("Total Service Games", "result"): "Total service games",
    ("Corners - Over Under Full Time", "fulltime"): "Corners 2-Way",
    ("Bookings - Over Under Full Time", "fulltime"): "Cards in match",
    # Totales por equipo: el comparador los pide cuando el pick nombra
    # un equipo del evento ("Madrid +7 córners" ≠ total del partido).
    ("Corners - Over Under Team 1", "fulltime"): "Team 1 corners",
    ("Corners - Over Under Team 2", "fulltime"): "Team 2 corners",
    ("Bookings - Over Under Team 1", "fulltime"): "Team 1 cards",
    ("Bookings - Over Under Team 2", "fulltime"): "Team 2 cards",
    ("Over Under Team 1", "fulltime"): "Team 1 goals",
    ("Over Under Team 2", "fulltime"): "Team 2 goals",
    # Fútbol por mitad ("+1.5 goles 1ª parte", "+4 córners 1T"): los
    # nombres canónicos "1st half X" son los mismos que Sofascore.
    ("Over Under First Half", "p1"): "1st half goals",
    ("Over Under Second Half", "p2"): "2nd half goals",
    ("Corners - Over Under First Half", "p1"): "1st half corners",
    ("Corners - Over Under Second Half", "p2"): "2nd half corners",
    ("Bookings - Over Under First Half", "p1"): "1st half bookings",
    ("Bookings - Over Under Second Half", "p2"): "2nd half bookings",
    # 1X2 del descanso: "1st half" es el nombre que ya espera el
    # comparador para "gana X la primera parte".
    ("First Half Result", "p1"): "1st half",
    ("Second Half Result", "p2"): "2nd half result",
    ("Both Teams To Score First Half", "p1"): "1st half btts",
    ("Asian Handicap First Half", "p1"): "1st half handicap",
    # Totales por equipo Y mitad ("+5 córners del Betis en la 2ª
    # parte"): el comparador genera "Nth half Team N X". La
    # convención Team-sigue-"cards" (igual que "Team N cards" del
    # fulltime y que betsapi/pulsescore), no "bookings".
    ("Over Under Team 1 First Half", "p1"): "1st half Team 1 goals",
    ("Over Under Team 2 First Half", "p1"): "1st half Team 2 goals",
    ("Over Under Team 1 Second Half", "p2"): "2nd half Team 1 goals",
    ("Over Under Team 2 Second Half", "p2"): "2nd half Team 2 goals",
    ("Corners - Over Under Team 1 First Half", "p1"): ("1st half Team 1 corners"),
    ("Corners - Over Under Team 2 First Half", "p1"): ("1st half Team 2 corners"),
    ("Corners - Over Under Team 1 Second Half", "p2"): ("2nd half Team 1 corners"),
    ("Corners - Over Under Team 2 Second Half", "p2"): ("2nd half Team 2 corners"),
    ("Bookings - Over Under Team 1 First Half", "p1"): ("1st half Team 1 cards"),
    ("Bookings - Over Under Team 2 First Half", "p1"): ("1st half Team 2 cards"),
    ("Bookings - Over Under Team 1 Second Half", "p2"): ("2nd half Team 1 cards"),
    ("Bookings - Over Under Team 2 Second Half", "p2"): ("2nd half Team 2 cards"),
    # Portería a cero / win to nil / equipo marca: Yes/No por equipo.
    ("Team 1 Clean Sheet", "fulltime"): "Team 1 clean sheet",
    ("Team 2 Clean Sheet", "fulltime"): "Team 2 clean sheet",
    ("Team 1 Win To Nil", "fulltime"): "Team 1 win to nil",
    ("Team 2 Win To Nil", "fulltime"): "Team 2 win to nil",
    ("Team 1 Win To Nil First Half", "p1"): "1st half Team 1 win to nil",
    ("Team 2 Win To Nil First Half", "p1"): "1st half Team 2 win to nil",
    ("Team 1 To Score", "fulltime"): "Team 1 to score",
    ("Team 2 To Score", "fulltime"): "Team 2 to score",
    ("Team 1 To Score First Half", "p1"): "1st half Team 1 to score",
    ("Team 2 To Score First Half", "p1"): "1st half Team 2 to score",
    ("Team 1 To Score Second Half", "p2"): "2nd half Team 1 to score",
    ("Team 2 To Score Second Half", "p2"): "2nd half Team 2 to score",
    ("Team 1 To Score in Both Halves", "fulltime"): ("Team 1 to score both halves"),
    ("Team 2 To Score in Both Halves", "fulltime"): ("Team 2 to score both halves"),
    # Hándicap europeo = 1X2 CON línea (el empate "X" es una opción,
    # no push como el asiático). La línea viaja en `choice_group`.
    ("European Handicap", "fulltime"): "European handicap",
    ("European Handicap First Half", "p1"): "1st half european handicap",
    ("European Handicap Second Half", "p2"): "2nd half european handicap",
    # 1X2 y hándicap de córners/tarjetas: "saca más córners X",
    # "empate a córners", "hándicap córners -3.5".
    ("Corners - 1X2", "fulltime"): "Corners 1X2",
    ("Corners - 1X2 First Half", "p1"): "1st half corners 1X2",
    ("Corners - 1X2 Second Half", "p2"): "2nd half corners 1X2",
    ("Corners - Handicap", "fulltime"): "Corners handicap",
    ("Corners - Handicap First Half", "p1"): "1st half corners handicap",
    ("Corners - Handicap Second Half", "p2"): "2nd half corners handicap",
    ("Bookings - 1X2", "fulltime"): "Cards 1X2",
    ("Bookings - 1X2 First Half", "p1"): "1st half cards 1X2",
    ("Bookings - 1X2 Second Half", "p2"): "2nd half cards 1X2",
    ("Bookings - Handicap", "fulltime"): "Cards handicap",
    ("Bookings - Handicap First Half", "p1"): "1st half cards handicap",
    ("Bookings - Handicap Second Half", "p2"): "2nd half cards handicap",
    # Especiales de goles / marcadores / descanso-final / menores.
    ("Half Time / Full Time", "fulltime"): "Half time/Full time",
    ("First Goal Full Time", "fulltime"): "First team to score",
    ("Last Goal Full Time", "fulltime"): "Last team to score",
    ("Correct Score First Half", "p1"): "1st half correct score",
    ("Correct Score Second Half", "p2"): "2nd half correct score",
    ("Odd Even Full Time", "fulltime"): "Goals odd/even",
    ("Odd Even First Half", "p1"): "1st half goals odd/even",
    ("Odd Even Second Half", "p2"): "2nd half goals odd/even",
    ("Team 1 Odd Even", "fulltime"): "Team 1 goals odd/even",
    ("Team 2 Odd Even", "fulltime"): "Team 2 goals odd/even",
    ("Both Teams To Score Second Half", "p2"): "2nd half btts",
    ("Asian Handicap Second Half", "p2"): "2nd half handicap",
    ("Double Chance First Half", "p1"): "1st half double chance",
    ("Double Chance Second Half", "p2"): "2nd half double chance",
    ("Draw No Bet First Half", "p1"): "1st half draw no bet",
    ("Draw No Bet Second Half", "p2"): "2nd half draw no bet",
    ("Highest Scoring Half", "fulltime"): "Highest scoring half",
    ("To Win From Behind Full Time", "fulltime"): "To win from behind",
}
# Props de jugador -> vocabulario del comparador (el mismo que emite
# theoddsapi.py). En el payload el precio del equipo está en
# `players["0"]` y cada `players["<playerId>"]` es un jugador; el
# nombre se resuelve con el diccionario de /v4/players (players_<sport>.json).
_PROP_TRANSLATION = {
    ("Anytime Goal Scorer", "fulltime"): "Goalscorer anytime",
    ("First Goal Scorer", "fulltime"): "First goalscorer",
    ("Last Goal Scorer", "fulltime"): "Last goalscorer",
    ("Player To Be Carded (incl. overtime)", "result"): "Player to be booked",
    ("Over Under Player Assists (incl. overtime)", "result"): "Player assists",
    ("Over Under Player Shots (incl. overtime)", "result"): "Player shots",
    ("Over Under Player Shots On Goal (incl. overtime)", "result"): (
        "Player shots on target"
    ),
    ("Player Fouls Committed (incl. overtime)", "result"): "Player fouls",
    # "1+ goles" es el mismo contrato que "marca en cualquier momento":
    # los conteos "N+"/"0" se emiten como líneas Over/Under bajo el
    # nombre canónico del scorer.
    ("Player Goals (incl. overtime)", "result"): "Goalscorer anytime",
    # Variantes O/U y de conteo del resto de props de fútbol. Las
    # formas de conteo ("N+"/"0") pasan por `_prop_group` — salen como
    # líneas "Over N-0.5"/"Under 0.5", mismo contrato.
    ("Over Under Player Goals (incl. overtime)", "result"): "Player goals",
    ("Player Assists (incl. overtime)", "result"): "Player assists",
    ("Player Assists (incl. overtime)", "fulltime+overtime"): "Player assists",
    ("Player Shots (incl. overtime)", "result"): "Player shots",
    ("Over Under Player Shots (incl. overtime)", "fulltime+overtime"): ("Player shots"),
    ("Player Shots On Goal (incl. overtime)", "result"): ("Player shots on target"),
    ("Over Under Player Fouls Committed (incl. overtime)", "result"): ("Player fouls"),
    ("Over Under Player To Be Carded (incl. overtime)", "result"): (
        "Player to be booked"
    ),
    ("Over Under Player Goalkeeper Saves (incl. overtime)", "result"): ("Player saves"),
    ("Over Under Player Tackles (incl. overtime)", "result"): "Player tackles",
    ("Player Tackles (incl. overtime)", "result"): "Player tackles",
    ("Over Under Player Offsides (incl. overtime)", "result"): ("Player offsides"),
    ("Player Offsides (incl. overtime)", "result"): "Player offsides",
}
_PROP_COUNT_OUTCOME = re.compile(r"^(\d+)\+$")

_SPREAD_MARKETS = {
    "Asian handicap",
    "Game handicap",
    "Set handicap",
    "1st set game handicap",
    "2nd set game handicap",
    "3rd set game handicap",
    "1st half handicap",
    "Corners handicap",
    "1st half corners handicap",
    "2nd half corners handicap",
    "Cards handicap",
    "1st half cards handicap",
    "2nd half cards handicap",
    "2nd half handicap",
}
# 1X2 con línea (hándicap europeo): choices "1"/"X"/"2" y la línea
# en `choice_group` — como los totales pero con tres opciones.
_LINE_3WAY_MARKETS = {
    "European handicap",
    "1st half european handicap",
    "2nd half european handicap",
}
_TOTAL_MARKETS = {
    "Match goals",
    "Total games won",
    "Total sets",
    "Corners 2-Way",
    "Cards in match",
    "Team 1 corners",
    "Team 2 corners",
    "Team 1 cards",
    "Team 2 cards",
    "Team 1 goals",
    "Team 2 goals",
    "1st half goals",
    "2nd half goals",
    "1st half corners",
    "2nd half corners",
    "1st half bookings",
    "2nd half bookings",
    "1st half Team 1 goals",
    "1st half Team 2 goals",
    "2nd half Team 1 goals",
    "2nd half Team 2 goals",
    "1st half Team 1 corners",
    "1st half Team 2 corners",
    "2nd half Team 1 corners",
    "2nd half Team 2 corners",
    "1st half Team 1 cards",
    "1st half Team 2 cards",
    "2nd half Team 1 cards",
    "2nd half Team 2 cards",
    "1st set games",
    "2nd set games",
    "3rd set games",
    "Player 1 games",
    "Player 2 games",
    "Total aces",
    "Total double faults",
    "Total breaks",
    "Total tie breaks",
    "Total service games",
}
# La doble oportunidad de OddsPapi usa "2X"; el comparador espera "X2".
_OUTCOME_ALIASES = {"2X": "X2", "No Goal": "X"}


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
    """El fixture del pick en dos pasadas:

    1. `sofascoreId` exacto si lo tenemos.
    2. Similitud de nombres dentro de `_DATE_TOLERANCE` (±1 día).
    3. Ventana ancha (`_WIDE_DATE_TOLERANCE`, ±4 días) para picks cuya
       `fecha_evento` era la del repost: exige `_WIDE_MIN_SCORE` y
       desempata por cercanía de fecha.
    """
    # El id lo enlazó el snapshotter en vivo contra OTRO proveedor:
    # si OddsPapi no lo lleva en su mapping el pool no lo contendrá
    # nunca — vetoear ahí condena picks cuyo fixture está delante. En
    # ese caso se casa por nombre, pero solo en ventana estricta (la
    # ancha es para fechas de repost; aquí la fecha viene del evento
    # enlazado, así que exigir cercanía es gratis).
    strict_only = sofascore_id is not None
    if sofascore_id is not None:
        for fixture in fixtures:
            if fixture.sofascore_id == sofascore_id:
                return fixture
    best: Optional[OddsPapiFixture] = None
    best_score = 0.0
    wide: list[tuple[OddsPapiFixture, float, float]] = []
    for fixture in fixtures:
        if not fixture.start:
            continue
        distance = abs((fixture.start - date).total_seconds())
        score = match_score(hint, fixture.home_name, fixture.away_name)
        if fixture.start and abs(fixture.start - date) <= _DATE_TOLERANCE:
            if score > best_score:
                best, best_score = fixture, score
        elif (
            not strict_only
            and distance <= _WIDE_DATE_TOLERANCE.total_seconds()
            and score >= _WIDE_MIN_SCORE
        ):
            wide.append((fixture, score, distance))
    if best is not None and best_score >= _MIN_MATCH_SCORE:
        return best
    if not wide:
        return None
    # Identidad primero; a igualdad de nombre gana la fecha más cercana
    # al repost.
    wide.sort(key=lambda t: (-t[1], t[2]))
    return wide[0][0]


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
    if translated.lower().endswith("correct score"):
        # OddsPapi publica "3:1"/"6:4"; el comparador busca "3-1"/"6-4".
        return name.replace(":", "-"), None
    if translated in _TOTAL_MARKETS or translated in _LINE_3WAY_MARKETS:
        return name, _fmt_line(handicap)
    return name, None


def _price_points(raw_points) -> list[PricePoint]:
    """Lista de puntos de precio válidos de una lista cruda del payload."""
    points: list[PricePoint] = []
    for point in raw_points or []:
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
    return points


def _prop_group(outcome_name: str, handicap: float) -> Optional[str]:
    """`choice_group` de una opción prop: la línea O/U del mercado,
    y para outcomes de conteo la línea entera equivalente
    ("1+" == "Over 0.5", "0" == "Under 0.5")."""
    if outcome_name in ("Over", "Under"):
        return f"{outcome_name} {_fmt_line(handicap)}"
    if outcome_name == "0":
        return "Under 0.5"
    m = _PROP_COUNT_OUTCOME.fullmatch(outcome_name)
    if m:
        return f"Over {_fmt_line(int(m.group(1)) - 0.5)}"
    return None


def _player_name(name: str) -> str:
    """'Apellido, Nombre' del catálogo /v4/players -> 'Nombre Apellido'."""
    if ", " in name:
        last, first = name.split(", ", 1)
        return f"{first} {last}"
    return name


# Casas pedidas a /historical-odds (máx 3 por llamada según la API).
# bet365 está regionalizado: el slug global "bet365" suele venir
# vacío — las cuotas viven en bet365.es / bet365.de / etc. Pedir
# solo "bet365" devolvía 404 en fixtures que SÍ tienen curva.
_BOOKMAKERS_REQUEST = "bet365,bet365.es,pinnacle"


def _book_priority(slug: str) -> tuple[int, str]:
    """bet365 global > variantes regionales bet365.* > el resto."""
    if slug == "bet365":
        return (0, slug)
    if slug.startswith("bet365"):
        return (1, slug)
    return (2, slug)


def translate_history(
    payload: dict,
    markets: dict[int, MarketDef],
    home: str,
    away: str,
    players: Optional[dict[str, str]] = None,
) -> list[HistoricalChoice]:
    """Payload de /historical-odds -> opciones traducidas con su curva.

    Mercados de equipo leen `players["0"]`; los mercados prop de
    `_PROP_TRANSLATION` leen cada `players["<playerId>"]` y resuelven
    el nombre con `players` (diccionario de /v4/players). Sin
    diccionario los props se omiten — mismo resultado que antes.
    """
    books = payload.get("bookmakers") or {}
    # Une mercados de todas las casas: el primer book por prioridad
    # que publica un mercado gana (bet365* tiene más cobertura).
    raw_markets: dict[str, dict] = {}
    for slug in sorted(books, key=_book_priority):
        for market_id, market in (books.get(slug) or {}).get("markets", {}).items():
            raw_markets.setdefault(str(market_id), market)
    choices: list[HistoricalChoice] = []
    for market_id_str, market in raw_markets.items():
        try:
            market_def = markets.get(int(market_id_str))
        except (TypeError, ValueError):
            continue
        if market_def is None:
            continue
        key = (market_def.market_name, market_def.period)
        prop_name = _PROP_TRANSLATION.get(key)
        translated = _MARKET_TRANSLATION.get(key)
        if prop_name is None and translated is None:
            continue
        for outcome_id_str, outcome in (market.get("outcomes") or {}).items():
            try:
                outcome_name = market_def.outcomes.get(int(outcome_id_str))
            except (TypeError, ValueError):
                continue
            if outcome_name is None:
                continue
            if prop_name is not None:
                # Cada clave de jugador es una opción distinta: el
                # comparador la busca por nombre en `choice_name`.
                group = (
                    None
                    if outcome_name == "Yes"
                    else _prop_group(outcome_name, market_def.handicap)
                )
                if outcome_name != "Yes" and group is None:
                    continue
                for player_id, raw_points in (outcome.get("players") or {}).items():
                    if player_id == "0":
                        continue
                    player = (players or {}).get(str(player_id))
                    if not player:
                        continue
                    points = _price_points(raw_points)
                    if not points:
                        continue
                    points.sort(key=lambda p: p.captured_at)
                    choices.append(
                        HistoricalChoice(
                            market_name=prop_name,
                            choice_name=player,
                            choice_group=group,
                            points=points,
                        )
                    )
                continue
            if translated is None:
                continue
            points = _price_points((outcome.get("players") or {}).get("0") or [])
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


# Carpeta de auditoría junto a provider_state.json: cada respuesta de
# /historical-odds y del catálogo /markets queda guardada. Re-traducir
# tras mejorar el mapa de mercados no vuelve a pagar la llamada — los
# picks marcados `nomarket` se reintentan contra disco gratis.
_DATA_DIR = Path(__file__).resolve().parents[3] / "oddspapi_data"


def _safe_name(ext_id: str) -> str:
    return ext_id.replace(":", "_").replace("/", "_")


def payload_path(ext_id: str, base: Optional[Path] = None) -> Path:
    return (base or _DATA_DIR) / "payloads" / f"{_safe_name(ext_id)}.json"


def save_payload(
    fixture: OddsPapiFixture,
    ext_id: str,
    payload: dict,
    base: Optional[Path] = None,
) -> None:
    """Guarda la respuesta cruda de /historical-odds con el contexto
    mínimo para reconstruir el evento y re-traducirla offline."""
    path = payload_path(ext_id, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "ext_id": ext_id,
        "fixture_id": fixture.fixture_id,
        "home_name": fixture.home_name,
        "away_name": fixture.away_name,
        "start": fixture.start.isoformat() if fixture.start else None,
        "sport_id": fixture.sport_id,
        "fetched_at": utc_now().isoformat(),
        "payload": payload,
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")


def load_payload(ext_id: str, base: Optional[Path] = None) -> Optional[dict]:
    """El sobre guardado por `save_payload`, o None si no existe."""
    path = payload_path(ext_id, base)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "payload" in data else None


def save_markets(markets: dict[int, MarketDef], base: Optional[Path] = None) -> None:
    """Catálogo /markets a disco: la re-traducción offline lo necesita."""
    directory = base or _DATA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    serial = {
        str(market_id): {
            "market_name": d.market_name,
            "market_type": d.market_type,
            "period": d.period,
            "handicap": d.handicap,
            "outcomes": d.outcomes,
        }
        for market_id, d in markets.items()
    }
    (directory / "markets.json").write_text(json.dumps(serial), encoding="utf-8")


def load_markets(base: Optional[Path] = None) -> Optional[dict[int, MarketDef]]:
    path = (base or _DATA_DIR) / "markets.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            int(market_id): MarketDef(
                market_name=str(v["market_name"]),
                market_type=str(v["market_type"]),
                period=str(v["period"]),
                handicap=float(v["handicap"]),
                outcomes={int(oi): str(on) for oi, on in v["outcomes"].items()},
            )
            for market_id, v in raw.items()
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- Caché de fixtures por día -------------------------------------------
#
# `/v4/fixtures` es BILLABLE (250/mes en el plan free) y la caché del
# cliente vive en memoria: cada pasada del backfill re-pagaba los
# rangos ya descargados — así se fundieron 250 req en 48 h y el 429
# se llevó por delante también el histórico gratis. Un día pasado es
# inmutable: su listado se paga una vez y queda en
# `oddspapi_data/fixtures/` para siempre.


def _fixture_day_path(sport_id: int, day: date, base: Optional[Path]) -> Path:
    return (base or _DATA_DIR) / "fixtures" / f"{sport_id}_{day.isoformat()}.json"


def save_fixture_day(
    sport_id: int,
    day: date,
    fixtures: list[OddsPapiFixture],
    base: Optional[Path] = None,
) -> None:
    path = _fixture_day_path(sport_id, day, base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        "fixture_id": f.fixture_id,
                        "sofascore_id": f.sofascore_id,
                        "home_name": f.home_name,
                        "away_name": f.away_name,
                        "start": f.start.isoformat() if f.start else None,
                    }
                    for f in fixtures
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


# --- Caché de participantes y de fixtures por participante ---------
#
# El barrido por día gasta ~1 billable por fecha aunque el backlog
# solo pida unos pocos equipos. La vía dirigida es al revés:
# `/v4/participants?sportId` (1 llamada, catálogo casi inmutable) da
# equipo -> participantId, y `/v4/fixtures?participantId` (1 llamada
# por equipo) trae TODOS sus fixtures de golpe. Ambos resultados se
# cachean a disco: el catálogo 30 días, la lista por equipo 7.

_PARTICIPANT_MAX_AGE = timedelta(days=30)
_PARTICIPANT_FIXTURES_MAX_AGE = timedelta(days=7)


def _participants_path(sport_id: int, base: Optional[Path]) -> Path:
    return (base or _DATA_DIR) / "participants" / f"{sport_id}.json"


def save_participants(
    sport_id: int, participants: dict[int, str], base: Optional[Path] = None
) -> None:
    path = _participants_path(sport_id, base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "fetched_at": utc_now().isoformat(),
                    "participants": {str(k): v for k, v in participants.items()},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_participants(
    sport_id: int, base: Optional[Path] = None
) -> Optional[dict[int, str]]:
    path = _participants_path(sport_id, base)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(data.get("fetched_at")))
        if utc_now() - fetched > _PARTICIPANT_MAX_AGE:
            return None
        return {int(k): str(v) for k, v in (data.get("participants") or {}).items()}
    except (OSError, ValueError, TypeError):
        return None


# --- Caché del diccionario de jugadores ---------------------------------
#
# `/v4/players?sportId=` devuelve plantillas por equipo
# ({participants: {<participantId>: {players: [{playerId,
# playerName}]}}}). Es billable pero se paga una vez: el catálogo se
# guarda en `oddspapi_data/players_<sport>.json` y solo lo consulta la
# traducción de props.


def _players_path(sport_id: int, base: Optional[Path]) -> Path:
    return (base or _DATA_DIR) / f"players_{sport_id}.json"


def save_players(sport_id: int, payload: dict, base: Optional[Path] = None) -> None:
    path = _players_path(sport_id, base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"fetched_at": utc_now().isoformat(), "payload": payload},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_players(
    sport_id: int, base: Optional[Path] = None
) -> Optional[dict[str, str]]:
    """playerId -> 'Nombre Apellido' (índice plano sobre todas las
    plantillas; el histórico solo trae el id, no el equipo)."""
    path = _players_path(sport_id, base)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        payload = data.get("payload", data)
        out: dict[str, str] = {}
        for block in (payload.get("participants") or {}).values():
            for p in (block or {}).get("players") or []:
                pid, name = p.get("playerId"), p.get("playerName")
                if pid and name:
                    out[str(pid)] = _player_name(str(name))
        return out
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _participant_fixtures_path(
    sport_id: int, participant_id: int, base: Optional[Path]
) -> Path:
    return (
        (base or _DATA_DIR)
        / "fixtures_participant"
        / f"{sport_id}_{participant_id}.json"
    )


def save_participant_fixtures(
    sport_id: int,
    participant_id: int,
    fixtures: list[OddsPapiFixture],
    base: Optional[Path] = None,
) -> None:
    path = _participant_fixtures_path(sport_id, participant_id, base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "fetched_at": utc_now().isoformat(),
                    "fixtures": [
                        {
                            "fixture_id": f.fixture_id,
                            "sofascore_id": f.sofascore_id,
                            "home_name": f.home_name,
                            "away_name": f.away_name,
                            "start": f.start.isoformat() if f.start else None,
                        }
                        for f in fixtures
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_participant_fixtures(
    sport_id: int, participant_id: int, base: Optional[Path] = None
) -> Optional[list[OddsPapiFixture]]:
    path = _participant_fixtures_path(sport_id, participant_id, base)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(data.get("fetched_at")))
        if utc_now() - fetched > _PARTICIPANT_FIXTURES_MAX_AGE:
            return None
        rows = data.get("fixtures") or []
    except (OSError, ValueError, TypeError):
        return None
    out: list[OddsPapiFixture] = []
    for raw in rows:
        start = None
        if raw.get("start"):
            try:
                start = datetime.fromisoformat(raw["start"])
            except ValueError:
                pass
        out.append(
            OddsPapiFixture(
                fixture_id=str(raw.get("fixture_id") or ""),
                sofascore_id=raw.get("sofascore_id"),
                home_name=raw.get("home_name") or "",
                away_name=raw.get("away_name") or "",
                start=start,
                sport_id=sport_id,
            )
        )
    return out


def load_fixture_day(
    sport_id: int, day: date, base: Optional[Path] = None
) -> Optional[list[OddsPapiFixture]]:
    path = _fixture_day_path(sport_id, day, base)
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out: list[OddsPapiFixture] = []
    for raw in rows:
        start = None
        if raw.get("start"):
            try:
                start = datetime.fromisoformat(raw["start"])
            except ValueError:
                pass
        out.append(
            OddsPapiFixture(
                fixture_id=str(raw.get("fixture_id") or ""),
                sofascore_id=raw.get("sofascore_id"),
                home_name=raw.get("home_name") or "",
                away_name=raw.get("away_name") or "",
                start=start,
                sport_id=sport_id,
            )
        )
    return out


_DIRECT_BASE = "https://api.oddspapi.io"
# Cooldowns documentados de la API directa (segundos mínimos entre
# llamadas al MISMO endpoint): historical-odds 5 s, fixtures 2 s.
# markets comparte el cooldown genérico billable. El servidor aplica
# rate-limit por ráfaga y en el 429 devuelve `error.retryMs` — si nos
# saltamos la ventana hay que esperarla y reintentar, no aparcar la
# fuente (un 429 transitorio no es cuota mensual agotada).
_ENDPOINT_COOLDOWNS = {
    "/historical-odds": 5.0,
    "/fixtures": 2.0,
    "/markets": 2.0,
    "/participants": 1.0,
}
# En modo directo estos endpoints SÍ consumen la cuota mensual; el
# histórico es gratis por diseño del proveedor.
_DIRECT_BILLABLE = {"/fixtures", "/markets", "/participants"}
_DIRECT_429_RETRIES = 4
# Tope mensual de billables del canal directo (plan free 250/mes):
# a 250/250 el histórico gratis TAMBIÉN devuelve 429 — así murió la
# primera cuenta. Parar ~15 antes es el seguro que mantiene vivo el
# endpoint gratis todo el mes.
_DIRECT_MONTHLY_CAP = 235


def _billable_period() -> str:
    return utc_now().strftime("%Y-%m")


def direct_billable_count() -> int:
    """Billables gastadas este mes natural — persistente entre pasadas
    y procesos (vive en provider_state.json, no en memoria)."""
    entry = (_load_state().get("billable_month") or {}).get(NAME)
    if not isinstance(entry, dict) or entry.get("period") != _billable_period():
        return 0
    return int(entry.get("count") or 0)


def _bump_billable() -> None:
    state = _load_state()
    table = state.setdefault("billable_month", {})
    entry = table.get(NAME)
    if not isinstance(entry, dict) or entry.get("period") != _billable_period():
        entry = {"period": _billable_period(), "count": 0}
    # Otra réplica (sonda `docker exec`, script manual) puede haber
    # sumado a disco mientras este proceso tenía `_STATE` cacheado —
    # sin releer, el próximo `_save_state` pisaría ese gasto y el freno
    # mensual quedaría por debajo del gasto real.
    try:
        disk = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        d_entry = (disk.get("billable_month") or {}).get(NAME)
        if isinstance(d_entry, dict) and d_entry.get("period") == _billable_period():
            entry["count"] = max(
                int(entry.get("count") or 0), int(d_entry.get("count") or 0)
            )
    except (OSError, ValueError, TypeError):
        pass
    entry["count"] = int(entry.get("count") or 0) + 1
    table[NAME] = entry
    _save_state()


async def sync_billable_count(api_key: str) -> Optional[int]:
    """Reconcilia el contador local con `/v4/account` del proveedor.

    El endpoint es no-medido y nunca se bloquea (diseño del proveedor:
    sirve justo para consultar la cuota restante). El contador local
    puede derivar — los reintentos 429 cuentan en el proveedor y rutas
    ajenas al cliente pueden no bump-ar — así que una pasada diaria
    desde el loop de lifecycle deja el freno mensual pegado a la
    realidad en vez de a nuestra cuenta aproximada.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{_DIRECT_BASE}/v4/account", params={"apiKey": api_key}
            )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        subs = response.json().get("subscriptions") or []
        count = int(subs[0]["request_count"])
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
    state = _load_state()
    table = state.setdefault("billable_month", {})
    table[NAME] = {"period": _billable_period(), "count": count}
    _save_state()
    return count


def _direct_retry_seconds(response: httpx.Response) -> Optional[float]:
    """Espera que pide la API directa en un 429 (`error.retryMs` /
    `error.retryAfter`), o None si el cuerpo no lo trae."""
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        return None
    retry_ms = error.get("retryMs")
    if isinstance(retry_ms, (int, float)) and retry_ms > 0:
        return retry_ms / 1000
    raw = str(error.get("retryAfter") or "")
    match = re.match(r"([\d.]+)\s*seconds?", raw)
    return float(match.group(1)) if match else None


class OddsPapiClient:
    """Cliente HTTP de bet36528 / api.oddspapi.io con cuota del proyecto.

    `direct=True` cambia tres cosas: auth por query param `apiKey` en
    vez de headers RapidAPI, paths bajo `/v4`, y `self.calls` solo
    cuenta endpoints billables (el histórico es gratis ahí).
    """

    def __init__(
        self,
        api_key: str,
        api_host: str = _DEFAULT_HOST,
        direct: bool = False,
    ) -> None:
        self.NAME = NAME
        self._api_key = api_key
        self._api_host = api_host
        self._direct = direct
        self._markets: Optional[dict[int, MarketDef]] = None
        self._fixture_cache: dict[tuple, Optional[list[OddsPapiFixture]]] = {}
        self._last_hit: dict[str, float] = {}
        # Peticiones billables emitidas por esta instancia: el plan son
        # ~200-250 req/MES — el backfill la dosifica por pasada.
        self.calls = 0

    def _headers(self) -> dict[str, str]:
        if self._direct:
            return {}
        return {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._api_host,
        }

    def _url(self, path: str) -> str:
        return (
            f"{_DIRECT_BASE}/v4{path}"
            if self._direct
            else f"https://{self._api_host}{path}"
        )

    async def _cooldown(self, path: str) -> None:
        """Respeta el cooldown por endpoint de la API directa."""
        if not self._direct:
            return
        wait = _ENDPOINT_COOLDOWNS.get(path, 2.0) - (
            time.monotonic() - self._last_hit.get(path, 0.0)
        )
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_hit[path] = time.monotonic()

    async def _get(
        self, client: httpx.AsyncClient, path: str, params: Optional[dict] = None
    ) -> tuple[int, Any]:
        """GET -> (status, json). status 0 = error de red; en 403/429
        marca el proveedor sin cuota. 404 devuelve (404, None): para
        historical-odds significa "fixture sin mercados", no un fallo.

        Modo directo: un 429 es rate-limit de ráfaga (la API pide una
        espera concreta en `retryMs`), no cuota agotada — se espera y
        reintenta unas veces antes de rendirse. Solo se aparca la fuente
        si el 429 persiste o es un 403 real (cuota mensual)."""
        if is_rate_limited(self.NAME):
            return 0, None
        billable = not self._direct or path in _DIRECT_BILLABLE
        query = dict(params or {})
        if self._direct:
            query["apiKey"] = self._api_key
        response: Optional[httpx.Response] = None
        for attempt in range(_DIRECT_429_RETRIES if self._direct else 1):
            if billable:
                # Freno de seguridad: agotar la cuota mensual tumba
                # también el histórico gratis (429 global). Mejor dejar
                # picks sin revisar este ciclo que matar el canal.
                if self._direct and direct_billable_count() >= _DIRECT_MONTHLY_CAP:
                    logger.warning(
                        "[ODDS:%s] tope mensual billable (%d) — freno para "
                        "conservar el histórico gratis",
                        self.NAME,
                        _DIRECT_MONTHLY_CAP,
                    )
                    return 0, None
                self.calls += 1
                if self._direct:
                    _bump_billable()
            await self._cooldown(path)
            try:
                response = await client.get(
                    self._url(path),
                    headers=self._headers(),
                    params=query,
                )
            except httpx.HTTPError as exc:
                logger.warning("[ODDS:%s] Error de red en %s: %s", self.NAME, path, exc)
                return 0, None
            # Cuenta la llamada y captura el límite diario observado (si
            # RapidAPI lo reporta) antes de decidir qué hacer con el
            # status — en un 429 es justo esa respuesta la que trae el
            # header.
            log_remaining_quota(self.NAME, response)
            if not (self._direct and response.status_code == 429):
                break
            wait = _direct_retry_seconds(response)
            if wait is None or attempt == _DIRECT_429_RETRIES - 1:
                break
            logger.warning(
                "[ODDS:%s] 429 en %s — espero %.1fs (intento %d/%d)",
                self.NAME,
                path,
                wait,
                attempt + 1,
                _DIRECT_429_RETRIES,
            )
            await asyncio.sleep(wait + 0.1)
        if response is None:
            return 0, None
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

    async def participants(
        self, client: httpx.AsyncClient, sport_id: int
    ) -> Optional[dict[int, str]]:
        """Catálogo participantId -> nombre (`/v4/participants`).

        Respuesta `{"3409": "Chicago Bulls"}`. Billable pero casi
        inmutable: el llamador la cachea a disco por deporte.
        """
        status, data = await self._get(
            client, "/participants", params={"sportId": sport_id}
        )
        if status != 200 or not isinstance(data, dict):
            return None
        out: dict[int, str] = {}
        for raw_id, name in data.items():
            try:
                out[int(raw_id)] = str(name)
            except (TypeError, ValueError):
                continue
        return out or None

    async def fixtures_by_participant(
        self, client: httpx.AsyncClient, participant_id: int, sport_id: int
    ) -> Optional[list[OddsPapiFixture]]:
        """Todos los fixtures de un equipo (`participantId` basta solo,
        según la doc) — 1 llamada billable por equipo en vez de una por
        día calendario."""
        status, data = await self._get(
            client, "/fixtures", params={"participantId": participant_id}
        )
        if status != 200 or not isinstance(data, list):
            return None
        return [f for f in (parse_fixture(r, sport_id) for r in data) if f]

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
            params={"fixtureId": fixture_id, "bookmakers": _BOOKMAKERS_REQUEST},
        )
