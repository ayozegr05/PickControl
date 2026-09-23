"""Proveedor de resultados de tenis usando TheSportsDB (primario, gratis).

Por qué TheSportsDB: no existe una API REST de tenis realmente gratuita
con cobertura completa — api-tennis.com solo ofrece trial de 14 días,
Live Tennis API no incluye resultados terminados en su tier gratuito y
Sofascore bloquea requests de servidor con Cloudflare. TheSportsDB es
gratis sin registro (la key pública "3" sirve para uso personal; con un
Patreon de $2 dan key propia) y cubre ATP/WTA Tour y Grand Slams.

Limitación: no cubre Challengers ni ITF. Para esos partidos existe
`rapidapi_tennis.py` como fallback (datos de Sofascore vía RapidAPI,
cuota gratuita mensual limitada que solo se consume cuando este
proveedor no encuentra el partido).

Endpoint: GET https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php
          ?d=YYYY-MM-DD&s=Tennis

Respuesta relevante por evento:

    "strEvent": "Australian Open Siniakova vs Udvardy",
    "strResult": "Siniakova  beat Udvardy  2-0\\r\\nSiniakova : 6 6\\nUdvardy : 1 2",
    "strStatus": "Match Finished" (o vacío)

El marcador de sets solo está en `strResult` (intHomeScore viene null
en tenis), así que se parsea "X beat Y N-M". El MatchResult devuelto
usa home_team/away_team = ganador/perdedor y home_score/away_score =
sets ganados — lo que necesita la resolución del mercado "ganador".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.services.results.base import MatchResult, MatchState

logger = get_logger("app.results.api_tennis")

_BASE_URL = "https://www.thesportsdb.com/api/v1/json/{key}/eventsday.php"
_MIN_PLAYER_SIMILARITY = 0.6
# La fecha del pick a veces es solo una aproximación (día de
# publicación, no del partido): probamos el día indicado y ±1.
_DATE_OFFSETS = (0, -1, 1)
# "Siniakova  beat Udvardy  2-0" → ganador, perdedor, sets.
_RESULT_PATTERN = re.compile(
    r"^\s*(?P<winner>.+?)\s+beat\s+(?P<loser>.+?)\s+(?P<w>\d+)\s*-\s*(?P<l>\d+)",
    re.IGNORECASE,
)
# Líneas siguientes de strResult: "Siniakova : 6 6" → juegos por set.
_GAMES_LINE = re.compile(r"^\s*(?P<name>.+?)\s*:\s*(?P<games>\d+(?:\s+\d+)*)\s*$")
# Retirada/walkover: "Sinner beat Alcaraz 1-0 RET", "... W/O", "RET".
_RETIREMENT_PATTERN = re.compile(
    r"\bret(?:ired)?\.?\b|w/?o\b|\bwalkover\b|\bdef\.?\b", re.IGNORECASE
)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _player_similar(hint: str, api_name: str) -> float:
    """Similitud entre el nombre del pick y el del evento.

    En tenis TheSportsDB usa casi siempre solo apellidos ("Siniakova",
    "Droguet") en `strResult`, mientras el tipster escribe "Titouan
    Droguet". Si algún token de >=4 letras del hint aparece en el
    nombre de la API (el apellido), elevamos la similitud por encima
    del umbral.
    """
    hint_low = hint.lower().strip()
    api_low = re.sub(r"[.\-'/]", " ", api_name).lower().strip()
    direct = _similar(hint_low, api_low)
    surname_hit = any(w in api_low for w in hint_low.split() if len(w) >= 4)
    return max(direct, 0.8) if surname_hit else direct


# Separadores de parejas de dobles en los nombres de las APIs.
_PAIR_SPLIT = re.compile(r"\s*[/+&]\s*")


def _pair_similar(hint: str, api_name: str) -> float:
    """Similitud nombre-a-nombre consciente de dobles.

    - Si el nombre de la API es una pareja ("Alcaraz / Munar") exige que
      TODOS sus miembros casen con ALGÚN miembro del hint: un pick
      individual "Alcaraz" no resuelve contra un dobles del mismo
      jugador, y un "Cukierman/Shimanov" solo casa con esa pareja.
    - Si el hint trae "/" pero el nombre de la API es individual, se
      penaliza: un pick de dobles no debe casar con el individual.
    - El orden de los miembros no importa ("Munar / Alcaraz" casa igual
      que "Alcaraz / Munar").
    - El hint también se parte por parejas: sin ello "Cukierman/Shimanov"
      era un único token y el apellido corto nunca superaba el umbral
      contra el miembro suelto de la API (caso real).
    """
    members = [m.strip() for m in _PAIR_SPLIT.split(api_name) if m.strip()]
    hint_members = [m.strip() for m in _PAIR_SPLIT.split(hint) if m.strip()]
    if len(members) <= 1:
        score = max(
            (_player_similar(h, api_name) for h in hint_members),
            default=_player_similar(hint, api_name),
        )
        return score * 0.5 if len(hint_members) > 1 else score
    return min(max(_player_similar(h, m) for h in hint_members) for m in members)


def _parse_result(event: dict) -> Optional[MatchResult]:
    """Extrae ganador/perdedor/sets de `strResult`. None si no terminó.

    Además del marcador de sets ("X beat Y 2-0") se parsean las líneas
    "Jugador : 6 6" para llevar los juegos por set en `MatchResult.sets`
    (orientados winner/loser, igual que home/away del resultado).
    """
    result = str(event.get("strResult") or "")
    m = _RESULT_PATTERN.search(result)

    # Retirada/walkover: el partido no terminó por la vía normal y las
    # casas suelen anular. Se reporta con `status` para que el
    # verificador devuelva anulada en lugar de resolver con un marcador
    # parcial ("1-0 RET" no es un 1-0 real).
    if _RETIREMENT_PATTERN.search(result):
        winner = loser = ""
        w_sets = l_sets = 0
        if m:
            winner = m.group("winner").strip()
            loser = m.group("loser").strip()
            w_sets, l_sets = int(m.group("w")), int(m.group("l"))
        else:
            # "X beat Y RET" sin marcador: nombres aproximados.
            clean = _RETIREMENT_PATTERN.sub(" ", result)
            beat = re.search(r"(?P<w>.+?)\s+beat\s+(?P<l>.+)", clean, re.IGNORECASE)
            if not beat:
                return None
            winner = beat.group("w").strip()
            loser = beat.group("l").strip()
        status = (
            "walkover" if re.search(r"w/?o\b|\bwalkover\b", result, re.I) else "retired"
        )
        return MatchResult(
            home_team=winner,
            away_team=loser,
            home_score=w_sets,
            away_score=l_sets,
            status=status,
        )

    if not m:
        return None
    winner = m.group("winner").strip()
    loser = m.group("loser").strip()

    games_by_name: dict[str, list[int]] = {}
    for line in result.splitlines()[1:]:
        gm = _GAMES_LINE.match(line)
        if gm:
            games_by_name[gm.group("name").strip()] = [
                int(x) for x in gm.group("games").split()
            ]

    sets = None
    winner_games = loser_games = None
    for name, games in games_by_name.items():
        if _player_similar(winner, name) >= _MIN_PLAYER_SIMILARITY:
            winner_games = games
        elif _player_similar(loser, name) >= _MIN_PLAYER_SIMILARITY:
            loser_games = games
    total_sets = int(m.group("w")) + int(m.group("l"))
    if (
        winner_games
        and loser_games
        and len(winner_games) == len(loser_games) == total_sets
    ):
        sets = list(zip(winner_games, loser_games))

    return MatchResult(
        home_team=winner,
        away_team=loser,
        home_score=int(m.group("w")),
        away_score=int(m.group("l")),
        sets=sets,
    )


class ApiTennisProvider:
    """Consulta TheSportsDB por partidos de tenis finalizados de un día."""

    SUPPORTED_SPORTS = frozenset({"tenis"})

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        # Caché de eventos por fecha (vive solo durante una pasada del
        # verificador): picks del mismo día reutilizan la respuesta.
        self._events_cache: dict[str, list] = {}

    async def _fetch_events(self, client: httpx.AsyncClient, date_str: str) -> list:
        if date_str in self._events_cache:
            return self._events_cache[date_str]
        try:
            response = await client.get(
                _BASE_URL.format(key=self._api_key),
                params={"d": date_str, "s": "Tennis"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("[TheSportsDB] Error de API (%s): %s", date_str, exc)
            return []
        events = response.json().get("events") or []
        self._events_cache[date_str] = events
        return events

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        best_match: Optional[MatchResult] = None
        best_score = 0.0

        async with httpx.AsyncClient(timeout=15) as client:
            for offset in _DATE_OFFSETS:
                date_str = (date + timedelta(days=offset)).strftime("%Y-%m-%d")
                for event in await self._fetch_events(client, date_str):
                    match = _parse_result(event)
                    if not match:
                        continue  # sin strResult "X beat Y" → no terminado
                    score = max(
                        _pair_similar(team_hint, match.home_team),
                        _pair_similar(team_hint, match.away_team),
                    )
                    if score > best_score:
                        best_score = score
                        best_match = match

        if not best_match or best_score < _MIN_PLAYER_SIMILARITY:
            return None
        return best_match

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """Partido aplazado/cancelado según `strStatus` de TheSportsDB
        ("Postponed"/"Cancelled"). Reusa la caché de eventos por fecha
        de `find_match` — no cuesta llamadas extra."""
        async with httpx.AsyncClient(timeout=15) as client:
            for offset in _DATE_OFFSETS:
                date_str = (date + timedelta(days=offset)).strftime("%Y-%m-%d")
                for event in await self._fetch_events(client, date_str):
                    raw_status = str(event.get("strStatus") or "").lower()
                    if "postpon" in raw_status:
                        status = "postponed"
                    elif "cancel" in raw_status:
                        status = "cancelled"
                    else:
                        continue
                    home = str(event.get("strHomeTeam") or "")
                    away = str(event.get("strAwayTeam") or "")
                    score = max(
                        _pair_similar(team_hint, home),
                        _pair_similar(team_hint, away),
                    )
                    if score >= _MIN_PLAYER_SIMILARITY:
                        return MatchState(home_team=home, away_team=away, status=status)
        return None
