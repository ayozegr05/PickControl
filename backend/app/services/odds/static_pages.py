"""Cuotas de cierre desde listados estáticos (sin key, sin cuota).

Dos fuentes HTML planas que NO necesitan render JS ni LLM — la
página ya trae la cuota de cierre embebida en la fila del partido:

- **tennisexplorer.com** `/results/?type=all&year&month&day` —
  TODOS los partidos terminados del día (ATP, WTA, Challenger e
  **ITF**, el hueco que ninguna API ni bet365 cubre). Cada partido
  son dos `<tr>` (un jugador por fila, ids `rN`/`rNb`) con las
  cuotas decimales en `td.coursew`/`td.course` (columnas H/A).
  OJO: `/matches/?date=` ignora la fecha (sirve la cartelera actual)
  — solo vale de canario, no de histórico.
- **betexplorer.com** — dos vistas del mismo dato:
  * `/football/results/?year&month&day`: todos los partidos del
    día, pero `data-odd` solo viene embebido para fechas muy
    recientes (~ayer) — sirve para picks frescos.
  * `/football/{pais}/{liga}/results/`: resultados de la temporada
    entera con `data-odd` 1X2 en cada fila — la vía histórica.
    Requiere el mapa de ligas `_BE_LEAGUES` (se extiende al aparecer
    ligas nuevas, mismo criterio que `_LEAGUE_PAGES` de football24h).

Las páginas son grandes (~0.5-1.7 MB) pero una descarga cubre TODOS
los picks del día — sin cuota, sin rate-limit real (cortesía: un
fetch por página cacheado en la pasada).

Diagnóstico de rotura (canario): una página descargada bien pero con
CERO filas parseables es la firma de un cambio de layout — se cuenta
en `scraper_health` (provider_state) y pasado el umbral se avisa por
push a admins (`notify_scraper_broken`). Los fallos de red NO
cuentan: son transitorios. `fixtures()` devuelve None ante un
listado "sospechoso de vacío" para que el pick se reintente en vez
de marcarse como nofixture falso.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

import httpx

from app.core.dates import utc_now
from app.core.logging import get_logger
from app.services.odds.oddspapi import HistoricalChoice, PricePoint
from app.services.results.api_tennis import _pair_similar
from app.services.results.base import match_score, split_team_hint

logger = get_logger("app.odds.static_pages")

NAME_TE = "tennisexplorer"
NAME_BE = "betexplorer"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
_TIMEOUT = 45.0
_MIN_MATCH_SCORE = 0.6
# Página grande pero 0 filas -> parseo roto, no "día sin partidos".
_MIN_SUSPICIOUS_LEN = 50_000
# Fallos consecutivos de parseo antes de notificar al admin.
_CANARY_FAIL_THRESHOLD = 2
# El listado por día de BetExplorer solo embebe data-odd para fechas
# muy recientes; más viejo -> hay que ir a las páginas de liga.
_BE_DAY_ODDS_MAX_AGE = timedelta(days=2)

# Ligas cubiertas por la vía histórica de BetExplorer. Slugs
# verificados 2026-10-04; se extiende al aparecer ligas nuevas en
# los canales (el 404 de un slug mal escrito solo salta esa página,
# no rompe la pasada).
_BE_LEAGUES = (
    "spain/laliga",
    "spain/laliga2",
    "spain/copa-del-rey",
    "england/premier-league",
    "england/championship",
    "england/fa-cup",
    "italy/serie-a",
    "italy/serie-b",
    "italy/coppa-italia",
    "germany/bundesliga",
    "germany/2-bundesliga",
    "france/ligue-1",
    "france/ligue-2",
    "netherlands/eredivisie",
    "portugal/primeira-liga",
    "turkey/super-lig",
    "belgium/pro-league",
    "scotland/premiership",
    "austria/bundesliga",
    "switzerland/super-league",
    "greece/super-league",
    "europe/champions-league",
    "europe/europa-league",
    "europe/europa-conference-league",
    "argentina/liga-profesional",
    "brazil/serie-a",
    "mexico/liga-mx-apertura",
    "usa/mls",
    "colombia/primera-a",
    "chile/primera-division",
    "saudi-arabia/saudi-professional-league",
    "japan/j1-league",
    "south-korea/k-league-1",
    "asia/afc-champions-league",
)


@dataclass
class StaticMatch:
    """Un partido de un listado con su(s) cuota(s) de cierre."""

    home_name: str  # jugador 1 / equipo local, tal cual la página
    away_name: str
    start: datetime
    ext_id: str
    tournament: str
    odds1: Optional[float]
    oddsX: Optional[float]
    odds2: Optional[float]
    # BetExplorer: ruta "/football/{pais}/{liga}/" de la cabecera de
    # torneo — permite descargar la página de liga bajo demanda para
    # sacar el data-odd cuando la fila del día no lo embebe.
    league_path: str = ""

    @property
    def has_odds(self) -> bool:
        return self.odds1 is not None or self.odds2 is not None


# ---------------------------------------------------------------------------
# Salud del scraper (canario): página OK pero 0 filas parseables.
# ---------------------------------------------------------------------------


def _record_health(name: str, ok: bool) -> None:
    """Cuántas veces seguidas el listado viene descargado pero vacío.

    Persistido en provider_state para la vista de sistema y para que
    la alarma no se reenvíe cada pasada."""
    from app.services.results.base import _load_state, _save_state

    health = (
        _load_state()
        .setdefault("scraper_health", {})
        .setdefault(name, {"fails": 0, "alerted": False})
    )
    if ok:
        health["fails"] = 0
        health["alerted"] = False
        health["last_ok"] = utc_now().isoformat()
    else:
        health["fails"] = int(health.get("fails", 0)) + 1
        health["last_fail"] = utc_now().isoformat()
    _save_state()


def scraper_health() -> dict:
    """Estado de salud de los listados estáticos (vista de sistema)."""
    from app.services.results.base import _load_state

    return _load_state().get("scraper_health", {})


def _load_state_health(name: str) -> dict:
    from app.services.results.base import _load_state

    return _load_state().get("scraper_health", {}).get(name, {})


def _health_broken(name: str) -> bool:
    health = _load_state_health(name)
    return int(health.get("fails", 0)) >= _CANARY_FAIL_THRESHOLD and not health.get(
        "alerted"
    )


def _mark_alerted(name: str) -> None:
    from app.services.results.base import _load_state, _save_state

    health = _load_state().setdefault("scraper_health", {}).setdefault(name, {})
    health["alerted"] = True
    _save_state()


async def _notify_broken(name: str, detail: str) -> None:
    """Push a admins cuando el listado lleva N pasadas viniendo vacío
    (layout cambiado). Best-effort: nunca rompe el backfill."""
    try:
        from app.services.notifications.push import notify_scraper_broken

        await notify_scraper_broken(name, detail)
        _mark_alerted(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STATIC_ODDS] notify_scraper_broken(%s): %s", name, exc)


async def canary(http: httpx.AsyncClient) -> dict[str, int]:
    """Sonda diaria: descarga el listado de HOY de cada fuente y
    cuenta filas parseables. Cero filas con página grande = layout
    roto -> push tras el umbral. Devuelve {fuente: n_filas}.

    NO consume cuota de nada: un fetch propio + regex local. Se llama
    una vez por pasada del job diario. Para tennisexplorer la sonda
    usa /matches/ (la cartelera actual — /results/ de hoy aún puede
    venir vacía porque los partidos no han terminado)."""
    today = utc_now().date()
    result: dict[str, int] = {}
    for name, fetcher in (
        (NAME_TE, fetch_tennisexplorer_upcoming),
        (NAME_BE, fetch_betexplorer_day),
    ):
        try:
            rows, suspicious = await fetcher(http, today, health=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[STATIC_ODDS] canario %s falló: %s", name, exc)
            continue
        result[name] = len(rows)
        _record_health(name, not suspicious)
        if suspicious and _health_broken(name):
            await _notify_broken(
                name, "devuelve páginas sin filas de partido parseables"
            )
    return result


# ---------------------------------------------------------------------------
# TennisExplorer
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_TE_ROW = re.compile(r'<tr id="([rs]\d+)(b)?"[^>]*>(.*?)</tr>', re.S)
_TE_NAME = re.compile(r'<td class="t-name"><a[^>]*>(.*?)</a>', re.S)
_TE_TIME = re.compile(r'<td class="first time"[^>]*>(\d{1,2}):(\d{2})</td>')
_TE_ODDS = re.compile(
    r'<td class="course[w]?" rowspan="2">(?:&nbsp;|([0-9]+(?:\.[0-9]+)?))</td>'
)
_TE_DETAIL = re.compile(r"match-detail/\?id=(\d+)")
_TE_TOURNAMENT = re.compile(r'<a href="/[^"]+/"><span[^>]*>.*?</a>', re.S)


def _te_tournament(row_html: str) -> str:
    m = _TE_NAME.search(row_html)
    if not m:
        m = _TE_TOURNAMENT.search(row_html)
    if not m:
        return ""
    raw = _TAG.sub(" ", m.group(1) if m.lastindex else m.group(0))
    raw = raw.replace("&nbsp;", " ")
    return " ".join(raw.split())


def _parse_tennisexplorer(text: str, day: date) -> tuple[list[StaticMatch], bool]:
    """Partidos del HTML + bandera de 'página grande pero sin filas'
    (layout roto). El documento alterna cabeceras de torneo
    (`tr.head`) y parejas de filas sN/sNb o rN/rNb (jugador 1/2)."""
    matches: list[StaticMatch] = []
    tournament = ""
    pending: Optional[StaticMatch] = None
    for m in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.S):
        row = m.group(0)
        if row.startswith('<tr class="head'):
            tournament = _te_tournament(row)
            continue
        rm = _TE_ROW.match(row)
        if not rm:
            continue
        base_id, is_second, body = rm.groups()
        if not is_second:
            name_m = _TE_NAME.search(body)
            time_m = _TE_TIME.search(body)
            odds = _TE_ODDS.findall(body)
            detail = _TE_DETAIL.search(body)
            start = datetime.combine(day, time(12, 0))
            if time_m:
                start = datetime.combine(
                    day, time(int(time_m.group(1)), int(time_m.group(2)))
                )
            pending = StaticMatch(
                home_name=_TAG.sub("", name_m.group(1)).strip() if name_m else "",
                away_name="",
                start=start,
                ext_id=(
                    f"{NAME_TE}:{detail.group(1)}" if detail else f"{NAME_TE}:{base_id}"
                ),
                tournament=tournament,
                odds1=float(odds[0]) if len(odds) > 0 and odds[0] else None,
                oddsX=None,
                odds2=float(odds[1]) if len(odds) > 1 and odds[1] else None,
            )
        elif pending is not None:
            name_m = _TE_NAME.search(body)
            pending.away_name = _TAG.sub("", name_m.group(1)).strip() if name_m else ""
            matches.append(pending)
            pending = None

    return matches, (not matches and len(text) > _MIN_SUSPICIOUS_LEN)


async def fetch_tennisexplorer_day(
    http: httpx.AsyncClient, day: date, health: bool = True
) -> tuple[list[StaticMatch], bool]:
    """(partidos, sospechoso_vacío) del archivo /results/ del día —
    cubre ATP/WTA/Challenger/ITF con cuota de ganador inline."""
    url = (
        "https://www.tennisexplorer.com/results/"
        f"?type=all&year={day.year}&month={day.month}&day={day.day}"
    )
    resp = await http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    matches, suspicious = _parse_tennisexplorer(resp.text, day)
    if health:
        _record_health(NAME_TE, not suspicious)
    return matches, suspicious


async def fetch_tennisexplorer_upcoming(
    http: httpx.AsyncClient, day: date, health: bool = True
) -> tuple[list[StaticMatch], bool]:
    """Cartelera actual (/matches/) — solo para el canario: siempre
    trae filas, cosa que /results/ de hoy aún no garantiza."""
    url = "https://www.tennisexplorer.com/matches/?type=all"
    resp = await http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    matches, suspicious = _parse_tennisexplorer(resp.text, day)
    if health:
        _record_health(NAME_TE, not suspicious)
    return matches, suspicious


def te_match(rows: list[StaticMatch], hint: str) -> Optional[StaticMatch]:
    """Casa el hint ("Apellido1 vs Apellido2" o jugador suelto) con
    la fila — `_pair_similar` entiende el formato "Apellido I."."""
    parts = split_team_hint(hint)
    best: Optional[StaticMatch] = None
    best_score = 0.0
    for row in rows:
        if len(parts) == 1:
            score = max(
                _pair_similar(parts[0], row.home_name),
                _pair_similar(parts[0], row.away_name),
            )
        else:
            direct = min(
                _pair_similar(parts[0], row.home_name),
                _pair_similar(parts[1], row.away_name),
            )
            cross = min(
                _pair_similar(parts[0], row.away_name),
                _pair_similar(parts[1], row.home_name),
            )
            score = max(direct, cross)
        if score > best_score:
            best, best_score = row, score
    return best if best_score >= _MIN_MATCH_SCORE else None


def te_choices(row: StaticMatch) -> list[HistoricalChoice]:
    """Mercado "Full time" 1/2 con la cuota de cierre del listado
    (única cifra que publica — ni apertura ni curva)."""
    choices: list[HistoricalChoice] = []
    for choice_name, price in (("1", row.odds1), ("2", row.odds2)):
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


# ---------------------------------------------------------------------------
# BetExplorer (fútbol)
# ---------------------------------------------------------------------------

_BE_TOURNAMENT = re.compile(
    r'<a href="(/football/[^"]+/)" class="table-main__tournament">(.*?)</a>',
    re.S,
)
# Fila del listado por día: <tr data-dt="d,m,YYYY,H,M" ...>
_BE_ROW = re.compile(
    r"<tr[^>]*data-dt=\"(\d+),(\d+),(\d+),(\d+),(\d+)\"[^>]*>(.*?)</tr>", re.S
)
# Fila de la página de liga: <tr><td class="h-text-left"><a ...
# class="in-match">A - B</a></td> ... <td ...>FECHA</td></tr>
_BE_LEAGUE_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
# <a class="in-match" href="/football/.../home-away/ID/">A - B</a> —
# el orden de atributos varía; se captura el tag entero y se saca
# href y texto por separado.
_BE_IN_MATCH = re.compile(r'<a\b[^>]*\bclass="in-match"[^>]*>(.*?)</a>', re.S)
_BE_HREF = re.compile(r'href="(/football/[^"]+/([A-Za-z0-9]{6,})/?)"')
_BE_DATE_CELL = re.compile(r'<td class="h-text-right h-text-no-wrap">([^<]+)</td>')
_BE_ROUND = re.compile(r'<th class="h-text-left"[^>]*>(.*?)</th>', re.S)
_BE_TEAMS = re.compile(
    r'<a href="(/football/[^"]+/([A-Za-z0-9]{6,})/?)"[^>]*>(.*?)</a>', re.S
)
_BE_ODD = re.compile(r'data-odd="([0-9]+(?:\.[0-9]+)?)"')
_BE_DATE_TXT = re.compile(r"(\d{1,2})\.(\d{1,2})\.")


def _be_teams(body: str) -> Optional[tuple[str, str, str]]:
    """(home, away, ext_code) del enlace del partido, o None."""
    link = _BE_TEAMS.search(body)
    if not link:
        return None
    teams_raw = _TAG.sub("", link.group(3))
    teams = re.split(r"\s+-\s+", teams_raw.strip(), maxsplit=1)
    if len(teams) != 2:
        return None
    return teams[0].strip(), teams[1].strip(), link.group(2)


def _parse_betexplorer_day(text: str) -> tuple[list[StaticMatch], bool]:
    """Partidos del listado por día (con tournament por sección)."""
    matches: list[StaticMatch] = []
    tournament = ""
    league_path = ""
    for m in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.S):
        row = m.group(0)
        tm = _BE_TOURNAMENT.search(row)
        if tm:
            league_path = tm.group(1)
            tournament = _TAG.sub(" ", tm.group(2)).strip()
            continue
        rm = _BE_ROW.search(row)
        if not rm:
            continue
        d, mo, y, h, mi = (int(g) for g in rm.groups()[:5])
        body = rm.group(6)
        parsed = _be_teams(body)
        if not parsed:
            continue
        home, away, code = parsed
        odds = _BE_ODD.findall(body)
        matches.append(
            StaticMatch(
                home_name=home,
                away_name=away,
                start=datetime(y, mo, d, h, mi),
                ext_id=f"{NAME_BE}:{code}",
                tournament=tournament,
                odds1=float(odds[0]) if len(odds) > 0 else None,
                oddsX=float(odds[1]) if len(odds) > 1 else None,
                odds2=float(odds[2]) if len(odds) > 2 else None,
                league_path=league_path,
            )
        )
    return matches, (not matches and len(text) > _MIN_SUSPICIOUS_LEN)


async def fetch_betexplorer_day(
    http: httpx.AsyncClient, day: date, health: bool = True
) -> tuple[list[StaticMatch], bool]:
    """(partidos, sospechoso_vacío) de /football/results/ del día.

    OJO: `data-odd` solo viene embebido para fechas muy recientes
    (~ayer); más atrás las filas existen pero sin cuota — para
    histórico está `fetch_betexplorer_league`."""
    url = (
        "https://www.betexplorer.com/football/results/"
        f"?year={day.year}&month={day.month}&day={day.day}"
    )
    resp = await http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    matches, suspicious = _parse_betexplorer_day(resp.text)
    if health:
        _record_health(NAME_BE, not suspicious)
    return matches, suspicious


def _parse_league_date(text: str) -> Optional[date]:
    """Fecha de la celda final del row de liga: "Today", "Yesterday"
    o "DD.MM." (el año se infiere del calendario actual)."""
    raw = text.strip()
    today = utc_now().date()
    if raw.lower() == "today":
        return today
    if raw.lower() == "yesterday":
        return today - timedelta(days=1)
    m = _BE_DATE_TXT.fullmatch(raw)
    if not m:
        return None
    candidate = date(today.year, int(m.group(2)), int(m.group(1)))
    # La temporada cruza el año: una fecha "15.08." en octubre es de
    # ESTE año; una "28.12." futura sería de la temporada anterior.
    if candidate > today:
        candidate = date(today.year - 1, int(m.group(2)), int(m.group(1)))
    return candidate


def _parse_betexplorer_league(
    text: str, tournament: str, league_path: str = ""
) -> tuple[list[StaticMatch], bool]:
    """Partidos de una página /results/ de liga (toda la temporada
    con `data-odd` por fila)."""
    matches: list[StaticMatch] = []
    for m in _BE_LEAGUE_ROW.finditer(text):
        body = m.group(1)
        link = _BE_IN_MATCH.search(body)
        if not link:
            continue
        href = _BE_HREF.search(link.group(0))
        teams_raw = _TAG.sub("", link.group(1))
        teams = re.split(r"\s+-\s+", teams_raw.strip(), maxsplit=1)
        if len(teams) != 2 or not href:
            continue
        date_cell = _BE_DATE_CELL.search(body)
        day = _parse_league_date(date_cell.group(1)) if date_cell else None
        odds = _BE_ODD.findall(body)
        matches.append(
            StaticMatch(
                home_name=teams[0].strip(),
                away_name=teams[1].strip(),
                # La página de liga no publica la hora: mediodía UTC
                # como convención (la fecha es lo auditable).
                start=(
                    datetime.combine(day, time(12, 0)) if day else datetime(2000, 1, 1)
                ),
                ext_id=f"{NAME_BE}:{href.group(2)}",
                tournament=tournament,
                odds1=float(odds[0]) if len(odds) > 0 else None,
                oddsX=float(odds[1]) if len(odds) > 1 else None,
                odds2=float(odds[2]) if len(odds) > 2 else None,
                league_path=league_path,
            )
        )
    return matches, (not matches and len(text) > _MIN_SUSPICIOUS_LEN)


async def fetch_betexplorer_league(
    http: httpx.AsyncClient, league_path: str
) -> list[StaticMatch]:
    """Todos los partidos de la temporada de una liga con sus
    cierres 1X2. Un slug malo/devuelto en 404 da lista vacía (no
    rompe la pasada)."""
    url = f"https://www.betexplorer.com/football/{league_path}/results/"
    try:
        resp = await http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("[STATIC_ODDS] betexplorer liga %s: %s", league_path, exc)
        return []
    if resp.status_code != 200:
        return []
    tournament = " ".join(p.replace("-", " ") for p in league_path.split("/"))
    matches, _ = _parse_betexplorer_league(resp.text, tournament, league_path)
    return matches


def be_match(rows: list[StaticMatch], hint: str) -> Optional[StaticMatch]:
    """Casa el hint con la fila por `match_score` (equipos)."""
    best: Optional[StaticMatch] = None
    best_score = 0.0
    for row in rows:
        score = match_score(hint, row.home_name, row.away_name)
        if score > best_score:
            best, best_score = row, score
    return best if best_score >= _MIN_MATCH_SCORE else None


def be_choices(row: StaticMatch) -> list[HistoricalChoice]:
    """Mercado "Full time" 1/X/2 con los cierres `data-odd` del
    listado (una sola cifra por opción — cuota media de cierre)."""
    choices: list[HistoricalChoice] = []
    for choice_name, price in (
        ("1", row.odds1),
        ("X", row.oddsX),
        ("2", row.odds2),
    ):
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


__all__ = [
    "NAME_TE",
    "NAME_BE",
    "StaticMatch",
    "fetch_tennisexplorer_day",
    "fetch_tennisexplorer_upcoming",
    "fetch_betexplorer_day",
    "fetch_betexplorer_league",
    "te_match",
    "be_match",
    "te_choices",
    "be_choices",
    "canary",
    "scraper_health",
]
