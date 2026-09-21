"""Proveedor de resultados de fútbol vía footapi7 (RapidAPI).

Es el dato de Sofascore servido por RapidAPI: `api.sofascore.com` bloquea
requests de servidor con Cloudflare, así que se accede por el mirror
`footapi7.p.rapidapi.com` (mismo backend y mismo espacio de ids que
allsportsapi2/tennisapi1). Plan BASIC gratis con cuota diaria propia.

Por qué existe: API-Football solo consulta fechas dentro de la ventana
±1 día del plan gratis; los mercados de estadísticas (córners, tarjetas,
tiros) y los marcadores de ligas menores que se salen de esa ventana se
perdían para siempre. footapi7 no tiene ventana: un partido terminado
sigue devolviendo marcador y estadísticas semanas después.

Cadena de fútbol: football-data -> api-football -> footapi7.

Rutas (verificadas en vivo 2026-09-20 con plan BASIC):

- `/api/search/{equipo}`                    -> id de equipo
- `/api/team/{id}/matches/previous/{page}`  -> ~30 partidos jugados
- `/api/match/{id}/statistics`              -> stats por periodo
                                               (ALL / 1ST / 2ND)
- `/api/match/{id}/incidents`               -> goles/tarjetas con
                                               jugador y minuto
- `/api/match/{id}/lineups`                 -> alineaciones + stats
                                               por jugador

Las estadísticas vienen como `statistics[].groups[].statisticsItems[]`
con `name`, `home`, `away` (strings; "2", "42%", "23/37 (62%)"). Se
traducen a las claves canónicas de API-Football que el verificador ya
consume ("Corner Kicks", "Yellow Cards", "Total Shots"...), así la
lógica de resolución de mercados no cambia.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from app.core.logging import get_logger
from app.services.results.api_tennis import _pair_similar
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    MatchEvents,
    MatchPlayers,
    MatchResult,
    MatchStats,
    is_missed,
    is_rate_limited,
    mark_missed,
    mark_rate_limited,
    match_score,
    miss_is_provisional,
    rate_limit_from,
)
from app.services.results.response_cache import (
    event_list_covers,
    get_entity_id,
    get_event_list,
    set_entity_id,
    set_event_list,
)

logger = get_logger("app.results.footapi")

_MIN_SIMILARITY = 0.6
# El pick puede llevar el día de publicación, no el del partido.
_DATE_TOLERANCE = timedelta(days=1)
# Páginas de `matches/previous` a revisar (~30 eventos cada una). Los
# pendientes son <=14 días, con 2 páginas sobra para equipos que juegan
# varias veces por semana.
_MAX_PAGES = 2

# Nombre de stat de Sofascore -> clave canónica que espera el
# verificador (mismas que devuelve API-Football `/fixtures/statistics`).
_STAT_NAME_MAP = {
    "Corner kicks": "Corner Kicks",
    "Yellow cards": "Yellow Cards",
    "Red cards": "Red Cards",
    "Total shots": "Total Shots",
    "Shots on target": "Shots on Goal",
    "Fouls": "Fouls",
    "Offsides": "Offsides",
}


def _stat_int(value) -> Optional[int]:
    """Entero de un valor de stat ("2", 2); descarta "42%" y "23/37"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


# Estadística de jugador de Sofascore (`/lineups` -> statistics) ->
# clave aplanada canónica de API-Football que espera el verificador
# para props de jugador ("X más de 1.5 tiros a puerta").
_PLAYER_STAT_MAP = {
    "goals": "goals.total",
    "goalAssist": "goals.assists",
    "shotsOnTarget": "shots.on",
    "fouls": "fouls.committed",
    "keyPass": "passes.key",
    "tackles": "tackles.total",
}
# Sofascore desglosa los tiros del jugador en tres contadores; el total
# canónico ("shots.total") es la suma.
_PLAYER_SHOT_PARTS = ("shotsOnTarget", "shotsOffTarget", "blockedScoringAttempt")


def _player_stats_flat(stats: dict) -> dict[str, int]:
    """Estadísticas de jugador de /lineups aplanadas a las claves
    canónicas que consume `_resolve_player_prop` del verificador."""
    flat: dict[str, int] = {}
    for sofa_key, canonical in _PLAYER_STAT_MAP.items():
        value = _stat_int(stats.get(sofa_key))
        if value is not None:
            flat[canonical] = value
    shot_parts = [_stat_int(stats.get(k)) for k in _PLAYER_SHOT_PARTS]
    if any(v is not None for v in shot_parts):
        flat["shots.total"] = sum(v or 0 for v in shot_parts)
    # Segunda amarilla cuenta como amarilla y como roja a la vez
    # (misma regla que una expulsión por doble amarilla en las casas).
    for canonical, keys in (
        ("cards.yellow", ("yellowCards", "yellowRedCards")),
        ("cards.red", ("redCards", "yellowRedCards")),
    ):
        if any(k in stats for k in keys):
            flat[canonical] = sum(_stat_int(stats.get(k)) or 0 for k in keys)
    return flat


