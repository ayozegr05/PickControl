"""Proveedores gratuitos de resultados y estadísticas vía ESPN.

Usan el endpoint JSON interno de espn.com (`site.api.espn.com`): no
documentado pero estable desde hace años, sin API key ni cuota — por eso
van PRIMERO en la cascada de cada deporte, descargando a los providers
de cuota (football-data 10 req/min, mirrors RapidAPI 50-100/día).

Cobertura por deporte:

- Fútbol (`EspnProvider`): ~50 ligas (`_LEAGUES_FUTBOL`: top, segundas,
  copas nacionales, femenino, amistosos, internacionales, América).
  `scoreboard` por liga-día -> marcador FT/HT + stats de equipo
  (córners, tarjetas, tiros, faltas). `summary?event=` -> roster con
  stats por jugador (goles, asistencias, tiros, faltas, tarjetas,
  `appearances`) -> habilita `find_match_events`/`find_match_players`
  y por tanto TODAS las props de jugador sin tocar footapi7.
- Tenis (`EspnTennisProvider`): ATP + WTA (individuales y dobles).
  `scoreboard` agrupa por torneo -> cada competition es un partido con
  `linescores` por set. Cubre ganador, sets y mercados de juegos.
  Sin stats de saque (aces/dobles faltas): ESPN no las publica.
- Baloncesto (`EspnBasketballProvider`): NBA, WNBA, NBL, FIBA — ESPN no
  cubre ACB ni Euroliga. Marcador final + descanso (Q1+Q2).

Salud: ESPN puede cambiar el formato sin aviso. Si N llamadas
consecutivas fallan (HTTP o payload inesperado), el provider se aparca
el día con `mark_rate_limited` — eso dispara el push a admins igual que
una cuota agotada.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.api_tennis import _pair_similar
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    MatchEvents,
    MatchPlayers,
    MatchResult,
    MatchState,
    MatchStats,
    count_provider_call,
    is_missed,
    is_rate_limited,
    mark_missed,
    mark_rate_limited,
    match_score,
    miss_is_provisional,
)

logger = get_logger("app.results.espn")

_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"
_MIN_TEAM_SIMILARITY = 0.6
# El pick puede llevar el día de publicación, no el del partido.
_DATE_WINDOW = timedelta(days=1)
# Fallos consecutivos (HTTP o formato roto) que declaran la API caída.
_MAX_CONSECUTIVE_FAILURES = 5

# Ligas consultadas, ordenadas por relevancia para los tipsters que se
# siguen. ESPN no expone un scoreboard global de fútbol: hay que iterar
# por liga. Lista extraída de sports.core.api.espn.com/.../leagues
# (219 disponibles; estas son las relevantes para los picks que entran).
_LEAGUES_FUTBOL = (
    # España
    "esp.1",
    "esp.2",
    "esp.copa_del_rey",
    "esp.super_cup",
    "esp.w.1",
    # Inglaterra
    "eng.1",
    "eng.2",
    "eng.fa",
    "eng.league_cup",
    "eng.w.1",
    # Grandes ligas + segundas
    "ita.1",
    "ita.2",
    "ger.1",
    "ger.2",
    "fra.1",
    # Copas nacionales
    "ita.coppa_italia",
    "ger.dfb_pokal",
    "fra.coupe_de_france",
    # Resto de Europa
    "ned.1",
    "por.1",
    "tur.1",
    "bel.1",
    "sco.1",
    "gre.1",
    "den.1",
    "ned.cup",
    "por.taca.portugal",
    # UEFA + FIFA
    "uefa.champions",
    "uefa.europa",
    "uefa.europa.conf",
    "uefa.wchampions",
    "uefa.euro",
    "uefa.nations",
    "fifa.world",
    "fifa.wwc",
    "fifa.cwc",
    "fifa.olympics",
    "fifa.friendly",
    "club.friendly",
    # América
    "ksa.1",
    "arg.1",
    "arg.2",
    "bra.1",
    "bra.2",
    "usa.1",
    "mex.1",
    "chi.1",
    "ecu.1",
    "conmebol.libertadores",
    "conmebol.sudamericana",
    "concacaf.champions",
    "concacaf.leagues.cup",
)
_LEAGUES_TENIS = ("atp", "wta")
# ESPN solo cubre basket americano/FIBA — no ACB ni Euroliga.
_LEAGUES_BASKET = ("nba", "wnba", "nbl", "fiba")

# `name` de la estadística ESPN -> clave canónica que ya consume el
# verificador (mismas que devuelve API-Football `/fixtures/statistics`).
_STAT_NAME_MAP = {
    "wonCorners": "Corner Kicks",
    "foulsCommitted": "Fouls",
    "totalShots": "Total Shots",
    "shotsOnTarget": "Shots on Goal",
}

# Stats de jugador del `summary` -> claves aplanadas canónicas que el
# verificador consume en props ("X más de 1.5 tiros a puerta").
_PLAYER_STAT_MAP = {
    "totalGoals": "goals.total",
    "goalAssists": "goals.assists",
    "shotsOnTarget": "shots.on",
    "totalShots": "shots.total",
    "foulsCommitted": "fouls.committed",
    "yellowCards": "cards.yellow",
    "redCards": "cards.red",
}

# `status.type.name` de ESPN que anula la apuesta.
_VOIDED_STATUSES = {
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "cancelled",
    "STATUS_CANCELLED": "cancelled",
}


def _team_name(team: dict) -> str:
    """El mejor nombre disponible del equipo/jugador en el payload."""
    return (
        team.get("displayName")
        or team.get("name")
        or team.get("shortDisplayName")
        or ""
    )


def _competitors(competition: dict) -> tuple[Optional[dict], Optional[dict]]:
    """(competidor local, competidor visitante) del evento."""
    home = away = None
    for comp in competition.get("competitors") or []:
        if comp.get("homeAway") == "home":
            home = comp
        elif comp.get("homeAway") == "away":
            away = comp
    return home, away


def _competitor_name(competitor: dict) -> str:
    """Nombre del equipo (fútbol/basket) o jugador (tenis)."""
    if (competitor.get("type") or "").lower() == "athlete":
        return _team_name(competitor.get("athlete") or {})
    return _team_name(competitor.get("team") or {})


def _stat_int(competitor: dict, name: str) -> Optional[int]:
    """Valor entero de una estadística de equipo ESPN (displayValue)."""
    for stat in competitor.get("statistics") or []:
        if stat.get("name") != name:
            continue
        try:
            return int(float(stat.get("displayValue") or ""))
        except (TypeError, ValueError):
            return None
    return None


def _competition_date(competition: dict) -> Optional[datetime]:
    """Fecha UTC de la competition (en tenis difiere de la del torneo)."""
    raw = competition.get("date") or competition.get("startDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _stat_value(entry_stats: list, name: str) -> float:
    for stat in entry_stats or []:
        if stat.get("name") == name:
            try:
                return float(stat.get("value") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


class EspnCoreProvider:
    """Maquinaria compartida: scoreboards por liga-día, matching de
    evento, cachés de pasada y detector de API rota.

    Las subclases parametrizan el deporte (`_SPORT_PATH`, `_LEAGUES`,
    `_SPORT_TAG`), cómo iterar las competitions del payload y cómo
    puntuar el cruce de nombres (`_score_hint`).
    """

    NAME = "espn"
    SUPPORTED_SPORTS: frozenset = frozenset()
    _SPORT_PATH = ""
    _LEAGUES: tuple[str, ...] = ()
    _SPORT_TAG = ""

    def __init__(self) -> None:
        # Scoreboards por (liga, día): una pasada comparte respuestas
        # entre picks de la misma fecha. Los fallos cachean None.
        self._boards: dict[tuple[str, str], Optional[dict]] = {}
        # Summaries por id de evento (props de jugador y odds).
        self._summaries: dict[str, Optional[dict]] = {}
        self._consecutive_failures = 0

    # --- HTTP + salud -------------------------------------------------

    def _register_failure(self, league: str, exc: Exception) -> None:
        self._consecutive_failures += 1
        logger.warning(
            "[ESPN] Error en %s (%d consecutivos): %s",
            league,
            self._consecutive_failures,
            exc,
        )
        if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            # Sin response no hay cooldown corto: aparca el día entero y
            # dispara el push "cuota agotada" a admins — aquí significa
            # "API rota o caída, revisar".
            mark_rate_limited(self.NAME)
            logger.error(
                "[ESPN] %d fallos consecutivos — API probablemente rota o "
                "caída; aparcada hasta mañana y notificado a admins",
                self._consecutive_failures,
            )

    async def _get_json(self, url: str, league: str, params: dict) -> Optional[dict]:
        """GET con conteo de llamadas y detector de salud."""
        if is_rate_limited(self.NAME):
            return None
        count_provider_call(self.NAME)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # httpx + json decode
            self._register_failure(league, exc)
            return None
        if not isinstance(data, dict):
            self._register_failure(league, ValueError("payload no-dict"))
            return None
        self._consecutive_failures = 0
        return data

    async def _scoreboard(self, league: str, day: str) -> Optional[dict]:
        """Payload del scoreboard de una liga en un día (yyyymmdd)."""
        key = (league, day)
        if key in self._boards:
            return self._boards[key]
        data = await self._get_json(
            f"{_BASE_URL}/{self._SPORT_PATH}/{league}/scoreboard",
            league,
            {"dates": day, "limit": 100},
        )
        if data is not None and "events" not in data:
            self._register_failure(league, ValueError("payload sin 'events'"))
            data = None
        self._boards[key] = data
        return data

    async def _summary(self, league: str, event_id: str) -> Optional[dict]:
        """`summary?event=` — rosters, stats por jugador y odds."""
        key = f"{league}:{event_id}"
        if key in self._summaries:
            return self._summaries[key]
        data = await self._get_json(
            f"{_BASE_URL}/{self._SPORT_PATH}/{league}/summary",
            league,
            {"event": event_id},
        )
        self._summaries[key] = data
        return data

    # --- Resolución de evento ------------------------------------------

    def _iter_competitions(self, board: dict):
        """(event, competition) del scoreboard. En fútbol/basket cada
        evento es un partido; en tenis el evento es el TORNEO y sus
        `groupings[].competitions` son los partidos."""
        for event in board.get("events") or []:
            groupings = event.get("groupings")
            if groupings:
                for grouping in groupings:
                    for competition in grouping.get("competitions") or []:
                        yield event, competition
            else:
                for competition in event.get("competitions") or []:
                    yield event, competition

    def _score_hint(self, hint: str, home_name: str, away_name: str) -> float:
        """Cruce hint->evento. Por defecto equipos; tenis lo sobreescribe
        con `_pair_similar` (jugadores/parejas)."""
        return match_score(hint, home_name, away_name)

    async def _find_event(
        self, date: datetime, team_hint: str
    ) -> Optional[tuple[dict, dict, str]]:
        """(evento, competition, liga) que mejor casa con el hint, o None.

        Primero escanea el día exacto en todas las ligas; solo si no hay
        cruce mira ±1 día (el pick a veces lleva el día de publicación).
        Un miss es definitivo: el scoreboard enumera todos los partidos
        de la liga ese día.
        """
        hint = team_hint.strip()
        if not hint or is_rate_limited(self.NAME):
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{self.NAME}|{self._SPORT_TAG}|{date.strftime('%Y-%m-%d')}"
            f"|{hint.lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        best = None
        best_score = 0.0
        best_teams: tuple[str, str] | None = None
        ambiguous = False
        for offset in (0, -1, 1):
            day = (date + timedelta(days=offset)).strftime("%Y%m%d")
            for league in self._LEAGUES:
                board = await self._scoreboard(league, day)
                if board is None:
                    continue
                for event, competition in self._iter_competitions(board):
                    comp_date = _competition_date(competition)
                    if comp_date is not None and abs(comp_date - date) > (_DATE_WINDOW):
                        continue
                    home, away = _competitors(competition)
                    if home is None or away is None:
                        continue
                    home_name = _competitor_name(home)
                    away_name = _competitor_name(away)
                    score = self._score_hint(hint, home_name, away_name)
                    if score > best_score:
                        best_score = score
                        best = (event, competition, league)
                        best_teams = (home_name, away_name)
                        ambiguous = False
                    elif (
                        score == best_score
                        and score >= _MIN_TEAM_SIMILARITY
                        and (home_name, away_name) != best_teams
                    ):
                        ambiguous = True
            if best is not None and best_score >= _MIN_TEAM_SIMILARITY:
                break  # encontrado en el día exacto: no mirar ±1

        if best is None or best_score < _MIN_TEAM_SIMILARITY or ambiguous:
            mark_missed(miss_key)
            return None
        return best

    def _is_completed(self, competition: dict) -> bool:
        status = (competition.get("status") or {}).get("type") or {}
        return bool(status.get("completed"))

    def _int_score(self, competitor: dict) -> Optional[int]:
        try:
            return int(competitor.get("score"))
        except (TypeError, ValueError):
            return None

    # --- Interfaz ResultsProvider --------------------------------------

    def _match_result(self, competition: dict) -> Optional[MatchResult]:
        """Marcador final normalizado por deporte. None si no acabó."""
        raise NotImplementedError

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        found = await self._find_event(date, team_hint)
        if found is None:
            return None
        _, competition, _ = found
        if not self._is_completed(competition):
            return None
        return self._match_result(competition)

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """Partido aplazado/cancelado. Reusa los scoreboards cacheados
        — no cuesta llamadas extra."""
        found = await self._find_event(date, team_hint)
        if found is None:
            return None
        _, competition, _ = found
        status_name = ((competition.get("status") or {}).get("type") or {}).get("name")
        status = _VOIDED_STATUSES.get(status_name or "")
        if status is None:
            return None
        home, away = _competitors(competition)
        return MatchState(
            home_team=_competitor_name(home or {}),
            away_team=_competitor_name(away or {}),
            status=status,
        )


class EspnProvider(EspnCoreProvider):
    """Resultados, stats de equipo y props de jugador de fútbol vía
    scoreboard + summary de ESPN (gratis, sin key ni cuota)."""

    SUPPORTED_SPORTS = frozenset({"futbol"})
    _SPORT_PATH = "soccer"
    _LEAGUES = _LEAGUES_FUTBOL
    _SPORT_TAG = "futbol"

    def _match_result(self, competition: dict) -> Optional[MatchResult]:
        home, away = _competitors(competition)
        if home is None or away is None:
            return None
        home_score = self._int_score(home)
        away_score = self._int_score(away)
        if home_score is None or away_score is None:
            return None
        ht_home = ht_away = None
        home_lines = home.get("linescores") or []
        away_lines = away.get("linescores") or []
        if home_lines and away_lines:
            try:
                ht_home = int(home_lines[0].get("value"))
                ht_away = int(away_lines[0].get("value"))
            except (TypeError, ValueError, IndexError):
                pass
        return MatchResult(
            home_team=_competitor_name(home),
            away_team=_competitor_name(away),
            home_score=home_score,
            away_score=away_score,
            ht_home_score=ht_home,
            ht_away_score=ht_away,
        )

    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        """Estadísticas del partido completo: córners, tarjetas, tiros,
        faltas — gratis, sin la ventana ±1 día del plan free de
        API-Football ni la cuota diaria de footapi7.

        Las tarjetas no vienen en `statistics`: se cuentan de
        `details[]` (eventos "Yellow Card"/"Red Card" por equipo). Una
        "Yellow - Red Card" cuenta como ambas — la casa paga la doble
        amarilla como amarilla + roja.
        """
        found = await self._find_event(date, team_hint)
        if found is None:
            return None
        _, competition, _ = found
        if not self._is_completed(competition):
            return None
        home, away = _competitors(competition)
        if home is None or away is None:
            return None

        values: dict[str, tuple[int, int]] = {}
        for espn_name, canonical in _STAT_NAME_MAP.items():
            home_val = _stat_int(home, espn_name)
            away_val = _stat_int(away, espn_name)
            if home_val is not None and away_val is not None:
                values[canonical] = (home_val, away_val)

        home_id = (home.get("team") or {}).get("id")
        away_id = (away.get("team") or {}).get("id")
        yellow = {"home": 0, "away": 0}
        red = {"home": 0, "away": 0}
        saw_card_details = False
        for detail in competition.get("details") or []:
            text = (detail.get("type") or {}).get("text") or ""
            if "Card" not in text:
                continue
            saw_card_details = True
            side = None
            team_id = (detail.get("team") or {}).get("id")
            if team_id == home_id:
                side = "home"
            elif team_id == away_id:
                side = "away"
            if side is None:
                continue
            if "Yellow" in text:
                yellow[side] += 1
            if "Red" in text:
                red[side] += 1
        if saw_card_details:
            values["Yellow Cards"] = (yellow["home"], yellow["away"])
            values["Red Cards"] = (red["home"], red["away"])

        if not values:
            return None
        return MatchStats(
            home_team=_competitor_name(home),
            away_team=_competitor_name(away),
            values=values,
        )

    # --- Jugadores: rosters del summary ---------------------------------

    def _roster_players(self, summary: dict) -> tuple[list[dict], list[dict]]:
        """(jugados, todos) del roster de ambos equipos.

        `appearances` distingue al que disputó minutos (1.0) del
        convocado que no entró (0.0) — verificado en vivo.
        """
        played: list[dict] = []
        everyone: list[dict] = []
        for team in summary.get("rosters") or []:
            for entry in team.get("roster") or []:
                everyone.append(entry)
                if _stat_value(entry.get("stats"), "appearances") > 0:
                    played.append(entry)
        return played, everyone

    async def find_match_events(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchEvents]:
        """Goles/asistencias/tarjetas por jugador vía `summary` -> rosters.

        El roster es COMPLETO (no solo jugadores con evento), así que
        `participants` es fiable y las props que no casan pueden marcar
        fallo en vez de quedar pendientes.
        """
        found = await self._find_event(date, team_hint)
        if found is None:
            return None
        event, competition, league = found
        if not self._is_completed(competition) or event.get("id") is None:
            return None
        summary = await self._summary(league, event["id"])
        if summary is None:
            return None
        played, everyone = self._roster_players(summary)
        if not played:
            return None

        scorers: list[str] = []
        assisters: list[str] = []
        booked: list[str] = []
        participants: list[str] = []

        for entry in everyone:
            stats = entry.get("stats") or []
            name = _team_name(entry.get("athlete") or {})
            if not name:
                continue
            if _stat_value(stats, "appearances") > 0:
                participants.append(name)
            goals = _stat_value(stats, "totalGoals")
            own_goals = _stat_value(stats, "ownGoals")
            # Propia puerta no cuenta como gol del jugador (misma regla
            # que en API-Football); el penalti marcado sí.
            if goals > own_goals and name not in scorers:
                scorers.append(name)
            if _stat_value(stats, "goalAssists") > 0 and name not in assisters:
                assisters.append(name)
            if (
                _stat_value(stats, "yellowCards") > 0
                or _stat_value(stats, "redCards") > 0
            ) and name not in booked:
                booked.append(name)

        home, away = _competitors(competition)
        return MatchEvents(
            home_team=_competitor_name(home or {}),
            away_team=_competitor_name(away or {}),
            scorers=scorers,
            assisters=assisters,
            booked=booked,
            participants=participants,
        )

    async def find_match_players(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchPlayers]:
        """Jugadores con minutos + sus estadísticas aplanadas (props con
        número: "X más de 1.5 tiros a puerta")."""
        found = await self._find_event(date, team_hint)
        if found is None:
            return None
        event, competition, league = found
        if not self._is_completed(competition) or event.get("id") is None:
            return None
        summary = await self._summary(league, event["id"])
        if summary is None:
            return None
        played, _ = self._roster_players(summary)
        if not played:
            return None

        names: list[str] = []
        stats_map: dict[str, dict[str, int]] = {}
        for entry in played:
            name = _team_name(entry.get("athlete") or {})
            if not name:
                continue
            names.append(name)
            flat: dict[str, int] = {}
            for espn_name, canonical in _PLAYER_STAT_MAP.items():
                value = _stat_value(entry.get("stats"), espn_name)
                flat[canonical] = int(value)
            stats_map[name] = flat

        home, away = _competitors(competition)
        return MatchPlayers(
            home_team=_competitor_name(home or {}),
            away_team=_competitor_name(away or {}),
            played=names,
            stats=stats_map,
        )


class EspnTennisProvider(EspnCoreProvider):
    """Resultados de tenis ATP/WTA (individuales y dobles) vía ESPN.

    Sin stats de saque (aces/dobles faltas): ESPN no las publica.
    """

    SUPPORTED_SPORTS = frozenset({"tenis"})
    _SPORT_PATH = "tennis"
    _LEAGUES = _LEAGUES_TENIS
    _SPORT_TAG = "tenis"

    def _score_hint(self, hint: str, home_name: str, away_name: str) -> float:
        # Jugadores/parejas: misma semántica que los providers de tenis.
        return max(_pair_similar(hint, home_name), _pair_similar(hint, away_name))

    def _match_result(self, competition: dict) -> Optional[MatchResult]:
        # Retirada/walkover: el partido no se completó. Las casas anulan
        # el pick — resolverlo con sets parciales sería un fallo falso.
        # Queda pendiente para los providers de debajo / manual.
        status_name = ((competition.get("status") or {}).get("type") or {}).get("name")
        if status_name in ("STATUS_RETIRED", "STATUS_WALKOVER"):
            return None
        home, away = _competitors(competition)
        if home is None or away is None:
            return None
        home_lines = home.get("linescores") or []
        away_lines = away.get("linescores") or []
        sets: list[tuple[int, int]] = []
        for h_line, a_line in zip(home_lines, away_lines):
            try:
                sets.append((int(h_line.get("value")), int(a_line.get("value"))))
            except (TypeError, ValueError):
                continue
        # Sets ganados: prioriza `winner` del competidor; si falta,
        # cuenta líneas ganadas.
        if home.get("winner") is True:
            home_score = sum(1 for s in sets if s[0] > s[1])
            away_score = len(sets) - home_score
        elif away.get("winner") is True:
            away_score = sum(1 for s in sets if s[1] > s[0])
            home_score = len(sets) - away_score
        else:
            home_score = sum(1 for s in sets if s[0] > s[1])
            away_score = sum(1 for s in sets if s[1] > s[0])
        if not sets and home_score == 0 and away_score == 0:
            return None
        return MatchResult(
            home_team=_competitor_name(home),
            away_team=_competitor_name(away),
            home_score=home_score,
            away_score=away_score,
            sets=sets or None,
        )


class EspnBasketballProvider(EspnCoreProvider):
    """Resultados de basket vía ESPN (NBA/WNBA/NBL/FIBA; sin ACB ni
    Euroliga — ESPN no las publica)."""

    SUPPORTED_SPORTS = frozenset({"baloncesto"})
    _SPORT_PATH = "basketball"
    _LEAGUES = _LEAGUES_BASKET
    _SPORT_TAG = "baloncesto"

    def _match_result(self, competition: dict) -> Optional[MatchResult]:
        home, away = _competitors(competition)
        if home is None or away is None:
            return None
        home_score = self._int_score(home)
        away_score = self._int_score(away)
        if home_score is None or away_score is None:
            return None
        # Descanso = Q1 + Q2 de las líneas por cuarto.
        ht_home = ht_away = None
        home_lines = home.get("linescores") or []
        away_lines = away.get("linescores") or []
        if len(home_lines) >= 2 and len(away_lines) >= 2:
            try:
                ht_home = sum(int(line.get("value")) for line in home_lines[:2])
                ht_away = sum(int(line.get("value")) for line in away_lines[:2])
            except (TypeError, ValueError):
                pass
        return MatchResult(
            home_team=_competitor_name(home),
            away_team=_competitor_name(away),
            home_score=home_score,
            away_score=away_score,
            ht_home_score=ht_home,
            ht_away_score=ht_away,
        )
