"""football-data.co.uk: CSVs GRATIS por liga/temporada con cuotas.

Las ligas principales (LaLiga, Segunda, Premier, Serie A, Bundesliga,
Ligue 1, Eredivisie, Portugal, Turquía, Grecia y las menores
inglesas/escocesas) viven en `mmz4281/{temporada}/{liga}.csv`: cada
fila trae el partido con cuotas de apertura Y cierre de Bet365,
Pinnacle, media y máximo de mercado en tres mercados — 1X2
("Full time"), over/under 2.5 ("Match goals") y hándicap asiático
("Asian handicap").

Las ligas "extra" (Argentina, Brasil, Japón, México, USA...) viven en
`/new/{pais}.csv`: UN archivo histórico por país con todas las
temporadas y solo el 1X2 de cierre — sin apertura, totales ni
hándicaps.

Sin key, sin cuota, sin rate-limit: es la fuente gratis del backfill
de fútbol, el gemelo de `tennisdata`. Además de las cuotas, su
listado completo de fixtures actúa de sondeo de fecha: casa un pick
cuya `fecha_evento` guardada es la del repost (a semanas del partido
real) con el fixture verdadero — el backfill corrige la fecha con la
de la fuente antes de que las APIs de pago busquen en el día
equivocado.

Precio elegido (cierre): Pinnacle -> Bet365 -> media -> máximo, como
en tennisdata. La apertura (columnas sin sufijo "C") se guarda como
segundo punto de la curva anclado a `inicio - 7 días`: fd no publica
cuándo se abrió el mercado, es convención documentada, no dato real.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import httpx

from app.services.odds.oddspapi import HistoricalChoice, PricePoint
from app.services.results.base import fold_name, match_score

logger = logging.getLogger(__name__)

NAME = "footballdata"

_BASE = "https://www.football-data.co.uk"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ControlPick/1.0)"}
_TIMEOUT = 30.0

# Ligas principales: un CSV por temporada en mmz4281/{temp}/{code}.csv
# con apertura+cierre de 1X2, O/U 2.5 y hándicap asiático.
_MAIN_LEAGUES = (
    "E0",
    "E1",
    "E2",
    "E3",
    "EC",
    "SC0",
    "SC1",
    "SC2",
    "SC3",
    "D1",
    "D2",
    "I1",
    "I2",
    "SP1",
    "SP2",
    "F1",
    "F2",
    "N1",
    "B1",
    "P1",
    "T1",
    "G1",
)
# Ligas "extra": un CSV histórico por país en /new/{code}.csv con
# todas las temporadas pero solo el 1X2 de cierre.
_EXTRA_LEAGUES = (
    "ARG",
    "AUT",
    "BRA",
    "CHN",
    "DNK",
    "FIN",
    "IRL",
    "JPN",
    "MEX",
    "NOR",
    "POL",
    "ROU",
    "RUS",
    "SWE",
    "SWZ",
    "USA",
)

_MIN_MATCH_SCORE = 0.6
# El listado de la temporada entera hace de sondeo de fecha, pero con
# tope: dos equipos se reencuentran cada ~4-5 meses en liga, así que
# mirar la temporada completa podría casar el pick con el partido de
# la próxima temporada. ±60 días cubre con margen el skew típico del
# repost (semanas) sin ese riesgo.
_MATCH_WINDOW = timedelta(days=60)
# Convención para anclar la cuota de apertura: fd no publica cuándo
# abrió el mercado — ver docstring del módulo.
_OPENING_ANCHOR = timedelta(days=7)


@dataclass
class FootballFixture:
    """Una fila del CSV: partido con cuotas de apertura y cierre.

    `home`/`away`/`start` dan la interfaz de fixture que usa
    `_run_source` (home_name/away_name/start)."""

    day: date
    start: datetime
    home: str
    away: str
    league: str
    # Precio ya resuelto por lado: {"1","X","2"}, {"Over","Under"} o
    # {"home","away"} del hándicap. None si la fila no trae ese mercado.
    win_open: dict[str, Optional[float]]
    win_close: dict[str, Optional[float]]
    ou25_open: dict[str, Optional[float]]
    ou25_close: dict[str, Optional[float]]
    ah_line: Optional[float]  # hándicap del local (AHCh / AHh)
    ah_open: dict[str, Optional[float]]
    ah_close: dict[str, Optional[float]]

    @property
    def home_name(self) -> str:
        return self.home

    @property
    def away_name(self) -> str:
        return self.away


def season_codes(day: date) -> list[str]:
    """Códigos de temporada candidatos para `day` ("2627"), con la
    anterior y la siguiente: la fecha guardada del pick puede ser la
    del repost y caer en otra temporada."""
    start_year = day.year if day.month >= 7 else day.year - 1
    return [
        f"{y % 100:02d}{(y + 1) % 100:02d}"
        for y in (start_year - 1, start_year, start_year + 1)
    ]


def event_ext_id(row: FootballFixture) -> str:
    """Id estable por evento: fecha real + pareja de equipos."""
    h = fold_name(row.home)
    a = fold_name(row.away)
    return f"{NAME}:{row.day.isoformat()}:{h}|{a}"


def _as_float(value) -> Optional[float]:
    if isinstance(value, (int, float)) and value > 1.0:
        return float(value)
    if isinstance(value, str):
        try:
            num = float(value.strip())
            return num if num > 1.0 else None
        except ValueError:
            return None
    return None


def _as_line(value) -> Optional[float]:
    """La línea de hándicap sí puede ser 0 o negativa (-1.5, 0.0)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _best(*cols) -> Optional[float]:
    """Pinnacle -> Bet365 -> media -> máximo (misma prioridad que
    tennisdata: cierre agudo primero)."""
    for value in cols:
        price = _as_float(value)
        if price is not None:
            return price
    return None


