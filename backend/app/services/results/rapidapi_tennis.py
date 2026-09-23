"""Proveedor de resultados de tenis vía RapidAPI (fallback de TheSportsDB).

"Tennis API - ATP WTA ITF" (tennis-api-atp-wta-itf.p.rapidapi.com, docs
en docs.tennis-api.com) cubre ATP, WTA, Challenger e ITF — justo lo que
TheSportsDB no cubre. Su plan gratuito da ~50 req/día, así que el
verificador lo consulta SOLO cuando TheSportsDB no encontró el partido
(mismo patrón que football-data → api-football).

A diferencia del resto de proveedores, este busca por **jugador**, no
por fecha:

    GET /tennis/v2/profile/{nombre}/matches-played?year=YYYY&limit=50

Respuesta: `singles` es una lista de partidos donde `player1` es
SIEMPRE el ganador y `player2` el perdedor (convención documentada de
la tabla histórica), con `date` ISO y `result` tipo "6-7(5) 6-1 6-3"
(juegos por set desde la perspectiva del ganador).

El MatchResult devuelto usa home_team/away_team = ganador/perdedor y
home_score/away_score = sets ganados — lo que necesita la resolución
del mercado "ganador".
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
    MatchResult,
    is_missed,
    is_rate_limited,
    log_remaining_quota,
    mark_missed,
    mark_rate_limited,
    miss_is_provisional,
    rate_limit_from,
)
from app.services.results.response_cache import (
    event_list_covers,
    get_event_list,
    set_event_list,
)

logger = get_logger("app.results.rapidapi_tennis")

# Nombre con el que este proveedor se marca cuando agota su cuota
# diaria (ver base.py): se salta sin llamar hasta el día siguiente.
_PROVIDER_NAME = "rapidapi-tennis-atp-wta-itf"

_MIN_PLAYER_SIMILARITY = 0.6
# La fecha del pick a veces es solo una aproximación (día de
# publicación, no del partido): aceptamos partidos ±1 día.
_DATE_TOLERANCE = timedelta(days=1)
# Un set "6-7(5)" → juegos de cada jugador (se ignora el tiebreak entre
# paréntesis). Letras ("RET", "W/O") = retirada/wo → no se parsea: los
# corredores suelen anular esas apuestas, así que queda pendiente.
_SET_SCORE = re.compile(r"^(\d+)-(\d+)")


# Retirada/walkover en el string de resultado: "6-1 2-0 RET", "W/O".
_RETIREMENT_PATTERN = re.compile(
    r"\bret(?:ired)?\.?\b|w/?o\b|\bwalkover\b|\bdef\.?\b", re.IGNORECASE
)


def _parse_set_games(result: str) -> Optional[list[tuple[int, int]]]:
    """Juegos por set desde la perspectiva del ganador: "6-7(5) 6-1"
    -> [(6, 7), (6, 1)]. None si hay retirada/walkover (letras) o no
    es parseable."""
    if re.search(r"[a-zA-Z]", result):
        return None
    games = []
    for token in result.split():
        m = _SET_SCORE.match(token)
        if not m:
            return None
        games.append((int(m.group(1)), int(m.group(2))))
    return games or None


def _count_sets(result: str) -> Optional[tuple[int, int]]:
    """Sets ganados por cada jugador a partir de "6-7(5) 6-1 6-3".

    Devuelve (sets_p1, sets_p2) contando qué jugador ganó cada set, o
    None si el string contiene retiradas/walkovers o no es parseable.
    """
    games = _parse_set_games(result)
    if not games:
        return None
    p1_won = p2_won = 0
    for g1, g2 in games:
        if g1 > g2:
            p1_won += 1
        elif g2 > g1:
            p2_won += 1
    if not p1_won and not p2_won:
        return None
    return p1_won, p2_won


def _parse_match_date(raw: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


class RapidApiTennisProvider:
    """Consulta Tennis API (RapidAPI) como fallback de tenis."""

    SUPPORTED_SPORTS = frozenset({"tenis"})

    def __init__(self, api_key: str, api_host: str) -> None:
        self._api_key = api_key
        self._api_host = api_host
        # Caché de historial por jugador (vive solo durante una pasada
        # del verificador): picks del mismo jugador/día no repiten la
        # llamada — clave para estirar la cuota gratuita diaria.
        self._matches_cache: dict[str, list] = {}

    async def _fetch_matches(
        self,
        client: httpx.AsyncClient,
        player_name: str,
        year: int,
        pick_date: datetime,
    ) -> Optional[list]:
        """Historial del jugador, [] si no tiene datos y None si la
        llamada falló (error transitorio: el caller NO debe marcar
        `missed`, porque la ausencia no es evidencia real).

        Persiste en `provider_cache.json`: un año ya cerrado es inmutable
        (incluso si la lista salió vacía); el año en curso se reutiliza
        solo si cubre la fecha del pick (`event_list_covers`).
        """
        cache_key = f"{player_name.strip().lower()}:{year}"
        if cache_key in self._matches_cache:
            return self._matches_cache[cache_key]
        persist_key = f"{_PROVIDER_NAME}|{cache_key}"
        past_year = year < datetime.now(timezone.utc).year
        cached = get_event_list(persist_key)
        if cached is not None and (past_year or event_list_covers(cached, pick_date)):
            self._matches_cache[cache_key] = cached["events"]
            return cached["events"]
        try:
            response = await client.get(
                f"https://{self._api_host}/tennis/v2/profile/"
                f"{quote(player_name.strip())}/matches-played",
                params={"year": year, "limit": 50},
                headers={
                    "X-RapidAPI-Key": self._api_key,
                    "X-RapidAPI-Host": self._api_host,
                },
            )
            response.raise_for_status()
            log_remaining_quota(_PROVIDER_NAME, response)
        except httpx.HTTPError as exc:
            if rate_limit_from(exc):
                mark_rate_limited(_PROVIDER_NAME)
                logger.warning(
                    "[RapidAPI-Tennis] Cuota agotada (%s); se omite hasta mañana",
                    _PROVIDER_NAME,
                )
            else:
                logger.warning(
                    "[RapidAPI-Tennis] Error de API (%s): %s", player_name, exc
                )
            # Se cachea el fallo dentro de la instancia para no reintentar
            # el mismo jugador en cada pick del ciclo.
            self._matches_cache[cache_key] = None
            return None
        data = response.json()
        # El endpoint separa individuales y dobles del jugador; nos
        # interesan ambos (el matching por parejas filtra después).
        matches = (data.get("singles") or []) + (data.get("doubles") or [])
        self._matches_cache[cache_key] = matches
        # Año cerrado -> inmutable (se guarda aunque esté vacío); año en
        # curso -> solo se fija una lista con datos (puede crecer).
        if past_year or matches:
            set_event_list(persist_key, matches)
        return matches

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        if is_rate_limited(_PROVIDER_NAME):
            return None
        # Si este jugador ya se buscó sin resultado, no se repite la
        # llamada en cada ciclo (la cuota diaria es de ~50 peticiones).
        # Evento reciente: la ausencia puede ser "aún no terminó" ->
        # fallo provisional (TTL de horas, clave "|prov"). Evento ya
        # pasado: definitivo (15 días).
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{_PROVIDER_NAME}|{date.strftime('%Y-%m-%d')}|{team_hint.strip().lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, None if not provisional else MISSED_TTL_PROVISIONAL):
            return None
        best_match: Optional[MatchResult] = None
        best_score = 0.0

        # El endpoint busca el historial de UN jugador: si la pista es
        # un evento ("Alcaraz - Sinner") o una pareja de dobles
        # ("Alcaraz / Munar"), se consulta por el primer nombre.
        lookup_name = (
            re.split(r"[/+&]|\s+-\s+|\s+vs\.?\s+", team_hint, maxsplit=1)[0].strip()
            or team_hint
        )

        async with httpx.AsyncClient(timeout=15) as client:
            matches = await self._fetch_matches(client, lookup_name, date.year, date)
            if matches is None:
                return None  # error de API: no se marca missed
            for match in matches:
                winner = (match.get("player1") or {}).get("name")
                loser = (match.get("player2") or {}).get("name")
                played = _parse_match_date(match.get("date") or "")
                if not winner or not loser or not played:
                    continue
                if abs(played - date) > _DATE_TOLERANCE:
                    continue
                score = max(
                    _pair_similar(team_hint, winner),
                    _pair_similar(team_hint, loser),
                )
                if score <= best_score:
                    continue
                result_str = str(match.get("result") or "")
                sets = _count_sets(result_str)
                if not sets:
                    if _RETIREMENT_PATTERN.search(result_str):
                        # Retirada/walkover: el verificador lo anula.
                        best_score = score
                        best_match = MatchResult(
                            home_team=winner,
                            away_team=loser,
                            home_score=0,
                            away_score=0,
                            status="retired",
                        )
                    continue
                best_score = score
                best_match = MatchResult(
                    home_team=winner,
                    away_team=loser,
                    home_score=sets[0],
                    away_score=sets[1],
                    # Juegos por set (perspectiva del ganador) para
                    # mercados de juegos.
                    sets=_parse_set_games(result_str),
                )

        if not best_match or best_score < _MIN_PLAYER_SIMILARITY:
            mark_missed(miss_key)
            return None
        return best_match