class FootApiStatsProvider:
    """Resultados y estadísticas de fútbol vía footapi7 (RapidAPI).

    Los attrs `_SPORT_*` y `_ENTITY_NS` parametrizan la búsqueda para que
    una subclase pueda reusar toda la lógica con otro deporte/host
    (p. ej. `SofascoreBasketballProvider` sobre allsportsapi2).
    """

    NAME = "footapi7"
    SUPPORTED_SPORTS = frozenset({"futbol"})
    _SPORT_SLUG = "football"  # filtro `entity.sport.slug` en /api/search
    _SPORT_TAG = "futbol"  # etiqueta en las claves de miss
    _ENTITY_NS: Optional[str] = None  # namespace de caché; None -> NAME

    @property
    def _entity_ns(self) -> str:
        return self._ENTITY_NS or self.NAME

    def __init__(self, api_key: str, api_host: str) -> None:
        self._api_key = api_key
        self._api_host = api_host
        self._search_cache: dict[str, Optional[list]] = {}
        self._events_cache: dict[int, Optional[list]] = {}
        self._stats_cache: dict[int, Optional[dict]] = {}
        self._incidents_cache: dict[int, Optional[dict]] = {}
        self._lineups_cache: dict[int, Optional[dict]] = {}

    # --- HTTP ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._api_host,
        }

    async def _get_json(self, client: httpx.AsyncClient, path: str) -> Optional[dict]:
        """GET con gestión de cuota unificada: 403/429 marca el proveedor
        sin cuota hasta mañana (mismo patrón que el resto de providers)."""
        try:
            response = await client.get(
                f"https://{self._api_host}{path}", headers=self._headers()
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(self.NAME)
                logger.warning("[FOOTAPI7] Cuota agotada; se omite hasta mañana")
            else:
                logger.warning("[FOOTAPI7] Error de API (%s): %s", path, exc)
            return None

    # --- Resolución de evento --------------------------------------------

    async def _search(self, client: httpx.AsyncClient, name: str) -> Optional[list]:
        """Entidades candidatas por nombre; None si la llamada falló."""
        key = name.strip().lower()
        if key in self._search_cache:
            return self._search_cache[key]
        data = await self._get_json(client, f"/api/search/{quote(key)}")
        results = None if data is None else (data.get("results") or [])
        self._search_cache[key] = results
        return results

    async def _previous_events(
        self, client: httpx.AsyncClient, team_id: int, need_date: datetime
    ) -> Optional[list]:
        """Partidos ya jugados del equipo (todas las páginas revisadas);
        None si alguna llamada falló.

        La lista se persiste en `provider_cache.json`: si cubre la fecha
        del pick (`event_list_covers`) se reutiliza sin llamar a la API;
        si el partido pudo jugarse tras la captura se refetchea y se
        sobrescribe (el fetch nuevo siempre incluye el historial viejo).
        """
        if team_id in self._events_cache:
            return self._events_cache[team_id]
        cache_key = f"{self._entity_ns}|{team_id}"
        cached = get_event_list(cache_key)
        if cached is not None and event_list_covers(cached, need_date):
            self._events_cache[team_id] = cached["events"]
            return cached["events"]
        events: list = []
        for page in range(_MAX_PAGES):
            data = await self._get_json(
                client, f"/api/team/{team_id}/matches/previous/{page}"
            )
            if data is None:
                self._events_cache[team_id] = None
                return None
            batch = data.get("events") or []
            events.extend(batch)
            if len(batch) < 30:  # última página
                break
        self._events_cache[team_id] = events
        if events:
            set_event_list(cache_key, events)
        return events

    def _team_id(self, name: str, results: list) -> Optional[int]:
        """Id del equipo de fútbol más parecido al nombre buscado."""
        best_id: Optional[int] = None
        best_score = 0.0
        for res in results:
            entity = res.get("entity") or {}
            if (entity.get("sport") or {}).get("slug") != self._SPORT_SLUG:
                continue
            score = _pair_similar(name, entity.get("name") or "")
            if score > best_score:
                best_score = score
                best_id = entity.get("id")
        return best_id if best_score >= _MIN_SIMILARITY else None

    async def _find_event(self, date: datetime, team_hint: str) -> Optional[dict]:
        """El evento terminado que mejor casa con el hint en la fecha.

        `team_hint` es el cruce ("Alavés - Valencia"); se busca por la
        primera parte y se puntúa contra ambos equipos del evento.
        """
        if is_rate_limited(self.NAME):
            return None
        hint = team_hint.strip()
        if not hint:
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{self.NAME}|{self._SPORT_TAG}|{date.strftime('%Y-%m-%d')}|{hint.lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        name = (
            re.split(r"[/+&]|\s+-\s+|\s+vs\.?\s+", hint, maxsplit=1)[0].strip() or hint
        )

        async with httpx.AsyncClient(timeout=15) as client:
            team_id = get_entity_id(self._entity_ns, name)
            if team_id is None:
                results = await self._search(client, name)
                if results is None:
                    return None  # error de API: no se marca missed
                team_id = self._team_id(name, results)
                if team_id is None:
                    mark_missed(miss_key)
                    return None
                set_entity_id(self._entity_ns, name, team_id)
            events = await self._previous_events(client, team_id, date)
            if events is None:
                return None

        best_score = 0.0
        best_event: Optional[dict] = None
        for event in events:
            ts = event.get("startTimestamp")
            if ts:
                played = datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
                if abs(played - date) > _DATE_TOLERANCE:
                    continue
            score = match_score(
                hint,
                (event.get("homeTeam") or {}).get("name") or "",
                (event.get("awayTeam") or {}).get("name") or "",
            )
            if score > best_score:
                best_score = score
                best_event = event

        if best_event is None or best_score < _MIN_SIMILARITY:
            mark_missed(miss_key)
            return None
        return best_event

    # --- Interfaz ResultsProvider -----------------------------------------

    def _ht_scores(self, event: dict) -> tuple[Optional[int], Optional[int]]:
        """Marcador al descanso. En fútbol Sofascore lo da en `period1`."""
        return (
            (event.get("homeScore") or {}).get("period1"),
            (event.get("awayScore") or {}).get("period1"),
        )

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Marcador final del partido (cubre ligas que football-data no
        incluye gratis y fechas fuera de la ventana de API-Football)."""
        event = await self._find_event(date, team_hint)
        if event is None:
            return None
        if (event.get("status") or {}).get("type") != "finished":
            return None
        home_score = (event.get("homeScore") or {}).get("current")
        away_score = (event.get("awayScore") or {}).get("current")
        if home_score is None or away_score is None:
            return None
        ht_home, ht_away = self._ht_scores(event)
        return MatchResult(
            home_team=(event.get("homeTeam") or {}).get("name") or "",
            away_team=(event.get("awayTeam") or {}).get("name") or "",
            home_score=home_score,
            away_score=away_score,
            ht_home_score=ht_home,
            ht_away_score=ht_away,
        )

    async def _stats_for_period(
        self, date: datetime, team_hint: str, period: str
    ) -> Optional[MatchStats]:
        """Estadísticas de un periodo ("ALL", "1ST").

        `MatchStats.values` usa las claves canónicas de API-Football, así
        el verificador resuelve córners/tarjetas/tiros sin cambios.
        """
        event = await self._finished_event(date, team_hint)
        if event is None:
            return None
        event_id = event["id"]

        async with httpx.AsyncClient(timeout=15) as client:
            if event_id not in self._stats_cache:
                self._stats_cache[event_id] = await self._get_json(
                    client, f"/api/match/{event_id}/statistics"
                )
            data = self._stats_cache[event_id]
        if data is None:
            return None

        values: dict[str, tuple[int, int]] = {}
        for period_stats in data.get("statistics") or []:
            if period_stats.get("period") != period:
                continue
            for group in period_stats.get("groups") or []:
                for item in group.get("statisticsItems") or []:
                    key = _STAT_NAME_MAP.get(item.get("name") or "")
                    if key is None or key in values:
                        continue
                    home = _stat_int(item.get("homeValue", item.get("home")))
                    away = _stat_int(item.get("awayValue", item.get("away")))
                    if home is not None and away is not None:
                        values[key] = (home, away)

        if not values:
            return None
        return MatchStats(
            home_team=(event.get("homeTeam") or {}).get("name") or "",
            away_team=(event.get("awayTeam") or {}).get("name") or "",
            values=values,
        )

    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        """Estadísticas del partido completo (periodo ALL)."""
        return await self._stats_for_period(date, team_hint, "ALL")

    async def find_match_stats_1h(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        """Estadísticas de la PRIMERA parte (periodo 1ST): habilita
        mercados de córners/tarjetas/tiros al descanso."""
        return await self._stats_for_period(date, team_hint, "1ST")

    # --- Jugadores: incidents + lineups ------------------------------------

    async def _finished_event(self, date: datetime, team_hint: str) -> Optional[dict]:
        """El evento terminado que casa con el hint (o None)."""
        event = await self._find_event(date, team_hint)
        if event is None or event.get("id") is None:
            return None
        if (event.get("status") or {}).get("type") != "finished":
            return None
        return event

    async def find_match_events(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchEvents]:
        """Goles/asistencias/tarjetas del partido vía
        `/api/match/{id}/incidents`.

        Desbloquea los mercados de jugador ("X marca", "X marca o
        asiste", "X recibe tarjeta") fuera de la ventana ±1 día del
        plan gratis de API-Football — el incidente queda disponible
        semanas después.
        """
        event = await self._finished_event(date, team_hint)
        if event is None:
            return None
        event_id = event["id"]

        async with httpx.AsyncClient(timeout=15) as client:
            if event_id not in self._incidents_cache:
                self._incidents_cache[event_id] = await self._get_json(
                    client, f"/api/match/{event_id}/incidents"
                )
            data = self._incidents_cache[event_id]
        if data is None:
            return None

        scorers: list[str] = []
        assisters: list[str] = []
        booked: list[str] = []
        participants: list[str] = []

        def add(lst: list[str], name: Optional[str]) -> None:
            if name and name not in lst:
                lst.append(name)

        for incident in data.get("incidents") or []:
            player = (incident.get("player") or {}).get("name")
            assist = (incident.get("assist1") or {}).get("name")
            sub_in = (incident.get("playerIn") or {}).get("name")
            sub_out = (incident.get("playerOut") or {}).get("name")
            for name in (player, assist, sub_in, sub_out):
                add(participants, name)
            itype = incident.get("incidentType")
            if itype == "goal":
                # Propia puerta no cuenta como gol del jugador (misma
                # regla que en API-Football); el penalti marcado sí.
                if incident.get("incidentClass") == "ownGoal":
                    continue
                add(scorers, player)
                add(assisters, assist)
            elif itype == "card":
                add(booked, player)

        if not participants:
            return None
        return MatchEvents(
            home_team=(event.get("homeTeam") or {}).get("name") or "",
            away_team=(event.get("awayTeam") or {}).get("name") or "",
            scorers=scorers,
            assisters=assisters,
            booked=booked,
            participants=participants,
        )

    async def find_match_players(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchPlayers]:
        """Jugadores que disputaron minutos, vía
        `/api/match/{id}/lineups`.

        Sirve para distinguir "jugó sin acertar el mercado" (fallo) de
        "no jugó" (anulada — la casa devuelve) y para props de jugador
        con número ("X más de 1.5 tiros a puerta"): `statistics` de la
        alineación se aplana a las claves canónicas de API-Football.
        """
        event = await self._finished_event(date, team_hint)
        if event is None:
            return None
        event_id = event["id"]

        async with httpx.AsyncClient(timeout=15) as client:
            if event_id not in self._lineups_cache:
                self._lineups_cache[event_id] = await self._get_json(
                    client, f"/api/match/{event_id}/lineups"
                )
            data = self._lineups_cache[event_id]
        if data is None:
            return None

        played: list[str] = []
        player_stats: dict[str, dict[str, int]] = {}
        for side in ("home", "away"):
            for entry in (data.get(side) or {}).get("players") or []:
                name = (entry.get("player") or {}).get("name")
                if not name:
                    continue
                stats = entry.get("statistics") or {}
                flat = _player_stats_flat(stats)
                if flat:
                    player_stats[name] = flat
                minutes = _stat_int(stats.get("minutesPlayed"))
                if minutes is None:
                    # Sin dato de minutos: el titular consta como que
                    # jugó; el suplente no se puede confirmar.
                    if not entry.get("substitute") and name not in played:
                        played.append(name)
                elif minutes > 0 and name not in played:
                    played.append(name)

        if not played:
            return None
        return MatchPlayers(
            home_team=(event.get("homeTeam") or {}).get("name") or "",
            away_team=(event.get("awayTeam") or {}).get("name") or "",
            played=played,
            stats=player_stats,
        )