def _parse_start(day: Optional[date], time_raw) -> Optional[datetime]:
    if day is None:
        return None
    try:
        hour, minute = str(time_raw or "").split(":")
        return datetime.combine(day, datetime.min.time()).replace(
            hour=int(hour), minute=int(minute)
        )
    except (ValueError, TypeError):
        # Sin hora fiable: mediodía UTC del día, como tennisdata.
        return datetime.combine(day, datetime.min.time()).replace(hour=12)


def _parse_day(value) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_csv(content: str, league: str) -> list[FootballFixture]:
    """Filas de un CSV de fd (principal o extra — las columnas se
    buscan por nombre; las que una liga no trae quedan en None)."""
    rows = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    out: list[FootballFixture] = []
    for row in rows:
        day = _parse_day(row.get("Date"))
        home = (row.get("HomeTeam") or row.get("Home") or "").strip()
        away = (row.get("AwayTeam") or row.get("Away") or "").strip()
        start = _parse_start(day, row.get("Time"))
        if day is None or start is None or not home or not away:
            continue
        out.append(
            FootballFixture(
                day=day,
                start=start,
                home=home,
                away=away,
                league=league,
                win_open={
                    "1": _best(
                        row.get("B365H"),
                        row.get("PSH"),
                        row.get("AvgH"),
                        row.get("MaxH"),
                    ),
                    "X": _best(
                        row.get("B365D"),
                        row.get("PSD"),
                        row.get("AvgD"),
                        row.get("MaxD"),
                    ),
                    "2": _best(
                        row.get("B365A"),
                        row.get("PSA"),
                        row.get("AvgA"),
                        row.get("MaxA"),
                    ),
                },
                win_close={
                    "1": _best(
                        row.get("PSCH"),
                        row.get("B365CH"),
                        row.get("AvgCH"),
                        row.get("MaxCH"),
                    ),
                    "X": _best(
                        row.get("PSCD"),
                        row.get("B365CD"),
                        row.get("AvgCD"),
                        row.get("MaxCD"),
                    ),
                    "2": _best(
                        row.get("PSCA"),
                        row.get("B365CA"),
                        row.get("AvgCA"),
                        row.get("MaxCA"),
                    ),
                },
                ou25_open={
                    "Over": _best(
                        row.get("B365>2.5"),
                        row.get("P>2.5"),
                        row.get("Avg>2.5"),
                        row.get("Max>2.5"),
                    ),
                    "Under": _best(
                        row.get("B365<2.5"),
                        row.get("P<2.5"),
                        row.get("Avg<2.5"),
                        row.get("Max<2.5"),
                    ),
                },
                ou25_close={
                    "Over": _best(
                        row.get("PC>2.5"),
                        row.get("B365C>2.5"),
                        row.get("AvgC>2.5"),
                        row.get("MaxC>2.5"),
                    ),
                    "Under": _best(
                        row.get("PC<2.5"),
                        row.get("B365C<2.5"),
                        row.get("AvgC<2.5"),
                        row.get("MaxC<2.5"),
                    ),
                },
                ah_line=_as_line(row.get("AHCh")) or _as_line(row.get("AHh")),
                ah_open={
                    "home": _best(
                        row.get("B365AHH"),
                        row.get("PAHH"),
                        row.get("AvgAHH"),
                        row.get("MaxAHH"),
                    ),
                    "away": _best(
                        row.get("B365AHA"),
                        row.get("PAHA"),
                        row.get("AvgAHA"),
                        row.get("MaxAHA"),
                    ),
                },
                ah_close={
                    "home": _best(
                        row.get("PCAHH"),
                        row.get("B365CAHH"),
                        row.get("AvgCAHH"),
                        row.get("MaxCAHH"),
                    ),
                    "away": _best(
                        row.get("PCAHA"),
                        row.get("B365CAHA"),
                        row.get("AvgCAHA"),
                        row.get("MaxCAHA"),
                    ),
                },
            )
        )
    return out


