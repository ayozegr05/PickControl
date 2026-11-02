"""Proveedor histórico de cuotas 5DollarFootballAPI (api.5dollarfootballapi.com).

La fuente GRATIS permanente (plan Free: 60 req/h, sin tarjeta) que
publica apertura + cierre REALES de bet365 para las cinco grandes
ligas europeas (LaLiga, EPL, Serie A, Bundesliga, Ligue 1): 1X2,
hándicap asiático, líneas de goles, córners, tarjetas, BTTS y
escalera fija de goles. Histórico de ~3 meses en Free (12 en Pro).

Verificado (2026-10): `/v1/fixtures` sin `league` devuelve TODAS las
ligas del plan en una llamada; `/v1/fixtures/{id}/odds` devuelve un
objeto `bookmakers[0].odds` con mercados `{opening, closing}`.

Nota de mercado: la línea de córners/tarjetas de bet365 es la línea
principal (suele 9-10.5 córners, 4-4.5 tarjetas) — NO la escalera
completa de 1xBet. Cuando la línea del tipster no casa, el payload
documenta qué línea existió de verdad (auditoría honesta, no None).

Timestamps: solo 1x2 trae `opening.opened_at` real; el resto de
mercados no expone cuándo abrió. El punto de apertura usa
`opened_at` si existe y si no `start - 1 día` como cota sintética
(los mercados principales siempre abren antes del partido): el
PRECIO es real, la hora es aproximada — documentado, no inventado.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.odds.oddspapi import HistoricalChoice, PricePoint
from app.services.results.base import count_provider_call, match_score

logger = get_logger("app.odds.fivedollar")

NAME = "fivedollar"
_PREFIX = "fivedollar:"
_BASE = "https://api.5dollarfootballapi.com/v1"
_DATA_DIR = Path(__file__).resolve().parents[4] / "fivedollar_data"
_MIN_SCORE = 0.75
# Presupuesto por pasada: 60 req/h del plan Free; un fixture cuesta
# 1 listado de día (cacheado en memoria) + 1 llamada de odds.
MAX_CALLS_PER_RUN = 50
# Cota sintética del precio de apertura cuando el mercado no trae
# `opened_at` (solo 1x2 la expone): el mercado principal siempre
# abre antes del partido — la hora exacta es desconocida, el precio no.
_OPENING_LAG = timedelta(days=1)


@dataclass
class FiveFixture:
    fixture_id: int
    home_name: str
    away_name: str
    start: Optional[datetime]
    league_name: str = ""


class FiveDollarClient:
    """Cliente mínimo de la API con conteo de llamadas por instancia."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self.calls = 0

    async def _get(self, http: httpx.AsyncClient, path: str, params: dict):
        try:
            response = await http.get(
                f"{_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("[ODDS:%s] Error de red: %s", NAME, exc)
            return None
        self.calls += 1
        count_provider_call(NAME)
        if response.status_code == 429:
            logger.warning("[ODDS:%s] 429 — límite de 60/h alcanzado", NAME)
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("[ODDS:%s] %s en %s", NAME, response.status_code, path)
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def fixtures(self, http: httpx.AsyncClient, day: date):
        """Todos los fixtures del plan ese día UTC (una llamada)."""
        start = int(
            datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
        )
        data = await self._get(
            http,
            "/fixtures",
            {
                "start_time": start,
                "end_time": start + 86340,  # la ventana máxima es 24 h
                "per_page": 100,
            },
        )
        if not isinstance(data, dict):
            return None
        out: list[FiveFixture] = []
        for raw in data.get("data") or []:
            teams = raw.get("teams") or {}
            home = (teams.get("home") or {}).get("name") or ""
            away = (teams.get("away") or {}).get("name") or ""
            kickoff = raw.get("kickoff_time") or raw.get("start_time")
            start_dt = None
            if isinstance(kickoff, (int, float)):
                start_dt = datetime.fromtimestamp(kickoff, tz=timezone.utc).replace(
                    tzinfo=None
                )
            elif isinstance(kickoff, str):
                try:
                    start_dt = datetime.fromisoformat(
                        kickoff.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except ValueError:
                    pass
            if not home or not away:
                continue
            out.append(
                FiveFixture(
                    fixture_id=int(raw.get("id") or 0),
                    home_name=home,
                    away_name=away,
                    start=start_dt,
                    league_name=(raw.get("league") or {}).get("name") or "",
                )
            )
        return out

    async def odds(self, http: httpx.AsyncClient, fixture_id: int):
        """Odds bet365 (opening/closing) del fixture; None si falla."""
        data = await self._get(http, f"/fixtures/{fixture_id}/odds", {})
        if not isinstance(data, dict):
            return None
        return data.get("data")


def event_ext_id(fixture: FiveFixture) -> str:
    return f"{_PREFIX}{fixture.fixture_id}"


def match_fixture(fixtures: list[FiveFixture], hint: str, dt) -> Optional[FiveFixture]:
    """El fixture cuyos nombres casan mejor con el hint ("A vs B")."""
    best, best_score = None, 0.0
    for fixture in fixtures:
        if dt is not None and fixture.start is not None:
            if abs((fixture.start - dt).total_seconds()) > 24 * 3600:
                continue
        score = match_score(hint, fixture.home_name, fixture.away_name)
        if score > best_score:
            best, best_score = fixture, score
    return best if best_score >= _MIN_SCORE else None


# --- Traducción bet365 -> vocabulario del comparador ---------------------


def _price(raw) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _opened_at(raw: Optional[dict], start: Optional[datetime]) -> Optional[datetime]:
    """Cuándo abrió el mercado: `opened_at` real si viene (solo 1x2
    la expone); si no, cota sintética `start - 1 día` — el precio es
    real, la hora aproximada (ver docstring del módulo)."""
    raw_opened = (raw or {}).get("opened_at")
    if isinstance(raw_opened, str):
        try:
            return datetime.fromisoformat(raw_opened.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            pass
    return start - _OPENING_LAG if start is not None else None


def _flat_choices(
    market_name: str,
    raw: Optional[dict],
    start: Optional[datetime],
    keys: list[tuple[str, str]],
) -> list[HistoricalChoice]:
    """Mercado `{opening:{k:precio...}, closing:{k:precio...}}` sin
    línea (1X2, BTTS): una opción por clave con apertura + cierre."""
    if not isinstance(raw, dict):
        return []
    opening, closing = raw.get("opening") or {}, raw.get("closing") or {}
    opened_at = _opened_at(opening, start)
    out: list[HistoricalChoice] = []
    for key, label in keys:
        points: list[PricePoint] = []
        open_price, close_price = _price(opening.get(key)), _price(closing.get(key))
        if opened_at is not None and open_price is not None:
            points.append(PricePoint(captured_at=opened_at, cuota=open_price))
        if start is not None and close_price is not None:
            points.append(PricePoint(captured_at=start, cuota=close_price))
        if points:
            out.append(
                HistoricalChoice(
                    market_name=market_name,
                    choice_name=label,
                    choice_group=None,
                    points=points,
                )
            )
    return out


def _line_choices(
    market_name: str,
    raw: Optional[dict],
    start: Optional[datetime],
    keys: list[tuple[str, str, str]],
) -> list[HistoricalChoice]:
    """Mercado de línea `{opening:{line,<opts>}, closing:{line,...}}`.

    La línea puede CAMBIAR entre apertura y cierre (corner_line
    9→9.5): cada fase es una línea distinta — se emite una opción por
    (opción, línea de fase) y se funden en una fila si coinciden.
    `keys` son (clave, etiqueta, signo-del-grupo): el hándicap lleva
    "+"/"-" y los totales "".
    """
    if not isinstance(raw, dict):
        return []
    opening, closing = raw.get("opening") or {}, raw.get("closing") or {}
    opened_at = _opened_at(opening, start)
    out: list[HistoricalChoice] = []
    for key, label, sign in keys:
        phases = (
            (opening.get("line"), _price(opening.get(key)), opened_at),
            (closing.get("line"), _price(closing.get(key)), start),
        )
        for line, price, when in phases:
            if line is None or price is None or when is None:
                continue
            group = f"{sign}{line}"
            existing = next(
                (c for c in out if c.choice_name == label and c.choice_group == group),
                None,
            )
            point = PricePoint(captured_at=when, cuota=price)
            if existing is not None:
                existing.points.append(point)
            else:
                out.append(
                    HistoricalChoice(
                        market_name=market_name,
                        choice_name=label,
                        choice_group=group,
                        points=[point],
                    )
                )
    return out


def translate_odds(payload: dict, fixture: FiveFixture) -> list[HistoricalChoice]:
    """Payload de `/fixtures/{id}/odds` -> HistoricalChoice del comparador.

    Solo bet365 (`bookmakers[0]` en el plan Free) y los mercados con
    equivalente en compare.py: "Full time", "Match goals",
    "Corners 2-Way", "Cards in match", "Both teams to score",
    "Asian handicap" (grupo local "+L": la API expone la magnitud de
    la ventaja del desfavorecido — el visitante lleva "-L"). Los
    `_half` y los hándicaps de córners/tarjetas quedan en el payload
    guardado para futuras traducciones sin nueva llamada.
    """
    bookmakers = (payload or {}).get("bookmakers") or []
    if not bookmakers:
        return []
    markets = bookmakers[0].get("odds") or {}
    start = fixture.start
    choices: list[HistoricalChoice] = []
    choices += _flat_choices(
        "Full time",
        markets.get("1x2"),
        start,
        [("home", "1"), ("draw", "X"), ("away", "2")],
    )
    choices += _flat_choices(
        "Both teams to score",
        markets.get("btts"),
        start,
        [("yes", "Yes"), ("no", "No")],
    )
    choices += _line_choices(
        "Match goals",
        markets.get("goal_line"),
        start,
        [("over", "Over", ""), ("under", "Under", "")],
    )
    # goal_line_fixed: escalera — cada elemento es {opening:{line,over,
    # under}, closing:{...}} con su propia línea fija.
    for rung in markets.get("goal_line_fixed") or []:
        if isinstance(rung, dict):
            choices += _line_choices(
                "Match goals",
                rung,
                start,
                [("over", "Over", ""), ("under", "Under", "")],
            )
    choices += _line_choices(
        "Corners 2-Way",
        markets.get("corner_line"),
        start,
        [("over", "Over", ""), ("under", "Under", "")],
    )
    choices += _line_choices(
        "Cards in match",
        markets.get("card_line"),
        start,
        [("over", "Over", ""), ("under", "Under", "")],
    )
    choices += _line_choices(
        "Asian handicap",
        markets.get("asian_handicap"),
        start,
        [("home", "1", "+"), ("away", "2", "-")],
    )
    return choices


# --- Persistencia del payload (auditoría / re-traducción offline) -------


def _payload_path(ext_id: str, data_dir: Optional[Path]) -> Path:
    base = data_dir or _DATA_DIR
    return base / f"{ext_id.replace(':', '_')}.json"


def save_payload(
    fixture: FiveFixture, ext_id: str, payload: dict, data_dir: Optional[Path]
) -> None:
    path = _payload_path(ext_id, data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "fixture_id": fixture.fixture_id,
                    "home_name": fixture.home_name,
                    "away_name": fixture.away_name,
                    "start": fixture.start.isoformat() if fixture.start else None,
                    "league_name": fixture.league_name,
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_payload(ext_id: str, data_dir: Optional[Path]) -> Optional[dict]:
    path = _payload_path(ext_id, data_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- Caché de listados de día -------------------------------------------
#
# El listado `/v1/fixtures` de un día YA JUGADO es inmutable — pagarlo
# en cada pasada del backfill (la caché en memoria muere con el
# proceso) quemaba ~200 llamadas diarias re-pidiendo los mismos días
# para picks que nunca casarán. Se persiste a disco y se paga una
# sola vez en la vida.


def _day_path(day: date, data_dir: Optional[Path]) -> Path:
    base = data_dir or _DATA_DIR
    return base / "days" / f"{day.isoformat()}.json"


def save_day(day: date, fixtures: list[FiveFixture], data_dir: Optional[Path]) -> None:
    path = _day_path(day, data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        "fixture_id": f.fixture_id,
                        "home_name": f.home_name,
                        "away_name": f.away_name,
                        "start": f.start.isoformat() if f.start else None,
                        "league_name": f.league_name,
                    }
                    for f in fixtures
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_day(day: date, data_dir: Optional[Path]) -> Optional[list[FiveFixture]]:
    path = _day_path(day, data_dir)
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out: list[FiveFixture] = []
    for raw in rows:
        start = None
        if raw.get("start"):
            try:
                start = datetime.fromisoformat(raw["start"])
            except ValueError:
                pass
        out.append(
            FiveFixture(
                fixture_id=int(raw.get("fixture_id") or 0),
                home_name=raw.get("home_name") or "",
                away_name=raw.get("away_name") or "",
                start=start,
                league_name=raw.get("league_name") or "",
            )
        )
    return out
