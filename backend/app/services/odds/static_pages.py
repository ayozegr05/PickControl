"""Cuotas de cierre desde listados estáticos (sin key, sin cuota).

Dos fuentes HTML planas que NO necesitan render JS ni LLM — el
listado del día/liga ya trae la cuota de cierre embebida en la fila
del partido:

- **tennisexplorer.com** `/matches/?date=YYYY-MM-DD&type=all` —
  todos los partidos del día (ATP, WTA, Challenger e **ITF**, el
  hueco que ninguna API cubre). Cada partido son dos `<tr>` (un
  jugador por fila) con sus cuotas decimales en `td.coursew` /
  `td.course` (columnas H/A del header).
- **betexplorer.com** `/football/results/?year&month&day` — todos
  los partidos del día por liga, cada fila con los cierres 1X2 en
  atributos `data-odd`.

Las dos páginas son de ~1 MB: una descarga cubre TODOS los picks del
día, así que el coste por pick es una fracción de fetch — sin cuota,
sin rate-limit real (cortesía: un fetch por día cacheado en la
pasada).

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
from datetime import date, datetime, time
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


def _health_broken(name: str) -> bool:
    health = _load_state_health(name)
    return int(health.get("fails", 0)) >= _CANARY_FAIL_THRESHOLD and not health.get(
        "alerted"
    )


def _load_state_health(name: str) -> dict:
    from app.services.results.base import _load_state

    return _load_state().get("scraper_health", {}).get(name, {})


def _mark_alerted(name: str) -> None:
    from app.services.results.base import _load_state, _save_state

    health = _load_state().setdefault("scraper_health", {}).setdefault(name, {})
    health["alerted"] = True
    _save_state()


async def _notify_broken(name: str, detail: str) -> None:
    """Push a admins cuando el listado lleva N días viniendo vacío
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
    una vez por pasada del job diario."""
    result: dict[str, int] = {}
    for name, fetcher in (
        (NAME_TE, fetch_tennisexplorer_day),
        (NAME_BE, fetch_betexplorer_day),
    ):
        try:
            rows, suspicious = await fetcher(http, utc_now().date(), health=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[STATIC_ODDS] canario %s falló: %s", name, exc)
            continue
        result[name] = len(rows)
        _record_health(name, not suspicious)
        if suspicious and _health_broken(name):
            await _notify_broken(name, f"{name} devuelve páginas sin filas parseables")
    return result


# ---------------------------------------------------------------------------
# TennisExplorer
# ---------------------------------------------------------------------------

# Cabecera de torneo: <tr class="head flags"><td class="t-name"><a
# href="/.../"> ...NOMBRE</a> — el nombre vive dentro del <a> tras
# los spans de bandera/tipo.
_TE_HEAD = re.compile(r'<tr class="head[^"]*".*?</tr>', re.S)
_TE_ROW = re.compile(r'<tr id="(s\d+)(b)?"[^>]*>(.*?)</tr>', re.S)
_TE_NAME = re.compile(r'<td class="t-name"><a[^>]*>(.*?)</a>', re.S)
_TE_TIME = re.compile(r'<td class="first time"[^>]*>(\d{1,2}):(\d{2})</td>')
_TE_ODDS = re.compile(
    r'<td class="course[w]?" rowspan="2">(?:&nbsp;|([0-9]+(?:\.[0-9]+)?))</td>'
)
_TE_DETAIL = re.compile(r"match-detail/\?id=(\d+)")
_TE_TOURNAMENT = re.compile(r'<a href="/[^"]+/"><span[^>]*>.*?</a>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _te_tournament(row_html: str) -> str:
    m = _TE_NAME.search(row_html)
    if not m:
        m = _TE_TOURNAMENT.search(row_html)
    if not m:
        return ""
    raw = _TAG.sub(" ", m.group(1) if m.lastindex else m.group(0))
    raw = raw.replace("&nbsp;", " ")
    return " ".join(raw.split())


async def fetch_tennisexplorer_day(
    http: httpx.AsyncClient, day: date, health: bool = True
) -> tuple[list[StaticMatch], bool]:
    """(partidos, sospechoso_vacío) del listado del día.

    `sospechoso_vacío` = página descargada grande pero sin una sola
    fila de partido — firma de layout roto."""
    url = f"https://www.tennisexplorer.com/matches/?date={day:%Y-%m-%d}&type=all"
    resp = await http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    text = resp.text

    matches: list[StaticMatch] = []
    tournament = ""
    # El documento alterna cabeceras de torneo (tr.head) y parejas de
    # filas sN/sNb (jugador 1 / jugador 2).
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

    suspicious = not matches and len(text) > _MIN_SUSPICIOUS_LEN
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
# BetExplorer (fútbol): /football/results/?year&month&day
# ---------------------------------------------------------------------------

_BE_TOURNAMENT = re.compile(
    r'<a href="(/football/[^"]+/)" class="table-main__tournament">(.*?)</a>',
    re.S,
)
_BE_ROW = re.compile(
    r"<tr[^>]*data-dt=\"(\d+),(\d+),(\d+),(\d+),(\d+)\"[^>]*>(.*?)</tr>", re.S
)
_BE_TEAMS = re.compile(
    r'<a href="(/football/[^"]+/([A-Za-z0-9]{6,})/?)"[^>]*>(.*?)</a>', re.S
)
_BE_ODD = re.compile(r'data-odd="([0-9]+(?:\.[0-9]+)?)"')
_BE_EXT = re.compile(r"/([A-Za-z0-9]{6,})/?$")


async def fetch_betexplorer_day(
    http: httpx.AsyncClient, day: date, health: bool = True
) -> tuple[list[StaticMatch], bool]:
    """(partidos, sospechoso_vacío) de /football/results/ del día."""
    url = (
        "https://www.betexplorer.com/football/results/"
        f"?year={day.year}&month={day.month}&day={day.day}"
    )
    resp = await http.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    text = resp.text

    matches: list[StaticMatch] = []
    tournament = ""
    # El documento alterna <tr class="js-tournament"> (liga) con
    # <tr data-dt="d,m,YYYY,H,M"> (partido + cierres 1X2 en data-odd).
    for m in re.finditer(r"<tr\b[^>]*>.*?</tr>", text, re.S):
        row = m.group(0)
        tm = _BE_TOURNAMENT.search(row)
        if tm:
            tournament = _TAG.sub(" ", tm.group(2)).strip()
            continue
        rm = _BE_ROW.search(row)
        if not rm:
            continue
        d, mo, y, h, mi = (int(g) for g in rm.groups()[:5])
        body = rm.group(6)
        link = _BE_TEAMS.search(body)
        if not link:
            continue
        teams_raw = _TAG.sub("", link.group(3))
        teams = re.split(r"\s+-\s+", teams_raw.strip(), maxsplit=1)
        if len(teams) != 2:
            continue
        odds = _BE_ODD.findall(body)
        ext = _BE_EXT.search(link.group(1))
        matches.append(
            StaticMatch(
                home_name=teams[0].strip(),
                away_name=teams[1].strip(),
                start=datetime(y, mo, d, h, mi),
                ext_id=(
                    f"{NAME_BE}:{ext.group(1)}" if ext else f"{NAME_BE}:{link.group(1)}"
                ),
                tournament=tournament,
                odds1=float(odds[0]) if len(odds) > 0 else None,
                oddsX=float(odds[1]) if len(odds) > 1 else None,
                odds2=float(odds[2]) if len(odds) > 2 else None,
            )
        )

    suspicious = not matches and len(text) > _MIN_SUSPICIOUS_LEN
    if health:
        _record_health(NAME_BE, not suspicious)
    return matches, suspicious


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
    for choice_name, price in (("1", row.odds1), ("X", row.oddsX), ("2", row.odds2)):
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
    "fetch_betexplorer_day",
    "te_match",
    "be_match",
    "te_choices",
    "be_choices",
    "canary",
    "scraper_health",
]