async def _fetch_csv(http: httpx.AsyncClient, url: str) -> Optional[str]:
    """Texto del CSV. None si la descarga falla (red/5xx — la pasada
    lo cuenta como error y reintenta); un 404 da "" (liga sin archivo
    esa temporada: vacío, no error)."""
    try:
        response = await http.get(
            url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        logger.warning("[FOOTBALLDATA] error de red en %s: %s", url, exc)
        return None
    if response.status_code == 404:
        return ""
    if response.status_code != 200:
        logger.warning("[FOOTBALLDATA] %s -> HTTP %s", url, response.status_code)
        return None
    return response.text


async def fetch_season(
    http: httpx.AsyncClient, season: str
) -> Optional[list[FootballFixture]]:
    """Las ligas principales de una temporada ("2627").

    None si una descarga falla (red/5xx); una liga sin archivo (404)
    simplemente no aporta filas."""
    out: list[FootballFixture] = []
    for code in _MAIN_LEAGUES:
        text = await _fetch_csv(http, f"{_BASE}/mmz4281/{season}/{code}.csv")
        if text is None:
            return None
        try:
            out += parse_csv(text, code)
        except Exception as exc:  # formato nuevo/corrupto
            logger.warning("[FOOTBALLDATA] CSV ilegible %s/%s: %s", season, code, exc)
            return None
    return out


async def fetch_extra(http: httpx.AsyncClient) -> Optional[list[FootballFixture]]:
    """Las ligas "extra" de `/new/` — histórico completo por país,
    solo 1X2 de cierre. Las filas de todas las temporadas se devuelven
    enteras; la ventana de fechas de `match_row` es la que acota."""
    out: list[FootballFixture] = []
    for code in _EXTRA_LEAGUES:
        text = await _fetch_csv(http, f"{_BASE}/new/{code}.csv")
        if text is None:
            return None
        try:
            out += parse_csv(text, code)
        except Exception as exc:
            logger.warning("[FOOTBALLDATA] CSV ilegible new/%s: %s", code, exc)
            return None
    return out


def match_row(
    rows: list[FootballFixture], hint: str, dt: Optional[datetime]
) -> Optional[FootballFixture]:
    """El fixture del pick entre las filas dadas.

    Ventana ±60 días alrededor de `dt` (la fecha del pick): la fecha
    guardada puede ser la del repost, así que no se exige el día
    exacto — pero el tope evita casar con el reencuentro de la
    temporada siguiente. Entre filas con la mejor nota gana la más
    cercana a `dt` (el repost sesga hacia atrás)."""
    day = dt.date() if dt else None
    best: Optional[FootballFixture] = None
    best_score = 0.0
    best_dist = _MATCH_WINDOW + timedelta(days=1)
    for row in rows:
        if day is not None:
            dist = abs(row.day - day)
            if dist > _MATCH_WINDOW:
                continue
        else:
            dist = timedelta(0)
        score = match_score(hint, row.home, row.away)
        if score < best_score:
            continue
        if score > best_score or dist < best_dist:
            best, best_score, best_dist = row, score, dist
    return best if best_score >= _MIN_MATCH_SCORE else None


def _fmt_line(value: float) -> str:
    """Línea como la escribe Sofascore en `choice_group` ("2.5")."""
    return f"{value:g}"


def _points(
    open_price: Optional[float], close_price: Optional[float], start: datetime
) -> list[PricePoint]:
    """Curva de dos puntos: apertura (anclada a inicio -7d, convención)
    y cierre (al inicio del partido). Solo se emiten precios que la
    fila realmente traía."""
    points: list[PricePoint] = []
    if open_price is not None:
        points.append(
            PricePoint(
                captured_at=start - _OPENING_ANCHOR,
                cuota=open_price,
                suspended=False,
            )
        )
    if close_price is not None:
        points.append(PricePoint(captured_at=start, cuota=close_price, suspended=False))
    return points


def row_choices(row: FootballFixture) -> list[HistoricalChoice]:
    """Mercados del CSV traducidos al vocabulario de `compare.py`:

    - "Full time": "1"/"X"/"2".
    - "Match goals": "Over"/"Under" con la línea "2.5" en
      `choice_group` (es la única línea que publica fd).
    - "Asian handicap": "(linea) Local" / "(linea opuesta) Visitante"
      con la línea en `choice_group` — la convención de oddspapi.

    Vacío si la fila no trae cuota en ninguna casa."""
    choices: list[HistoricalChoice] = []
    for choice_name in ("1", "X", "2"):
        points = _points(
            row.win_open.get(choice_name), row.win_close.get(choice_name), row.start
        )
        if points:
            choices.append(
                HistoricalChoice(
                    market_name="Full time",
                    choice_name=choice_name,
                    choice_group=None,
                    points=points,
                )
            )
    for choice_name in ("Over", "Under"):
        points = _points(
            row.ou25_open.get(choice_name),
            row.ou25_close.get(choice_name),
            row.start,
        )
        if points:
            choices.append(
                HistoricalChoice(
                    market_name="Match goals",
                    choice_name=choice_name,
                    choice_group="2.5",
                    points=points,
                )
            )
    if row.ah_line is not None:
        home_price = _points(row.ah_open["home"], row.ah_close["home"], row.start)
        away_price = _points(row.ah_open["away"], row.ah_close["away"], row.start)
        if home_price:
            choices.append(
                HistoricalChoice(
                    market_name="Asian handicap",
                    choice_name=f"({_fmt_line(row.ah_line)}) {row.home}",
                    choice_group=_fmt_line(row.ah_line),
                    points=home_price,
                )
            )
        if away_price:
            choices.append(
                HistoricalChoice(
                    market_name="Asian handicap",
                    choice_name=f"({_fmt_line(-row.ah_line)}) {row.away}",
                    choice_group=_fmt_line(-row.ah_line),
                    points=away_price,
                )
            )
    return choices
