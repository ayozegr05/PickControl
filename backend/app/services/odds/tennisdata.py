"""tennis-data.co.uk: XLSX anuales GRATIS con cuotas de ganador ATP/WTA.

Un archivo por año y circuito (~350 KB) lista cada partido del tour
principal con sus cuotas pre-partido de Bet365 (`B365W`/`B365L`),
Pinnacle (`PSW`/`PSL`), mejor de mercado (`Max*`) y media (`Avg*`).
Sin key, sin cuota, sin rate-limit: es la fuente gratis del backfill
de tenis — drena los picks `mercado="ganador"` sin gastar ni una
llamada de las APIs de pago.

Solo cubre el tour principal (ATP + WTA): Challengers/ITF y mercados
de juegos/sets no están en el archivo — esos picks siguen a la
siguiente fuente de la cascada (marca `tennisdata|nofixture`).

Precio usado: Pinnacle si existe (cierre agudo, referencia estándar
de CLV), si no Bet365, si no media de mercado. La fila del CSV es un
único dato por partido — no hay apertura/cierre separados, así que la
"curva" que se guarda es un punto.

La ruta `/hrjk-.../` es el anti-leech que publica `alldata.php` — si
un día cambia, se actualiza la constante desde esa página.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import httpx

from app.services.odds.oddspapi import HistoricalChoice, PricePoint
from app.services.results.api_tennis import _pair_similar
from app.services.results.base import fold_name, split_team_hint

logger = logging.getLogger(__name__)

NAME = "tennisdata"

# El token de ruta lo publica https://www.tennis-data.co.uk/alldata.php
_BASE = "https://www.tennis-data.co.uk/hrjk-85HytOjkhth76j_ygh4jf7"
# El sitio rechaza requests sin UA de navegador (403/404 mudo).
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ControlPick/1.0)"}
_TIMEOUT = 30.0

_MIN_MATCH_SCORE = 0.6
# Serial de Excel -> fecha (el epoch de Lotus 1-2-3 que Excel heredó).
_EXCEL_EPOCH = date(1899, 12, 30)


@dataclass
class TennisMatch:
    """Una fila del XLSX: partido del tour con cuotas de ganador.

    `home`/`away` llevan la orientación winner/loser del archivo — las
    opciones "1"/"2" del mercado "Full time" siguen ese orden, igual
    que hace compare.py con los lados del evento."""

    day: date
    winner: str
    loser: str
    tournament: str
    circuit: str  # "atp" | "wta"
    winner_odds: dict[str, float]  # {"ps": .., "b365": .., "avg": .., "max": ..}
    loser_odds: dict[str, float]

    @property
    def home_name(self) -> str:
        return self.winner

    @property
    def away_name(self) -> str:
        return self.loser

    @property
    def start(self) -> datetime:
        # El archivo no trae hora: mediodía UTC del día del partido.
        return datetime.combine(self.day, datetime.min.time()).replace(hour=12)


def event_ext_id(row: TennisMatch) -> str:
    """Id estable por evento: fecha + pareja de jugadores."""
    w = fold_name(row.winner)
    lo = fold_name(row.loser)
    return f"{NAME}:{row.day.isoformat()}:{w}|{lo}"


def _as_date(value) -> Optional[date]:
    """La columna Date llega como datetime, serial de Excel o texto."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _EXCEL_EPOCH + timedelta(days=int(value))
    if isinstance(value, str):
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


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


