"""Cliente de BetsAPI (api.b365api.com) — feed bet365 real para backfill.

A diferencia de OddsPapi (mirror capado que ni listaba Champions ni
ligas grandes), BetsAPI publica el listado completo de bet365: UCL,
LaLiga, Challengers/ITF de tenis, WNBA, liga argentina... Y su endpoint
de prematch devuelve el payload COMPLETO de mercados del evento aunque
ya se haya jugado (las cuotas quedan congeladas al entrar en vivo) —
justo el cierre que audita el comparador.

Rutas (verificadas 2026-10-21, trial "Bet365 API" 1.800 req/hora):

- `GET /v1/bet365/upcoming?sport_id&day=YYYYMMDD&page=N` -> eventos
  del día (50/página, `pager.total` da el tope). OJO: `per_page` se
  ignora — siempre pagina de 50 en 50, y el esoccer inunda las
  primeras páginas (los partidos reales quedan enterrados; hay que
  paginar hasta el final). Los eventos terminados siguen listados en
  su día — sirve de archivo histórico (verificado hasta ~7 semanas).
  `sport_id` del feed bet365: 1=fútbol, 13=tenis, 18=basket
  (¡2 es carreras de caballos, no tenis!).
- `GET /v4/bet365/prematch?FI=<id>` -> mercados del evento agrupados
  por secciones (`result`, `goals`, `corners`, `cards_fouls`,
  `asian_lines`, `scorers`, `games`, `main`, `set`, `half`,
  `game`, `quarter`, `team`...) cada una con `sp.<market_key>.odds`.
  Entradas de opción: `{odds, name, header, handicap, team}` — el
  significado de cada campo depende del mercado (ver `_emit_*`).

Traducción al vocabulario del comparador (`compare.py`): cada mercado
se emite con los nombres que `map_pick_choices` ya sabe leer ("Full
time", "Match goals", "Corners 2-Way", "1st set games"...). Mercados
sin equivalente (marcador exacto, especiales, "result&total"...) no se
traducen — mismo criterio de miss honesto que el resto de fuentes.

Nota honesta de líneas: se emiten las líneas que bet365 ofreció de
verdad. Si el tipster citó una línea que bet365 no vendió (p. ej.
córners 8.0 cuando la casa movía 9.5 con alternas solo hacia abajo),
el pick queda `nomarket` — la cuota auditada siempre es la de un
mercado real, nunca una interpolación.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.core.logging import get_logger
from app.services.odds.oddspapi import HistoricalChoice, PricePoint
from app.services.results.base import (
    is_rate_limited,
    log_remaining_quota,
    mark_rate_limited,
    match_score,
    translate_team_aliases,
)
from app.services.results.espn import _translate_countries

logger = get_logger("app.odds.betsapi")

NAME = "betsapi"
_BASE_URL = "https://api.b365api.com"
# Deporte canónico -> sport_id del feed bet365 (no el de Events API:
# ahí tenis es 2, aquí 2 son carreras de caballos).
SPORT_IDS = {"futbol": 1, "tenis": 13, "baloncesto": 18}
_MIN_MATCH_SCORE = 0.6
_DATE_TOLERANCE = timedelta(days=1)
_WIDE_DATE_TOLERANCE = timedelta(days=4)
_WIDE_MIN_SCORE = 0.75
# Muro de seguridad de paginación: 50 eventos/página, >1.300 eventos
# en un día grande (esoccer incluido) -> ~30 páginas. El tope evita
# quemar la hora si el pager miente.
_MAX_PAGES = 40
# Ligas que no son deporte real: el feed mezcla esoccer/virtuals en
# el mismo listado — nunca casan con un pick, saltarlos ahorra el
# 90% del volumen de páginas.
_SKIP_LEAGUE = re.compile(r"esoccer|e-soccer|virtual|simulated", re.I)


@dataclass
class BetsFixture:
    """Un evento del feed bet365 con lo necesario para casarlo con un pick."""

    fixture_id: str  # FI del feed — el que pide /prematch
    home_name: str
    away_name: str
    start: Optional[datetime]
    sport_id: int
    league_name: str = ""


def event_ext_id(fixture: BetsFixture) -> str:
    return f"betsapi:{fixture.fixture_id}"


def fixture_key(fixture: BetsFixture) -> str:
    return fixture.fixture_id


def _parse_event(raw: dict, sport_id: int) -> Optional[BetsFixture]:
    league = (raw.get("league") or {}).get("name") or ""
    if _SKIP_LEAGUE.search(league):
        return None
    home = (raw.get("home") or {}).get("name") or ""
    away = (raw.get("away") or {}).get("name") or ""
    fixture_id = raw.get("id")
    if not fixture_id or not home or not away:
        # Los eventos no-1v1 (carreras, torneos) traen solo `home`.
        return None
    start = None
    try:
        epoch = int(raw.get("time") or 0)
        if epoch:
            start = datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        start = None
    return BetsFixture(
        fixture_id=str(fixture_id),
        home_name=home,
        away_name=away,
        start=start,
        sport_id=sport_id,
        league_name=league,
    )


def match_fixture(
    fixtures: list[BetsFixture], hint: str, when: datetime
) -> Optional[BetsFixture]:
    """El fixture del pick en dos pasadas (misma lógica que oddspapi):

    1. Similitud de nombres dentro de ±1 día.
    2. Ventana ancha ±4 días con umbral alto + desempate por fecha —
       para picks cuya `fecha_evento` es la del repost.
    """
    best: Optional[BetsFixture] = None
    best_score = 0.0
    wide: list[tuple[BetsFixture, float, float]] = []
    hints = [hint]
    translated_hint = _translate_countries(hint)
    if translated_hint != hint:
        hints.append(translated_hint)
    for fixture in fixtures:
        if not fixture.start:
            continue
        distance = abs((fixture.start - when).total_seconds())
        # Se puntúa contra el nombre crudo Y contra el expandido por
        # aliases: el hint del pick puede venir en cualquiera de las
        # dos formas ("psg" ó "paris saint germain") e igualmente el
        # feed — con el máximo de ambas variantes casan los dos
        # sentidos. Además el hint viaja en español ("Países Bajos")
        # y el feed en inglés, así que se prueba también la
        # traducción de países.
        teams = [
            (fixture.home_name, fixture.away_name),
            (
                translate_team_aliases(fixture.home_name),
                translate_team_aliases(fixture.away_name),
            ),
        ]
        score = max(match_score(h, home, away) for h in hints for home, away in teams)
        if abs(fixture.start - when) <= _DATE_TOLERANCE:
            if score > best_score:
                best, best_score = fixture, score
        elif (
            distance <= _WIDE_DATE_TOLERANCE.total_seconds()
            and score >= _WIDE_MIN_SCORE
        ):
            wide.append((fixture, score, distance))
    if best is not None and best_score >= _MIN_MATCH_SCORE:
        return best
    if not wide:
        return None
    wide.sort(key=lambda t: (-t[1], t[2]))
    return wide[0][0]


# ---------------------------------------------------------------------------
# Traducción de mercados -> vocabulario de compare.py
# ---------------------------------------------------------------------------


def _fmt_line(value: float) -> str:
    return f"{value:g}"


def _num(raw: Any) -> Optional[float]:
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).strip().lstrip("+"))
    except ValueError:
        return None


def _cuota(raw: Any) -> Optional[float]:
    value = _num(raw)
    return value if value and value > 1.0 else None


_SIDE_1X2 = {"1": "1", "2": "2", "draw": "X", "x": "X", "tie": "X"}
_DC_NORM = {"1x": "1X", "x2": "X2", "2x": "X2", "12": "12"}
_OU_TEXT = re.compile(r"(over|under|o|u)\s*[:\-]?\s*([-+]?\d+(?:[.,]\d+)?)", re.I)


def _ou_split(raw: Any) -> tuple[Optional[str], Optional[float]]:
    """'Over 4.5' / 'O 162.5' / 'U 80.5' -> ('Over'|'Under', línea)."""
    m = _OU_TEXT.match(str(raw or "").strip())
    if not m:
        return None, None
    word = m.group(1).lower()
    side = "Over" if word.startswith("o") else "Under"
    return side, _num(m.group(2))


def _side_name(*fields: Any) -> Optional[str]:
    """Primer campo que vale '1'/'2' -> el lado; None si ninguno lo es.

    Según el mercado el lado viaja en `header` (tenis, equipo) o en
    `name` (basket game_lines, donde header dice "Spread"/"ML").
    """
    for field in fields:
        raw = str(field or "").strip()
        if raw in ("1", "2"):
            return raw
    return None


class _Emitter:
    """Acumula HistoricalChoice deduplicando (mercado, opción, línea):
    el mismo mercado aparece en varias secciones del payload."""

    def __init__(self, when: datetime) -> None:
        self._when = when
        self._seen: set[tuple[str, str, Optional[str]]] = set()
        self.choices: list[HistoricalChoice] = []

    def add(
        self,
        market: str,
        choice_name: str,
        choice_group: Optional[str],
        cuota: Optional[float],
    ) -> None:
        if cuota is None or not choice_name:
            return
        key = (market, choice_name, choice_group)
        if key in self._seen:
            return
        self._seen.add(key)
        self.choices.append(
            HistoricalChoice(
                market_name=market,
                choice_name=choice_name,
                choice_group=choice_group,
                points=[PricePoint(captured_at=self._when, cuota=cuota)],
            )
        )

    def emit_1x2(self, market: str, odds: list[dict]) -> None:
        for o in odds:
            side = _SIDE_1X2.get(str(o.get("name") or "").lower())
            self.add(market, side or "", None, _cuota(o.get("odds")))

    def emit_side(self, market: str, odds: list[dict], home: str, away: str) -> None:
        """Mercados 1/2 planos (DNB, ganador 2-way, money line)."""
        for o in odds:
            side = _side_name(o.get("header"), o.get("name"))
            if side is not None:
                self.add(market, side, None, _cuota(o.get("odds")))

    def emit_double_chance(self, odds: list[dict]) -> None:
        for o in odds:
            code = _DC_NORM.get(str(o.get("name") or "").lower().replace(" ", ""))
            self.add("Double chance", code or "", None, _cuota(o.get("odds")))

    def emit_yes_no(self, market: str, odds: list[dict]) -> None:
        for o in odds:
            name = str(o.get("name") or "").strip()
            if name in ("Yes", "No"):
                self.add(market, name, None, _cuota(o.get("odds")))

    def emit_totals(
        self, market: str, odds: list[dict], *, skip_exactly: bool = True
    ) -> None:
        """Over/Under con línea: name=línea+header, o handicap='Over X'."""
        for o in odds:
            header = str(o.get("header") or "").strip()
            if skip_exactly and header.lower() == "exactly":
                continue
            side, line = _ou_split(o.get("handicap"))
            if side is None:
                # Formato fútbol/tenis: name=línea, header=Over/Under
                header_low = header.lower()
                if header_low in ("over", "under"):
                    side = header_low.capitalize()
                    line = _num(o.get("name"))
            if side is None or line is None:
                continue
            self.add(market, side, _fmt_line(line), _cuota(o.get("odds")))

    def emit_spread(self, market: str, odds: list[dict], home: str, away: str) -> None:
        """Hándicap con lado: '(-2.5) Home' / '(+2.5) Away' + línea firmada."""
        for o in odds:
            line = _num(o.get("handicap"))
            if line is None:
                line = _num(o.get("name"))
            side = _side_name(o.get("header"), o.get("name"))
            if side is None or line is None:
                continue
            team = home if side == "1" else away
            self.add(
                market,
                f"({_fmt_line(line)}) {team}",
                _fmt_line(line),
                _cuota(o.get("odds")),
            )

    def emit_team_totals(
        self, market_tpl: str, odds: list[dict], home: str, away: str
    ) -> None:
        """Totales por equipo: header/team='1'|'2' + 'Over/Under X'."""
        for o in odds:
            side = _side_name(o.get("header"), o.get("team"))
            ou, line = _ou_split(o.get("handicap"))
            if side is None or ou is None or line is None:
                continue
            self.add(
                market_tpl.format(side), ou, _fmt_line(line), _cuota(o.get("odds"))
            )


# market_key (normalizado) -> handler, por deporte.
def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def translate_prematch(
    payload: dict, sport: str, home: str, away: str, when: Optional[datetime] = None
) -> list[HistoricalChoice]:
    """Payload de /v4/bet365/prematch -> opciones en vocabulario del
    comparador. Un punto de cierre por opción (las cuotas prematch
    quedan congeladas al entrar el partido en vivo)."""
    results = payload.get("results") or []
    if not results:
        return []
    event = results[0] if isinstance(results, list) else results
    if not isinstance(event, dict):
        return []
    emitter = _Emitter(when or datetime.now())

    def markets_of(section: Any) -> dict[str, dict]:
        if not isinstance(section, dict):
            return {}
        sp = section.get("sp")
        return sp if isinstance(sp, dict) else {}

    def emit(market_key: str, odds: list[dict]) -> None:
        key = _norm_key(market_key)
        # --- comunes ---
        if key in ("full_time_result", "to_win_match", "match_odds", "money_line"):
            emitter.emit_1x2("Full time", odds)
        elif key in ("double_chance",):
            emitter.emit_double_chance(odds)
        elif key in ("draw_no_bet",):
            emitter.emit_side("Draw no bet", odds, home, away)
        elif key in ("both_teams_to_score",):
            emitter.emit_yes_no("Both teams to score", odds)
        # --- fútbol: totales y spreads ---
        elif key in ("goals_over_under", "alternative_total_goals", "goal_line"):
            emitter.emit_totals("Match goals", odds)
        elif key == "team_total_goals":
            emitter.emit_team_totals("Team {} goals", odds, home, away)
        elif key in ("corners_2_way", "alternative_corners", "asian_total_corners"):
            emitter.emit_totals("Corners 2-Way", odds)
        elif key == "team_corners":
            emitter.emit_team_totals("Team {} corners", odds, home, away)
        elif key in ("number_of_cards_in_match", "asian_total_cards"):
            emitter.emit_totals("Cards in match", odds)
        elif key == "team_cards":
            emitter.emit_team_totals("Team {} cards", odds, home, away)
        elif key in ("asian_handicap", "alternative_asian_handicap"):
            emitter.emit_spread("Asian handicap", odds, home, away)
        elif key == "half_time_result":
            emitter.emit_1x2("1st half", odds)
        # --- tenis ---
        elif key in ("total_games_2_way",):
            emitter.emit_totals("Total games won", odds)
        elif key in ("match_handicap_games", "match_handicap"):
            emitter.emit_spread("Game handicap", odds, home, away)
        elif key in ("1st_set_winner",):
            emitter.emit_side("First set winner", odds, home, away)
        elif key in ("1st_set_total_games",):
            emitter.emit_totals("1st set games", odds)
        elif key in ("first_set_handicap", "1st_set_handicap"):
            emitter.emit_spread("Set handicap", odds, home, away)
        # --- basket ---
        elif key in ("game_lines", "alternative_game_lines"):
            # Mercado compuesto: header ML/Spread/Total en la misma lista.
            emitter.emit_side(
                "Full time",
                [
                    o
                    for o in odds
                    if str(o.get("header") or "").lower() in ("ml", "money line", "")
                ],
                home,
                away,
            )
            emitter.emit_spread(
                "Asian handicap",
                [o for o in odds if str(o.get("header") or "").lower() == "spread"],
                home,
                away,
            )
            emitter.emit_totals(
                "Match goals",
                [o for o in odds if str(o.get("header") or "").lower() == "total"],
            )
        elif key in ("team_totals", "alternative_team_totals"):
            emitter.emit_team_totals("Team {} goals", odds, home, away)
        elif key in ("spread", "alternative_spreads", "handicap", "alternative_total"):
            if "total" in key:
                emitter.emit_totals("Match goals", odds)
            else:
                emitter.emit_spread("Asian handicap", odds, home, away)
        elif key in ("total", "totals", "match_total", "game_total"):
            emitter.emit_totals("Match goals", odds)
        # --- props de jugador (fútbol) ---
        elif key in ("goalscorers", "anytime_goalscorer"):
            for o in odds:
                player = str(o.get("name") or "").strip()
                self_header = str(o.get("header") or "").lower()
                if not player or player in ("1", "2"):
                    continue
                market = "Goalscorer anytime"
                if "first" in self_header:
                    market = "First goalscorer"
                elif "last" in self_header:
                    market = "Last goalscorer"
                emitter.add(market, player, None, _cuota(o.get("odds")))
        elif key in ("score_or_assist", "to_score_or_assist"):
            for o in odds:
                player = str(o.get("name") or "").strip()
                if player and player not in ("1", "2"):
                    emitter.add(
                        "Goalscorer or assist", player, None, _cuota(o.get("odds"))
                    )

    for section_key, section in event.items():
        if section_key in ("FI", "event_id", "sport_id"):
            continue
        for market_key, market in markets_of(section).items():
            odds = market.get("odds") or []
            if odds:
                emit(market_key, odds)
    return emitter.choices


# ---------------------------------------------------------------------------
# Persistencia de payloads (re-traducción gratis tras mejoras del mapa)
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parents[3] / "betsapi_data"


def _safe_name(ext_id: str) -> str:
    return ext_id.replace(":", "_").replace("/", "_")


def payload_path(ext_id: str, base: Optional[Path] = None) -> Path:
    return (base or _DATA_DIR) / "payloads" / f"{_safe_name(ext_id)}.json"


def save_payload(
    fixture: BetsFixture,
    ext_id: str,
    payload: dict,
    base: Optional[Path] = None,
) -> None:
    path = payload_path(ext_id, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "ext_id": ext_id,
        "fixture_id": fixture.fixture_id,
        "home_name": fixture.home_name,
        "away_name": fixture.away_name,
        "start": fixture.start.isoformat() if fixture.start else None,
        "sport_id": fixture.sport_id,
        "fetched_at": datetime.now().isoformat(),
        "payload": payload,
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")


def load_payload(ext_id: str, base: Optional[Path] = None) -> Optional[dict]:
    path = payload_path(ext_id, base)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "payload" in data else None


# ---------------------------------------------------------------------------
# Cliente HTTP
# ---------------------------------------------------------------------------


class BetsApiClient:
    """Cliente HTTP de api.b365api.com con el manejo de cuota del proyecto."""

    def __init__(self, token: str) -> None:
        self.NAME = NAME
        self._token = token
        # Peticiones HTTP emitidas por esta instancia: el trial permite
        # 1.800 req/hora — el backfill las dosifica por pasada.
        self.calls = 0
        self._day_cache: dict[tuple[int, date], Optional[list[BetsFixture]]] = {}

    async def _get(
        self, client: httpx.AsyncClient, path: str, params: Optional[dict] = None
    ) -> tuple[int, Any]:
        """GET -> (status, json). 0 = red/cuota aparcada; el success:0
        de la API (PERMISSION_DENIED, cuota) también aparca el provider."""
        if is_rate_limited(self.NAME):
            return 0, None
        self.calls += 1
        try:
            response = await client.get(
                f"{_BASE_URL}{path}",
                params={**(params or {}), "token": self._token},
            )
        except httpx.HTTPError as exc:
            logger.warning("[ODDS:%s] Error de red en %s: %s", self.NAME, path, exc)
            return 0, None
        log_remaining_quota(self.NAME, response)
        if response.status_code in (403, 429):
            mark_rate_limited(self.NAME, response)
            logger.warning("[ODDS:%s] Cuota agotada; se omite hasta mañana", self.NAME)
            return response.status_code, None
        if response.status_code >= 400:
            return response.status_code, None
        try:
            data = response.json()
        except ValueError:
            return response.status_code, None
        if isinstance(data, dict) and data.get("success") == 0:
            error = str(data.get("error") or "")
            logger.warning("[ODDS:%s] API error en %s: %s", self.NAME, path, error)
            if "QUOTA" in error or "PERMISSION" in error or "LIMIT" in error:
                # Sin token válido o paquete agotado: aparcar el provider
                # para no quemar la pasada entera contra un muro fijo.
                mark_rate_limited(self.NAME, response)
            return response.status_code, None
        return response.status_code, data

    async def upcoming(
        self, client: httpx.AsyncClient, sport_id: int, day: date
    ) -> Optional[list[BetsFixture]]:
        """Todos los eventos reales de un día (paginado completo).

        El esoccer inunda las primeras páginas — hay que recorrer
        `pager.total` entero o se pierden los partidos grandes.
        """
        key = (sport_id, day)
        if key in self._day_cache:
            return self._day_cache[key]
        collected: list[BetsFixture] = []
        seen: set[str] = set()
        page = 1
        total = None
        while page <= _MAX_PAGES:
            status, data = await self._get(
                client,
                "/v1/bet365/upcoming",
                params={
                    "sport_id": sport_id,
                    "day": day.strftime("%Y%m%d"),
                    "page": page,
                },
            )
            if status != 200 or not isinstance(data, dict):
                self._day_cache[key] = None
                return None
            pager = data.get("pager") or {}
            total = int(pager.get("total") or 0)
            results = data.get("results") or []
            for raw in results:
                fixture = _parse_event(raw, sport_id)
                if fixture and fixture.fixture_id not in seen:
                    seen.add(fixture.fixture_id)
                    collected.append(fixture)
            if page * 50 >= total or not results:
                break
            page += 1
        self._day_cache[key] = collected
        return collected

    async def prematch(
        self, client: httpx.AsyncClient, fixture_id: str
    ) -> tuple[int, Optional[dict]]:
        """Mercados prematch completos del evento (congelados al inicio)."""
        status, data = await self._get(
            client, "/v4/bet365/prematch", params={"FI": fixture_id}
        )
        if status != 200 or not isinstance(data, dict):
            return status or 0, None
        results = data.get("results") or []
        if not results:
            # Fixture sin mercados publicados — miss definitivo (404 lógico).
            return 404, None
        return 200, data
