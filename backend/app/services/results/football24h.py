"""Provider determinista sobre football24hours.com.

La web sirve HTML estático sin Cloudflare ni API key:

- Las páginas de liga (``/{pais}/{liga}/``) listan los partidos de la
  ventana reciente (~1-2 semanas) con URL
  ``/{pais}/{liga}/{DD-MM-YYYY}-{local}-vs-{visitante}.html`` — la fecha
  y los equipos van en el propio slug, así que el fixture se casa
  directamente contra la URL sin descargar cada partido.
- Las páginas globales ``/today-matches``, ``/yesterday-matches`` y
  ``/tomorrow-matches`` listan TODOS los partidos del día (cientos,
  todas las ligas): cubren cualquier pick verificado al día siguiente
  del partido sin necesidad de conocer la competición.
- Cada página de partido embebe marcador, estado (``FINAL-TIME``) y una
  tabla de estadísticas (``Corners``, ``Shots``, ``Possession``...) en
  HTML plano.

Cubre la cola larga que los providers de API no tocan: reservas y
divisiones menores danesas, AFC Cup, ligas femeninas de países pequeños...
Sin LLM ni buscador: escaneo directo de listados de liga/fecha.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    MatchResult,
    MatchStats,
    count_provider_call,
    fold_name,
    is_missed,
    mark_missed,
    match_score,
    miss_is_provisional,
    split_team_hint,
)

logger = get_logger("app.results.football24h")

_BASE = "https://www.football24hours.com"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Ventana de tolerancia entre la fecha del pick y la fecha del slug —
# el tipster suele publicar el día correcto pero la zona horaria puede
# mover el partido de día.
_DATE_TOLERANCE = timedelta(days=1)
_MIN_TEAM_SIMILARITY = 0.6

# Listados globales por día relativo: si el pick es de ayer se escanea
# `yesterday-matches` (todas las ligas, sin configuración).
_DAY_PAGES = {
    0: "/today-matches",
    -1: "/yesterday-matches",
    1: "/tomorrow-matches",
}

# Listados de liga de la cola larga: cada página conserva unas dos
# semanas de fixtures — rescata picks pendientes de días atrás en las
# competiciones que los providers de API no cubren. Se extiende a
# medida que aparecen ligas nuevas en los canales.
_LEAGUE_PAGES = (
    "/denmark/u21-ligaen/",
    "/denmark/1st-division/",
    "/denmark/2nd-division/",
    "/denmark/denmark-series-group-1/",
    "/denmark/denmark-series-group-2/",
    "/asia/afc-cup/",
)

# Nombre actual del club -> nombre histórico que usa la web (la página
# no actualiza renombrados: Lion City Sailors sigue siendo Home United,
# BG Pathum United sigue siendo Bangkok Glass). Mismo patrón que
# `_COUNTRY_ALIASES` de espn.py.
_CLUB_ALIASES = {
    "lion city sailors": "home united",
    "lion city": "home united",
    "bg pathum": "bangkok glass",
    "pathum united": "bangkok glass",
}

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Letras latinas que NFD no descompone (`fold_name` las conserva y el
# regex las comería como separador): ø→o, æ→ae, ð→d, þ→th, ß→ss, ł→l...
_SLUG_CHARS = str.maketrans(
    {"ø": "o", "æ": "ae", "ð": "d", "þ": "th", "ß": "ss", "ł": "l", "đ": "d"}
)


def _slugify(name: str) -> str:
    return _SLUG_NON_ALNUM.sub("-", fold_name(name).translate(_SLUG_CHARS)).strip("-")


_MATCH_LINK = re.compile(
    r'href="(/[a-z-]+/[a-z0-9-]+/(\d{2})-(\d{2})-(\d{4})'
    r"-([a-z0-9-]+)-vs-([a-z0-9-]+)\.html)\""
)

_STATUS_FINISHED = "board--match-summary-status-label-final_time"
_TEAM_LABEL = re.compile(r'board--match-summary-team-square-label">([^<]+)</p>')
_SCORE = re.compile(r'board--match-summary-scoreboard-score">(\d+)<')
_STAT_ROW = re.compile(
    r'timeline--statistic-hometeam">\s*([\d.]+)\s*</div>.*?'
    r'timeline--statistic-type">\s*([^<]+?)\s*</div>.*?'
    r'timeline--statistic-awayteam">\s*([\d.]+)\s*<',
    re.S,
)

# `timeline--statistic-type` de la web -> clave canónica que consume el
# verificador (mismas que API-Football `/fixtures/statistics`).
_STAT_NAME_MAP = {
    "corners": "Corner Kicks",
    "shots": "Total Shots",
    "shots on goal": "Shots on Goal",
    "ball possession": "Ball Possession",
    "possession": "Ball Possession",
    "fouls": "Fouls",
    "yellow cards": "Yellow Cards",
    "red cards": "Red Cards",
    "offsides": "Offsides",
}

# Caché en memoria de los listados (liga/día): una pasada verifica
# varios picks y no tiene sentido re-descargar la misma página en cada
# uno. TTL corto — las páginas se actualizan en vivo.
_LIST_TTL = 300  # 5 min
_list_cache: dict[str, tuple[float, str]] = {}


def _translate_clubs(text: str) -> str:
    """Nombre actual -> nombre histórico de la web (palabra completa)."""
    out = text.lower()
    for current, old in _CLUB_ALIASES.items():
        out = re.sub(rf"\b{re.escape(current)}\b", old, out)
    return out


def _slug_name(slug: str) -> str:
    """'sonderjyske-reserves' -> 'sonderjyske reserves'."""
    return slug.replace("-", " ")


class Football24hProvider:
    """Fútbol de cola larga: listados de liga/día de football24hours.

    Sin key ni cuota: una descarga por listado consultado (cacheada
    5 min) más una por la página del partido casado.
    """

    NAME = "football24h"
    SUPPORTED_SPORTS = frozenset({"futbol"})

    async def _fetch(self, path: str) -> Optional[str]:
        """HTML del path, con caché TTL para los listados."""
        now = time.monotonic()
        cached = _list_cache.get(path)
        if cached and now - cached[0] < _LIST_TTL:
            return cached[1]
        try:
            count_provider_call(self.NAME)
            async with httpx.AsyncClient(
                timeout=20, headers={"User-Agent": _UA}
            ) as client:
                resp = await client.get(_BASE + path, follow_redirects=True)
            if resp.status_code != 200 or len(resp.text) < 3000:
                return None
            _list_cache[path] = (now, resp.text)
            return resp.text
        except httpx.HTTPError as exc:
            logger.debug("[F24H] %s fallo de red: %s", path, exc)
            return None

    def _iter_board(self, html: str):
        """(path, fecha, local, visitante) de cada partido del listado."""
        for m in _MATCH_LINK.finditer(html):
            try:
                day, month, year = int(m.group(2)), int(m.group(3)), int(m.group(4))
                yield (
                    m.group(1),
                    datetime(year, month, day),
                    _slug_name(m.group(5)),
                    _slug_name(m.group(6)),
                )
            except ValueError:
                continue

    async def _find_path(
        self, date: datetime, team_hint: str
    ) -> Optional[tuple[str, str, str]]:
        """(path, nombre local, nombre visitante) del fixture casado.

        Escanea el listado del día relativo del pick (todas las ligas)
        más los listados de liga de cola larga configurados.
        """
        hint = team_hint.strip()
        translated = _translate_clubs(hint)
        candidates = {hint.lower(), translated}
        day_page = _DAY_PAGES.get((date.date() - datetime.now().date()).days)

        pages = list(_LEAGUE_PAGES)
        if day_page:
            pages.insert(0, day_page)

        best: Optional[tuple[str, str, str]] = None
        best_score = 0.0
        for page in pages:
            html = await self._fetch(page)
            if html is None:
                continue
            for path, match_date, home, away in self._iter_board(html):
                if abs(match_date - date.replace(hour=0, minute=0)) > _DATE_TOLERANCE:
                    continue
                score = max(match_score(h, home, away) for h in candidates)
                if score > best_score:
                    best_score = score
                    best = (path, home, away)
        if best is None or best_score < _MIN_TEAM_SIMILARITY:
            return await self._probe_slug(date, candidates)
        return best

    async def _probe_slug(
        self, date: datetime, candidates: set[str]
    ) -> Optional[tuple[str, str, str]]:
        """URL directa del partido cuando los listados ya no lo muestran.

        El slug es determinista (`{DD-MM-YYYY}-{home}-vs-{away}.html`)
        y la página del partido sobrevive semanas aunque el fixture
        rote fuera del listado de liga — el router resuelve por slug
        ignorando el prefijo de liga. Los soft-404 (la web devuelve
        200 con otra página) se rechazan validando los nombres del
        marcador contra el hint."""
        # El hint puede venir traducido por alias: probar todas las
        # variantes como origen del slug.
        for hint in candidates:
            parts = split_team_hint(hint)
            if len(parts) < 2:
                continue
            home_slug = _slugify(parts[0])
            away_slug = _slugify(parts[1])
            if not home_slug or not away_slug:
                continue
            for offset in range(-1, 2):
                day = date + timedelta(days=offset)
                slug = f"{day:%d-%m-%Y}-{home_slug}-vs-{away_slug}.html"
                for league in _LEAGUE_PAGES:
                    html = await self._fetch(f"{league}{slug}")
                    if html is None:
                        continue
                    names = _TEAM_LABEL.findall(html)
                    if len(names) < 2:
                        continue
                    sim = max(
                        match_score(h, names[0].strip(), names[1].strip())
                        for h in candidates
                    )
                    if sim >= _MIN_TEAM_SIMILARITY:
                        return (
                            f"{league}{slug}",
                            names[0].strip(),
                            names[1].strip(),
                        )
        return None

    def _parse_match(self, html: str) -> Optional[MatchResult]:
        """Marcador del partido si está FINAL-TIME, si no None."""
        if _STATUS_FINISHED not in html:
            return None
        scores = _SCORE.findall(html)
        teams = _TEAM_LABEL.findall(html)
        if len(scores) < 2 or len(teams) < 2:
            return None
        return MatchResult(
            home_team=teams[0].strip(),
            away_team=teams[1].strip(),
            home_score=int(scores[0]),
            away_score=int(scores[1]),
        )

    def _parse_stats(
        self, html: str, home_team: str, away_team: str
    ) -> Optional[MatchStats]:
        """Tabla de estadísticas -> MatchStats con claves canónicas."""
        values: dict[str, tuple[int, int]] = {}
        for home_v, name, away_v in _STAT_ROW.findall(html):
            key = _STAT_NAME_MAP.get(name.strip().lower())
            if key is None:
                continue
            values[key] = (int(float(home_v)), int(float(away_v)))
        if not values:
            return None
        return MatchStats(home_team=home_team, away_team=away_team, values=values)

    async def _finished_page(
        self, date: datetime, team_hint: str
    ) -> Optional[tuple[str, MatchResult]]:
        """(html, MatchResult) del fixture terminado que casa el hint."""
        if not team_hint.strip():
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{self.NAME}|futbol|{date.strftime('%Y-%m-%d')}"
            f"|{team_hint.strip().lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        found = await self._find_path(date, team_hint)
        if found is None:
            mark_missed(miss_key)
            return None
        path, _, _ = found
        html = await self._fetch(path)
        if html is None:
            return None
        match = self._parse_match(html)
        if match is None:
            return None  # aún no jugado o en vivo: reintentable, no miss
        return html, match

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        found = await self._finished_page(date, team_hint)
        return found[1] if found else None

    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        found = await self._finished_page(date, team_hint)
        if found is None:
            return None
        html, match = found
        return self._parse_stats(html, match.home_team, match.away_team)
