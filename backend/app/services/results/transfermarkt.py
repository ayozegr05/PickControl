"""Provider determinista sobre transfermarkt.es.

La web sirve HTML estático (vía Cloudflare) sin API key:

- ``/live/index?datum=YYYY-MM-DD`` lista TODOS los partidos del día
  (todas las competiciones, incluidas ligas menores, reservas y
  femenino): cada fila ``<tr class="begegnungZeile">`` lleva los
  equipos en ``verein-heim``/``verein-gast``, el estado
  ``matchresult finished`` con el marcador inline y el enlace al
  informe ``/spielbericht/index/spielbericht/{match_id}``.
- ``/statistik/index/spielbericht/{match_id}`` embebe la tabla de
  estadísticas (``unterueberschrift`` + ``sb-statistik-zahl``):
  córners, disparos, faltas, fueras de juego, paradas...

El marcador se lee directamente del listado; la página de stats solo
se descarga para mercados de estadísticas. Las etiquetas llegan
mezcladas en español/alemán (TM sirve parte del bloque en alemán
incluso en .es), así que el mapa acepta ambas variantes.

Como complemento de f24h cubre más ligas de cola larga (divisiones
inferiores, femenino, reservas) y el mismo dato en competiciones
grandes (redundancia útil cuando f24h no lista el fixture).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from html import unescape
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    MatchResult,
    MatchStats,
    count_provider_call,
    is_missed,
    mark_missed,
    match_score,
    miss_is_provisional,
)

logger = get_logger("app.results.transfermarkt")

_BASE = "https://www.transfermarkt.es"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Tolerancia de fecha: el tipster puede anotar el día en su zona horaria
# y TM lista por fecha local de la competición.
_DATE_TOLERANCE_DAYS = 1
_MIN_TEAM_SIMILARITY = 0.6

# FlareSolverr: TM protege con Cloudflare; desde la IP de la VM el
# fetch directo suele recibir challenge. Mismos límites que
# gemini_research (un solve cuesta segundos de CPU del núcleo libre).
_FLARESOLVERR_MAX_MS = 60000
_FLARESOLVERR_TIMEOUT = 75.0

# Fila del listado de livescore: id del informe, equipos y resultado.
_ROW = re.compile(r'<tr id="(\d+)" class="begegnungZeile">(.*?)</tr>', re.S)
_HOME_CELL = re.compile(r'verein-heim">(.*?)</td>', re.S)
_AWAY_CELL = re.compile(r'verein-gast">(.*?)</td>', re.S)
_IMG_TITLE = re.compile(r'<img[^>]*(?:title|alt)="([^"]+)"')
_SCORE_FINISHED = re.compile(r'matchresult finished">(\d+):(\d+)')
_SCORE_ANY = re.compile(r'matchresult[^"]*">(-?\d+):(-?\d+)')
_TAG = re.compile(r"<[^>]+>")

# Bloque de la tabla de stats: etiqueta + valores local/visitante.
_STAT_BLOCK = re.compile(
    r'unterueberschrift">\s*([^<]+?)\s*</div>\s*'
    r'<div class="sb-statistik">(.*?)</ul>',
    re.S,
)
_STAT_VALUE = re.compile(r'sb-statistik-zahl"[^>]*>\s*([\d.]+)\s*<')

# Etiqueta TM (español/alemán/inglés, en minúsculas) -> clave canónica
# del verificador (mismas que `/fixtures/statistics` de API-Football).
_STAT_NAME_MAP = {
    "córneres": "Corner Kicks",
    "córners": "Corner Kicks",
    "corneres": "Corner Kicks",
    "corners": "Corner Kicks",
    "ecken": "Corner Kicks",
    "disparos totales": "Total Shots",
    "tiros totales": "Total Shots",
    "torschüsse": "Total Shots",
    "disparos a puerta": "Shots on Goal",
    "tiros a puerta": "Shots on Goal",
    "schüsse aufs tor": "Shots on Goal",
    "faltas": "Fouls",
    "fouls": "Fouls",
    "fueras de juego": "Offsides",
    "fuera de juego": "Offsides",
    "abseits": "Offsides",
    "posesión": "Ball Possession",
    "posesión del balón": "Ball Possession",
    "ballbesitz": "Ball Possession",
    "possession": "Ball Possession",
    "paradas": "Goalkeeper Saves",
    "paraden": "Goalkeeper Saves",
    "tarjetas amarillas": "Yellow Cards",
    "gelbe karten": "Yellow Cards",
    "yellow cards": "Yellow Cards",
    "tarjetas rojas": "Red Cards",
    "rote karten": "Red Cards",
    "red cards": "Red Cards",
}

# "Disparos fuera" (fuera de la portería) permite derivar tiros a
# puerta = totales - fuera cuando TM no da la fila explícita.
_SHOTS_OFF_LABELS = {"disparos fuera", "tiros fuera", "schüsse vorbei"}

# Caché en memoria de los listados por fecha (una pasada del verifier
# consulta varios picks del mismo día). TTL corto — la página se
# actualiza en vivo.
_LIST_TTL = 300  # 5 min
_list_cache: dict[str, tuple[float, str]] = {}


def _team_name(cell_html: str) -> str:
    """Nombre del equipo: `title`/`alt` del escudo (nombre completo) o
    texto del enlace si no hay imagen."""
    m = _IMG_TITLE.search(cell_html)
    if m:
        return m.group(1).strip()
    return _TAG.sub("", cell_html).strip()


class TransfermarktProvider:
    """Fútbol de cola larga vía livescore por fecha + página de stats.

    Sin key ni cuota; cada listado por fecha va cacheado 5 min y la
    página de stats solo se descarga para mercados de estadísticas.
    """

    NAME = "transfermarkt"
    SUPPORTED_SPORTS = frozenset({"futbol"})

    async def _fetch(self, url: str) -> Optional[str]:
        """HTML de la URL, con caché TTL para los listados por fecha."""
        now = time.monotonic()
        cached = _list_cache.get(url)
        if cached and now - cached[0] < _LIST_TTL:
            return cached[1]
        try:
            count_provider_call(self.NAME)
            async with httpx.AsyncClient(
                timeout=25, headers={"User-Agent": _UA}
            ) as client:
                resp = await client.get(url, follow_redirects=True)
            html = resp.text
            ok = resp.status_code == 200 and "__cf_chl" not in html and len(html) > 3000
            if not ok:
                html = await self._fetch_via_flaresolverr(url)
            if html is None:
                return None
            _list_cache[url] = (now, html)
            return html
        except httpx.HTTPError as exc:
            logger.debug("[TM] %s fallo de red: %s", url, exc)
            html = await self._fetch_via_flaresolverr(url)
            if html:
                _list_cache[url] = (now, html)
            return html

    async def _fetch_via_flaresolverr(self, url: str) -> Optional[str]:
        """HTML renderizado por el FlareSolverr de la VM.

        Mismo patrón que `gemini_research._fetch_via_flaresolverr`: solo
        se usa cuando el fetch directo devuelve challenge de Cloudflare
        o falla — un solve cuesta segundos de CPU del núcleo libre.
        """
        base = getattr(get_settings(), "flaresolverr_url", None)
        if not base:
            return None
        try:
            count_provider_call(self.NAME)
            async with httpx.AsyncClient(timeout=_FLARESOLVERR_TIMEOUT) as client:
                resp = await client.post(
                    f"{base.rstrip('/')}/v1",
                    json={
                        "cmd": "request.get",
                        "url": url,
                        "maxTimeout": _FLARESOLVERR_MAX_MS,
                    },
                )
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("[TM] FlareSolverr %s falló: %r", url, exc)
            return None
        solution = data.get("solution") or {}
        html = solution.get("response") or ""
        if data.get("status") != "ok" or len(html) < 3000:
            return None
        return html

    def _iter_board(self, html: str):
        """(match_id, local, visitante, marcador|None) de cada fila."""
        for m in _ROW.finditer(html):
            row = m.group(2)
            home_cell = _HOME_CELL.search(row)
            away_cell = _AWAY_CELL.search(row)
            if not home_cell or not away_cell:
                continue
            score_m = _SCORE_FINISHED.search(row)
            score = (int(score_m.group(1)), int(score_m.group(2))) if score_m else None
            yield (
                int(m.group(1)),
                _team_name(home_cell.group(1)),
                _team_name(away_cell.group(1)),
                score,
            )

    async def _find_fixture(
        self, date: datetime, team_hint: str
    ) -> Optional[tuple[int, str, str, Optional[tuple[int, int]]]]:
        """Fila del livescore que casa el hint en fecha ± tolerancia.

        Devuelve (match_id, local, visitante, marcador|None); el
        marcador es None si el partido aún no está 'finished'.
        """
        hint = team_hint.strip()
        candidates = {hint.lower()}
        day0 = date.replace(hour=0, minute=0, second=0, microsecond=0)

        best: Optional[tuple[int, str, str, Optional[tuple[int, int]]]] = None
        best_score = 0.0
        for offset in range(-_DATE_TOLERANCE_DAYS, _DATE_TOLERANCE_DAYS + 1):
            day = day0 + timedelta(days=offset)
            url = f"{_BASE}/live/index?datum={day.strftime('%Y-%m-%d')}"
            html = await self._fetch(url)
            if html is None:
                continue
            for match_id, home, away, score in self._iter_board(html):
                sim = max(match_score(h, home, away) for h in candidates)
                if sim > best_score:
                    best_score = sim
                    best = (match_id, home, away, score)
        if best is None or best_score < _MIN_TEAM_SIMILARITY:
            return None
        return best

    async def _finished_fixture(
        self, date: datetime, team_hint: str
    ) -> Optional[tuple[int, MatchResult]]:
        """(match_id, MatchResult) del fixture terminado que casa."""
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

        found = await self._find_fixture(date, team_hint)
        if found is None:
            mark_missed(miss_key)
            return None
        match_id, home, away, score = found
        if score is None:
            return None  # aún no jugado o en vivo: reintentable, no miss
        return match_id, MatchResult(
            home_team=home, away_team=away, home_score=score[0], away_score=score[1]
        )

    def _parse_stats(
        self, html: str, home_team: str, away_team: str
    ) -> Optional[MatchStats]:
        """Tabla de estadísticas -> MatchStats con claves canónicas."""
        values: dict[str, tuple[int, int]] = {}
        shots_total: Optional[tuple[int, int]] = None
        shots_off: Optional[tuple[int, int]] = None
        for m in _STAT_BLOCK.finditer(html):
            label = unescape(m.group(1).strip().lower())
            nums = _STAT_VALUE.findall(m.group(2))
            if len(nums) < 2:
                continue
            pair = (int(float(nums[0])), int(float(nums[1])))
            if label in _SHOTS_OFF_LABELS:
                shots_off = pair
                continue
            key = _STAT_NAME_MAP.get(label)
            if key is None:
                continue
            if key == "Total Shots":
                shots_total = pair
            values[key] = pair
        # Tiros a puerta derivados = totales - fuera, solo si la fila
        # explícita no venía (aritmética exacta, no inferencia).
        if (
            "Shots on Goal" not in values
            and shots_total is not None
            and shots_off is not None
        ):
            values["Shots on Goal"] = (
                max(shots_total[0] - shots_off[0], 0),
                max(shots_total[1] - shots_off[1], 0),
            )
        if not values:
            return None
        return MatchStats(home_team=home_team, away_team=away_team, values=values)

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        found = await self._finished_fixture(date, team_hint)
        return found[1] if found else None

    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        found = await self._finished_fixture(date, team_hint)
        if found is None:
            return None
        match_id, match = found
        html = await self._fetch(f"{_BASE}/statistik/index/spielbericht/{match_id}")
        if html is None:
            return None
        return self._parse_stats(html, match.home_team, match.away_team)
