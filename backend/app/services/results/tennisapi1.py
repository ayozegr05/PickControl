"""Tercer proveedor de tenis: "TennisApi" en RapidAPI (datos de Sofascore).

Host: tennisapi1.p.rapidapi.com. Cubre ATP/WTA/Challenger/ITF/Copas —
misma cobertura que Sofascore. Se consulta SOLO cuando TheSportsDB y
"Tennis API - ATP WTA ITF" no encontraron el partido (su plan gratuito
son ~50 req/día, compartidos con cualquier otra API RapidAPI de la
misma cuenta solo a nivel de key — la cuota es por suscripción).

El feed plano por fecha (`/api/tennis/events/{d}/{m}/{y}`) está
deprecado en origen (devuelve 204). La vía soportada es por categoría:

    GET /api/tennis/category/{id}/events/{day}/{month}/{year}

IDs de categoría (descubiertos vía `/api/tennis/search`):
    3 ATP · 6 WTA · 72 Challenger · 785 ITF Men · 213 ITF Women
    871 WTA 125 · 76 Davis Cup · 1705 United Cup · 79 Exhibition

Para no quemar la cuota:
- Las categorías se recorren en orden de probabilidad y se sale en
  cuanto aparece el partido (lo normal: 1-3 llamadas por pick).
- Cada respuesta se cachea por (categoría, fecha): otros picks del
  mismo día reutilizan categorías ya descargadas.
- Si un partido no está en ninguna categoría, el fallo se recuerda
  ~15 días en disco (`is_missed`/`mark_missed` de base.py): los
  resultados históricos no aparecen tarde y reintentar solo quema cuota.
- Solo se consulta la fecha exacta del pick (la tolerancia ±1 día ya la
  cubre el nivel anterior, que busca en el historial del jugador): así
  el peor caso son 9 llamadas por partido no encontrado, no 27.
- Un 403/429 marca el proveedor como sin cuota hasta mañana (base.py).

Respuesta Sofascore por evento: `homeTeam`/`awayTeam` (nombre),
`homeScore.current`/`awayScore.current` = sets ganados y
`status.type` = "finished" cuando el partido terminó. Los dobles se
descartan (equipos "A / B") para no falsear picks de individuales.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.api_tennis import _player_similar
from app.services.results.base import (
    MatchResult,
    is_missed,
    is_rate_limited,
    mark_missed,
    mark_rate_limited,
    rate_limit_from,
)

logger = get_logger("app.results.tennisapi1")

_PROVIDER_NAME = "tennisapi1"
_MIN_PLAYER_SIMILARITY = 0.6
# Solo fecha exacta: la tolerancia ±1 día ya la da el nivel anterior
# (RapidApiTennisProvider busca en el historial del jugador). Limitarlo
# a un día acota el peor caso a len(_CATEGORIES) llamadas por pick.
_DATE_OFFSETS = (0,)
# Ordenadas por probabilidad de que un tipster publique esos partidos.
_CATEGORIES = (3, 6, 72, 785, 213, 871, 76, 1705, 79)


def _parse_event(event: dict) -> Optional[MatchResult]:
    """Evento Sofascore -> MatchResult. None si no terminó o es dobles.

    `homeScore.periodN`/`awayScore.periodN` llevan los juegos de cada
    set — se guardan en `MatchResult.sets` para mercados de juegos.
    """
    status = (event.get("status") or {}).get("type")
    home = (event.get("homeTeam") or {}).get("name") or ""
    away = (event.get("awayTeam") or {}).get("name") or ""
    if not home or not away or "/" in home or "/" in away:
        return None
    if status in ("retired", "walkover"):
        # Retirada/walkover: el partido no terminó por la vía normal.
        # Marcador parcial aproximado; el verificador devuelve anulada.
        try:
            ret_home = int((event.get("homeScore") or {})["current"])
            ret_away = int((event.get("awayScore") or {})["current"])
        except (KeyError, TypeError, ValueError):
            ret_home = ret_away = 0
        return MatchResult(
            home_team=home,
            away_team=away,
            home_score=ret_home,
            away_score=ret_away,
            status=status,
        )
    if status != "finished":
        return None
    try:
        home_sets = int((event.get("homeScore") or {})["current"])
        away_sets = int((event.get("awayScore") or {})["current"])
    except (KeyError, TypeError, ValueError):
        return None

    home_score = event.get("homeScore") or {}
    away_score = event.get("awayScore") or {}
    sets: list[tuple[int, int]] = []
    for period in range(1, home_sets + away_sets + 1):
        try:
            sets.append(
                (int(home_score[f"period{period}"]), int(away_score[f"period{period}"]))
            )
        except (KeyError, TypeError, ValueError):
            break

    return MatchResult(
        home_team=home,
        away_team=away,
        home_score=home_sets,
        away_score=away_sets,
        sets=sets or None,
    )


class TennisApi1Provider:
    """Consulta tennisapi1 (Sofascore) por categoría y fecha."""

    SUPPORTED_SPORTS = frozenset({"tenis"})

    def __init__(self, api_key: str, api_host: str) -> None:
        self._api_key = api_key
        self._api_host = api_host
        # Eventos por (categoría, fecha): varios picks del mismo día
        # reutilizan las categorías ya descargadas.
        self._events_cache: dict[tuple[int, str], list] = {}

    async def _fetch_events(
        self, client: httpx.AsyncClient, category_id: int, day: datetime
    ) -> list:
        cache_key = (category_id, day.strftime("%Y-%m-%d"))
        if cache_key in self._events_cache:
            return self._events_cache[cache_key]
        url = (
            f"https://{self._api_host}/api/tennis/category/{category_id}"
            f"/events/{day.day}/{day.month}/{day.year}"
        )
        try:
            response = await client.get(
                url,
                headers={
                    "X-RapidAPI-Key": self._api_key,
                    "X-RapidAPI-Host": self._api_host,
                },
            )
            response.raise_for_status()
            events = response.json().get("events") or []
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(_PROVIDER_NAME)
                logger.warning("[TennisApi1] Cuota agotada; se omite hasta mañana")
            else:
                logger.warning("[TennisApi1] Error de API (%s): %s", cache_key, exc)
            events = []
        # Los errores también se cachean dentro de la instancia: un
        # fallo transitorio no debe reintentarse con cada pick del ciclo.
        self._events_cache[cache_key] = events
        return events

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        if is_rate_limited(_PROVIDER_NAME):
            return None
        miss_key = (
            f"{_PROVIDER_NAME}|{date.strftime('%Y-%m-%d')}|{team_hint.strip().lower()}"
        )
        if is_missed(miss_key):
            return None

        best_match: Optional[MatchResult] = None
        best_score = 0.0

        async with httpx.AsyncClient(timeout=15) as client:
            for offset in _DATE_OFFSETS:
                day = date + timedelta(days=offset)
                for category_id in _CATEGORIES:
                    if is_rate_limited(_PROVIDER_NAME):
                        return None
                    for event in await self._fetch_events(client, category_id, day):
                        match = _parse_event(event)
                        if not match:
                            continue
                        score = max(
                            _player_similar(team_hint, match.home_team),
                            _player_similar(team_hint, match.away_team),
                        )
                        if score > best_score:
                            best_score = score
                            best_match = match
                    if best_match and best_score >= _MIN_PLAYER_SIMILARITY:
                        return best_match  # early-exit: no seguir gastando

        if not best_match or best_score < _MIN_PLAYER_SIMILARITY:
            mark_missed(miss_key)
            return None
        return best_match