def parse_xlsx(content: bytes, circuit: str) -> list[TennisMatch]:
    """Filas del XLSX anual (ATP o WTA según `circuit`).

    Las columnas se localizan por nombre de cabecera — el orden varía
    entre años (BFE* solo en algunos) y los formatos de fecha cambian.
    """
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = book.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = [str(c).strip() if c is not None else "" for c in next(rows)]
    col = {name: idx for idx, name in enumerate(header)}
    needed = ("Date", "Winner", "Loser")
    if any(name not in col for name in needed):
        return []

    def cell(row, name) -> object:
        idx = col.get(name)
        return row[idx] if idx is not None and idx < len(row) else None

    out: list[TennisMatch] = []
    for raw in rows:
        day = _as_date(cell(raw, "Date"))
        winner, loser = cell(raw, "Winner"), cell(raw, "Loser")
        if day is None or not winner or not loser:
            continue
        out.append(
            TennisMatch(
                day=day,
                winner=str(winner).strip(),
                loser=str(loser).strip(),
                tournament=str(cell(raw, "Tournament") or "").strip(),
                circuit=circuit,
                winner_odds={
                    "ps": _as_float(cell(raw, "PSW")),
                    "b365": _as_float(cell(raw, "B365W")),
                    "avg": _as_float(cell(raw, "AvgW")),
                    "max": _as_float(cell(raw, "MaxW")),
                },
                loser_odds={
                    "ps": _as_float(cell(raw, "PSL")),
                    "b365": _as_float(cell(raw, "B365L")),
                    "avg": _as_float(cell(raw, "AvgL")),
                    "max": _as_float(cell(raw, "MaxL")),
                },
            )
        )
    book.close()
    return out


async def fetch_year(http: httpx.AsyncClient, year: int) -> Optional[list[TennisMatch]]:
    """Los dos circuitos del año (ATP + WTA). None si la descarga
    falla (red/5xx — la pasada lo cuenta como error y reintenta);
    un año sin archivo publicado (404) da lista vacía, no error."""
    out: list[TennisMatch] = []
    for sub, circuit in ((f"{year}/{year}", "atp"), (f"{year}w/{year}", "wta")):
        url = f"{_BASE}/{sub}.xlsx"
        try:
            response = await http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.warning("[TENNISDATA] error de red en %s: %s", url, exc)
            return None
        if response.status_code == 404:
            continue
        if response.status_code != 200:
            logger.warning("[TENNISDATA] %s -> HTTP %s", url, response.status_code)
            return None
        try:
            out += parse_xlsx(response.content, circuit)
        except Exception as exc:  # archivo corrupto/formato nuevo
            logger.warning("[TENNISDATA] XLSX ilegible %s: %s", url, exc)
            return None
    return out


def match_row(rows: list[TennisMatch], hint: str) -> Optional[TennisMatch]:
    """El partido del pick entre las filas del día (±1 día ya aplicado
    por quien llama).

    `_pair_similar` entiende el formato "Apellido I." del archivo
    (token >=4 letras del hint dentro del nombre -> 0.8) y las parejas
    de dobles "A / B". Con dos partes se exige que cada una case con
    un lado distinto, en cualquier orientación (el archivo ordena
    ganador/perdedor, no local/visitante)."""
    parts = split_team_hint(hint)
    best: Optional[TennisMatch] = None
    best_score = 0.0
    for row in rows:
        if len(parts) == 1:
            score = max(
                _pair_similar(parts[0], row.winner),
                _pair_similar(parts[0], row.loser),
            )
        else:
            direct = min(
                _pair_similar(parts[0], row.winner),
                _pair_similar(parts[1], row.loser),
            )
            cross = min(
                _pair_similar(parts[0], row.loser),
                _pair_similar(parts[1], row.winner),
            )
            score = max(direct, cross)
        if score > best_score:
            best, best_score = row, score
    return best if best_score >= _MIN_MATCH_SCORE else None


def _best_price(odds: dict[str, float]) -> Optional[float]:
    """Pinnacle (cierre agudo) -> Bet365 -> media -> mejor de mercado."""
    for key in ("ps", "b365", "avg", "max"):
        if odds.get(key):
            return odds[key]
    return None


def row_choices(row: TennisMatch) -> list[HistoricalChoice]:
    """Mercado "Full time" con opciones "1"/"2" — el formato que
    `map_pick_choices` espera para `mercado="ganador"` de tenis.
    Vacío si el partido no trae cuota en ninguna casa."""
    choices: list[HistoricalChoice] = []
    for choice_name, odds in (("1", row.winner_odds), ("2", row.loser_odds)):
        price = _best_price(odds)
        if price is None:
            continue
        choices.append(
            HistoricalChoice(
                market_name="Full time",
                choice_name=choice_name,
                choice_group=None,
                points=[
                    PricePoint(captured_at=row.start, cuota=price, suspended=False)
                ],
            )
        )
    return choices
