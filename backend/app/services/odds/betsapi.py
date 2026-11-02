"""Cliente de BetsAPI (api.b365api.com) — feed bet365 real para backfill.

A diferencia de OddsPapi (mirror capado que ni listaba Champions ni
ligas grandes), BetsAPI publica el listado completo de bet365: UCL,
LaLiga, Challengers/ITF de tenis, WNBA, liga argentina... Y su endpoint
de prematch devuelve el payload COMPLETO de mercados del evento aunque
ya se haya jugado (las cuotas quedan congeladas al entrar en vivo) —
justo el cierre que audita el comparador.

Rutas — dos transportes sobre el mismo feed bet365:

- **Directo** `api.b365api.com` (token=..., trial/paquete de pago —
  el que SÍ sirve archivo histórico de prematch):
  - `GET /v1/bet365/upcoming?sport_id&day=YYYYMMDD&page=N` -> eventos
    del día (50/página, `pager.total` da el tope). OJO: `per_page` se
    ignora — siempre pagina de 50 en 50, y el esoccer inunda las
    primeras páginas (los partidos reales quedan enterrados; hay que
    paginar hasta el final). Los eventos terminados siguen listados en
    su día — sirve de archivo histórico (verificado hasta ~7 semanas).
  - `GET /v4/bet365/prematch?FI=<id>` -> mercados del evento.
- **RapidAPI** `betsapi2.p.rapidapi.com` (headers X-RapidAPI-Key/Host,
  plan gratis ~24 req/día — verificado 2026-10-27): mismas rutas pero
  bajo `/v3` (`/v3/bet365/upcoming`, `/v3/bet365/prematch`). El payload
  es IDÉNTICO en estructura al v4, pero el prematch solo se sirve
  mientras el partido no ha empezado: un evento terminado devuelve el
  stub `{FI, event_id, sport_id}` sin mercados (verificado con un
  partido Premier). Sirve para CONGELAR cuotas de picks nuevos al
  publicarse (snapshotter), no para el backlog.

`sport_id` del feed bet365: 1=fútbol, 13=tenis, 18=basket
(¡2 es carreras de caballos, no tenis!).

El prematch devuelve mercados agrupados por secciones (`result`,
`goals`, `corners`, `cards_fouls`, `asian_lines`, `scorers`, `games`,
`main`, `set`, `half`, `game`, `quarter`, `team`...) cada una con
`sp.<market_key>.odds` — salvo `others`, que es una LISTA de
secciones `{sp: ...}` (doble oportunidad, totales de equipo,
goleadores por equipo...). Entradas de opción:
`{odds, name, header, handicap, team, name2}` — el significado de
cada campo depende del mercado (ver `_emit_*`).

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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.core.logging import get_logger
from app.services.odds.base import EventRef, MarketChoice
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
# Provider del snapshotter vía wrapper RapidAPI: nombre propio para
# no compartir el estado de cuota con el cliente directo de pago.
NAME_RAPID = "betsapi_rapid"
_BASE_URL = "https://api.b365api.com"
_RAPIDAPI_HOST = "betsapi2.p.rapidapi.com"
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
# Paginación acotada del plan gratis (~24 req/día): un barrido
# completo de un día grande costaría más que la cuota entera. Con
# parada temprana al casar el partido, 8 páginas (400 eventos)
# suelen bastar — el resto queda como miss provisional.
_FREE_MAX_PAGES = 8
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
    for candidate in fields:
        raw = str(candidate or "").strip()
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

    def emit_double_chance(
        self, odds: list[dict], home: str = "", away: str = ""
    ) -> None:
        """Doble oportunidad: códigos "1X"/"X2"/"12" o nombres
        "Sevilla or Draw" / "Draw or Osasuna" / "Sevilla or Osasuna"
        (el orden del nombre varía — el equipo NO-draw decide el lado)."""
        for o in odds:
            name = str(o.get("name") or "")
            code = _DC_NORM.get(name.lower().replace(" ", ""))
            if code is None:
                parts = [p.strip() for p in re.split(r"\s+or\s+", name, flags=re.I)]
                if len(parts) == 2:
                    low = [p.lower() for p in parts]
                    if "draw" in low:
                        other = parts[1 - low.index("draw")]
                        home_score = match_score(other, home, home)
                        away_score = match_score(other, away, away)
                        code = "1X" if home_score >= away_score else "X2"
                    else:
                        code = "12"
            self.add("Double chance", code or "", None, _cuota(o.get("odds")))

    def emit_yes_no(self, market: str, odds: list[dict]) -> None:
        for o in odds:
            name = str(o.get("name") or "").strip()
            if name in ("Yes", "No"):
                self.add(market, name, None, _cuota(o.get("odds")))

    def emit_totals(self, market: str, odds: list[dict]) -> None:
        """Over/Under con línea: name=línea+header, o handicap='Over X'.

        Las filas 'Exactly' (líneas enteras 3-way) también se emiten
        como evidencia del resultado exacto — su choice_name nunca
        casa con un pick Over/Under.
        """
        for o in odds:
            header = str(o.get("header") or "").strip()
            header_low = header.lower()
            if header_low == "exactly":
                line = _num(o.get("name"))
                if line is not None:
                    self.add(market, "Exactly", _fmt_line(line), _cuota(o.get("odds")))
                continue
            side, line = _ou_split(o.get("handicap"))
            if side is None and header_low in ("over", "under"):
                # Formato fútbol/tenis: name=línea, header=Over/Under
                side = header_low.capitalize()
                line = _num(o.get("name"))
            if side is None or line is None:
                continue
            self.add(market, side, _fmt_line(line), _cuota(o.get("odds")))

    def emit_totals_flex(
        self, market_2w: str, market_3w: str, odds: list[dict]
    ) -> None:
        """Totales con nombre según contenido: si el mercado trae
        opción 'Exactly' es el 3-way de líneas enteras del tipster
        ("Más de 8 | Exactamente 8 | Menos de 8"); si no, el 2-way de
        medias líneas. Así `corners`/`alternative_corners` caen en
        "Corners 3-Way" y `corners_2_way` en "Corners 2-Way"."""
        market = (
            market_3w
            if any(
                str(o.get("header") or "").strip().lower() == "exactly" for o in odds
            )
            else market_2w
        )
        self.emit_totals(market, odds)

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

    def emit_score_or_assist(self, odds: list[dict]) -> None:
        """'Score or Assist' del feed: un precio por jugador y tipo
        (header 'Score'/'Assist'/'Score or Assist'). En la sección
        `main` la fila de precio NO trae `name` — el jugador va en
        filas 'PC<id>' con header 'Player/Last 5' que comparten el id
        numérico; en `scorers` el nombre viaja en la propia fila."""
        names = {
            str(o.get("id") or "")[2:]: str(o.get("name") or "").strip()
            for o in odds
            if str(o.get("id") or "").startswith("PC")
            and str(o.get("name") or "").strip()
        }
        for o in odds:
            market = {
                "score": "Goalscorer anytime",
                "assist": "Player assists",
                "score or assist": "Goalscorer or assist",
            }.get(str(o.get("header") or "").strip().lower())
            if market is None:
                continue
            player = str(o.get("name") or "").strip() or names.get(
                str(o.get("id") or ""), ""
            )
            if not player or player in ("1", "2"):
                continue
            self.add(market, player, None, _cuota(o.get("odds")))

    def emit_scorers(self, odds: list[dict], *, team_scoped: bool = False) -> None:
        """Goleadores: name=jugador, header=First/Last/Anytime (o
        'N or More' en multi_scorers). `team_scoped` marca el mercado
        'Team Goalscorer' — su 'First' es el primer goleador DE SU
        EQUIPO, no del partido: se guarda con su propio nombre en vez
        de colarse en 'First goalscorer'."""
        for o in odds:
            player = str(o.get("name") or "").strip()
            if not player or player in ("1", "2"):
                continue
            header = str(o.get("header") or "").strip()
            low = header.lower()
            cuota = _cuota(o.get("odds"))
            if "or more" in low or re.search(r"\d\s*\+", low):
                self.add("Multi scorers", player, header or None, cuota)
            elif team_scoped and low in ("first", "last"):
                self.add("Team goalscorer", player, header, cuota)
            elif "first" in low:
                self.add("First goalscorer", player, None, cuota)
            elif "last" in low:
                self.add("Last goalscorer", player, None, cuota)
            else:
                self.add("Goalscorer anytime", player, None, cuota)

    def emit_player_lines(self, market: str, odds: list[dict]) -> None:
        """Props de jugador por umbral ('Shots', 'Shots On Target'):
        header 'N+' con name=jugador. '1+' equivale a Over 0.5 — el
        comparador lee la línea como `choice_group` 'Over X.5'."""
        for o in odds:
            player = str(o.get("name") or "").strip()
            m = re.match(r"(\d+)\s*\+", str(o.get("header") or "").strip())
            if not player or player in ("1", "2") or not m:
                continue
            line = int(m.group(1)) - 0.5
            self.add(market, player, f"Over {line:g}", _cuota(o.get("odds")))

    def emit_named_1x2(self, market: str, odds: list[dict]) -> None:
        """1X2 con opción de 'nadie' tipo 'No Goals' -> 'X'
        (First Team to Score)."""
        for o in odds:
            name = str(o.get("name") or "").strip()
            code = _SIDE_1X2.get(name.lower())
            if code is None and name.lower() in ("no goals", "no goal", "none"):
                code = "X"
            self.add(market, code or "", None, _cuota(o.get("odds")))


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
            emitter.emit_double_chance(odds, home, away)
        elif key in ("draw_no_bet",):
            emitter.emit_side("Draw no bet", odds, home, away)
        elif key in ("both_teams_to_score",):
            emitter.emit_yes_no("Both teams to score", odds)
        elif key == "first_team_to_score":
            emitter.emit_named_1x2("First team to score", odds)
        elif key == "correct_score":
            for o in odds:
                score_name = str(o.get("name") or "").strip()
                emitter.add("Correct score", score_name, None, _cuota(o.get("odds")))
        # --- fútbol: totales y spreads ---
        elif key in ("goals_over_under", "alternative_total_goals", "goal_line"):
            emitter.emit_totals("Match goals", odds)
        elif key == "team_total_goals":
            emitter.emit_team_totals("Team {} goals", odds, home, away)
        elif key in ("corners", "alternative_corners", "corners_3_way"):
            # Totales de córners a 3 vías (Over/Exactly/Under en líneas
            # enteras): EL mercado del tipster. Si el mercado llega sin
            # fila Exactly (suspensa) cae a su equivalente 2-way.
            emitter.emit_totals_flex("Corners 2-Way", "Corners 3-Way", odds)
        elif key in ("corners_2_way", "asian_total_corners"):
            emitter.emit_totals("Corners 2-Way", odds)
        elif key == "team_corners":
            emitter.emit_team_totals("Team {} corners", odds, home, away)
        elif key in ("cards", "alternative_cards", "number_of_cards_in_match"):
            emitter.emit_totals_flex("Cards in match", "Cards 3-Way", odds)
        elif key == "asian_total_cards":
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
            emitter.emit_scorers(odds)
        elif key == "team_goalscorer":
            emitter.emit_scorers(odds, team_scoped=True)
        elif key == "multi_scorers":
            emitter.emit_scorers(odds)
        elif key in ("score_or_assist", "to_score_or_assist"):
            emitter.emit_score_or_assist(odds)
        elif key in ("shots_on_target", "player_shots_on_target"):
            emitter.emit_player_lines("Player shots on target", odds)
        elif key in ("shots", "player_shots"):
            emitter.emit_player_lines("Player shots", odds)

    for section_key, section in event.items():
        if section_key in ("FI", "event_id", "sport_id"):
            continue
        # `others` llega como LISTA de secciones {sp: {...}} (doble
        # oportunidad, totales de equipo, goleadores por equipo...) —
        # sin el aplanado se ignoraba entera.
        sections = section if isinstance(section, list) else [section]
        for sec in sections:
            for market_key, market in markets_of(sec).items():
                # El feed no es uniforme: casi todo llega {name, odds}
                # pero hay secciones cuyo valor es la lista de odds
                # directamente — sin el isinstance la pasada moría
                # con AttributeError en ese evento.
                if isinstance(market, dict):
                    odds = market.get("odds") or []
                elif isinstance(market, list):
                    odds = [o for o in market if isinstance(o, dict)]
                else:
                    continue
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


@dataclass
class _DayBoard:
    """Progreso del barrido `/upcoming` de un (sport_id, día).

    El plan gratis (~24 req/día) no da para re-paginar el board
    completo por cada pick: se guardan las páginas ya leídas y cada
    `upcoming()` reanuda donde quedó. Un día cuesta como mucho
    `_max_pages` llamadas EN TOTAL entre todos sus picks — los misses
    sobre un board `complete` son gratis (0 llamadas).
    """

    fixtures: list[BetsFixture] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    page_done: int = 0
    complete: bool = False
    dead: bool = False


class BetsApiClient:
    """Cliente HTTP del feed bet365 con el manejo de cuota del proyecto.

    Dos transportes (mismo feed, distinto acceso):
    - `token=` -> api.b365api.com directo (trial/paquete de pago; sirve
      prematch histórico de partidos terminados).
    - `rapidapi_key` -> wrapper betsapi2.p.rapidapi.com (plan gratis
      ~24 req/día; el prematch solo vive hasta que empieza el partido).
    """

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        rapidapi_key: Optional[str] = None,
        rapidapi_host: str = _RAPIDAPI_HOST,
        name: str = NAME,
    ) -> None:
        self.NAME = name
        self._token = token
        self._rapidapi = rapidapi_key is not None
        if self._rapidapi:
            self._base_url = f"https://{rapidapi_host}"
            self._headers = {
                "X-RapidAPI-Key": rapidapi_key or "",
                "X-RapidAPI-Host": rapidapi_host,
            }
            self._upcoming_path = "/v3/bet365/upcoming"
            self._prematch_path = "/v3/bet365/prematch"
            self._max_pages = _FREE_MAX_PAGES
        else:
            self._base_url = _BASE_URL
            self._headers = {}
            self._upcoming_path = "/v1/bet365/upcoming"
            self._prematch_path = "/v4/bet365/prematch"
            self._max_pages = _MAX_PAGES
        # Peticiones HTTP emitidas por esta instancia: el trial permite
        # 1.800 req/hora — el backfill las dosifica por pasada.
        self.calls = 0
        self._day_cache: dict[tuple[int, date], _DayBoard] = {}

    async def _get(
        self, client: httpx.AsyncClient, path: str, params: Optional[dict] = None
    ) -> tuple[int, Any]:
        """GET -> (status, json). 0 = red/cuota aparcada; el success:0
        de la API (PERMISSION_DENIED, cuota) también aparca el provider."""
        if is_rate_limited(self.NAME):
            return 0, None
        self.calls += 1
        request_params = dict(params or {})
        if not self._rapidapi:
            request_params["token"] = self._token
        try:
            response = await client.get(
                f"{self._base_url}{path}",
                params=request_params,
                headers=self._headers,
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
        self,
        client: httpx.AsyncClient,
        sport_id: int,
        day: date,
        *,
        hint: Optional[str] = None,
        when: Optional[datetime] = None,
    ) -> Optional[list[BetsFixture]]:
        """Eventos reales de un día (paginado y reanudable).

        El esoccer inunda las primeras páginas — hay que recorrer
        `pager.total` entero o se pierden los partidos grandes. El
        progreso vive en `_DayBoard`: las páginas ya leídas se
        comparten entre todos los picks del día y cada llamada
        reanuda en `page_done + 1`. Si el partido ya aparece en lo
        acumulado, la búsqueda cuesta CERO llamadas. Con `hint`/`when`
        hay parada temprana al casar; el board se queda a medias y el
        siguiente pick continúa (antes se re-paginaba desde la 1 —
        así se fundía la cuota diaria en misses repetidos).
        """
        key = (sport_id, day)
        board = self._day_cache.get(key)
        if board is None:
            board = _DayBoard()
            self._day_cache[key] = board
        if board.dead:
            return None
        if board.complete:
            return board.fixtures
        if (
            hint is not None
            and when is not None
            and board.fixtures
            and match_fixture(board.fixtures, hint, when) is not None
        ):
            # El partido ya cayó en una página leída por otro pick —
            # match gratis sin gastar ni una llamada más.
            return board.fixtures

        page = board.page_done + 1
        while page <= self._max_pages:
            status, data = await self._get(
                client,
                self._upcoming_path,
                params={
                    "sport_id": sport_id,
                    "day": day.strftime("%Y%m%d"),
                    "page": page,
                },
            )
            if status != 200 or not isinstance(data, dict):
                # Fallo (cuota/red): conserva el progreso leído para
                # reintentar en otro ciclo si el provider revive.
                board.dead = True
                return None
            board.page_done = page
            pager = data.get("pager") or {}
            total = int(pager.get("total") or 0)
            results = data.get("results") or []
            for raw in results:
                fixture = _parse_event(raw, sport_id)
                if fixture and fixture.fixture_id not in board.seen:
                    board.seen.add(fixture.fixture_id)
                    board.fixtures.append(fixture)
            if page * 50 >= total or not results:
                board.complete = True
                return board.fixtures
            if hint is not None and when is not None and board.fixtures:
                if match_fixture(board.fixtures, hint, when) is not None:
                    return board.fixtures  # parada temprana, progreso guardado
            page += 1
        board.complete = True
        return board.fixtures

    async def prematch(
        self, client: httpx.AsyncClient, fixture_id: str
    ) -> tuple[int, Optional[dict]]:
        """Mercados prematch completos del evento (congelados al inicio).

        En el wrapper gratis un partido ya empezado devuelve solo el
        stub `{FI, event_id, sport_id}` sin secciones — `results`
        no está vacío pero no hay mercados, así que se detecta por
        la ausencia de secciones con `sp`."""
        status, data = await self._get(
            client, self._prematch_path, params={"FI": fixture_id}
        )
        if status != 200 or not isinstance(data, dict):
            return status or 0, None
        results = data.get("results") or []
        if not results:
            # Fixture sin mercados publicados — miss definitivo (404 lógico).
            return 404, None
        event = results[0] if isinstance(results, list) else results
        if isinstance(event, dict) and not any(
            k not in ("FI", "event_id", "sport_id") for k in event
        ):
            # El plan gratis devuelve el stub {FI, event_id, sport_id}
            # para partidos empezados/terminados — equivale a un 404
            # de mercados (miss definitivo en este transporte).
            return 404, None
        return 200, data


# ---------------------------------------------------------------------------
# Provider del snapshotter (wrapper RapidAPI — plan gratis ~24 req/día)
# ---------------------------------------------------------------------------


class BetsApiOddsProvider:
    """OddsProvider sobre el wrapper gratis `betsapi2.p.rapidapi.com`.

    Sirve el feed bet365 REAL mientras el partido no ha empezado —
    su misión es CONGELAR el prematch de los picks nuevos (córners y
    tarjetas 3-way en líneas enteras, props de jugador, escalera
    alterna) antes de que la API deje de servirlo al pitido inicial.
    El backlog histórico es cosa del cliente directo de pago
    (`historical_backfill` + `BETSAPI_TOKEN`).

    El estado de cuota es independiente del directo (`NAME` propio):
    un 429 del free no aparca el trial de pago ni viceversa.
    """

    NAME = NAME_RAPID
    ID_PREFIX = "betsapi:"
    SUPPORTED_SPORTS = frozenset(SPORT_IDS)

    def __init__(self, api_key: str, api_host: str = _RAPIDAPI_HOST) -> None:
        self._client = BetsApiClient(
            rapidapi_key=api_key, rapidapi_host=api_host, name=self.NAME
        )
        # ext_id -> fixture resuelto en find_event: fetch_odds solo
        # recibe el id, y la traducción necesita home/away (totales de
        # equipo, hándicaps con nombre).
        self._fixtures: dict[str, BetsFixture] = {}

    async def find_event(
        self, sport: str, date: datetime, team_hint: str
    ) -> Optional[EventRef]:
        sport_id = SPORT_IDS.get(sport)
        if sport_id is None or not team_hint:
            return None
        async with httpx.AsyncClient(timeout=20) as client:
            fixtures = await self._client.upcoming(
                client,
                sport_id,
                date.date(),
                hint=team_hint,
                when=date,
            )
        if not fixtures:
            return None
        fixture = match_fixture(fixtures, team_hint, date)
        if fixture is None:
            return None
        ext_id = event_ext_id(fixture)
        self._fixtures[ext_id] = fixture
        return EventRef(
            event_ext_id=ext_id,
            home_team=fixture.home_name,
            away_team=fixture.away_name,
            start=fixture.start,
        )

    async def fetch_odds(
        self, event_ext_id: str, sport: str
    ) -> Optional[list[MarketChoice]]:
        if not event_ext_id.startswith(self.ID_PREFIX):
            return None
        fixture_id = event_ext_id.split(":", 1)[1]
        async with httpx.AsyncClient(timeout=30) as client:
            status, payload = await self._client.prematch(client, fixture_id)
        if status == 404:
            return []  # empezado/sin mercados en el plan gratis
        if payload is None:
            return None  # error/cuota: reintenta en el próximo ciclo
        fixture = self._fixtures.get(event_ext_id)
        choices = translate_prematch(
            payload,
            sport,
            fixture.home_name if fixture else "",
            fixture.away_name if fixture else "",
            when=fixture.start if fixture else None,
        )
        return [
            MarketChoice(
                market_name=c.market_name,
                choice_name=c.choice_name,
                choice_group=c.choice_group,
                cuota=c.points[-1].cuota,
            )
            for c in choices
            if c.points
        ]
