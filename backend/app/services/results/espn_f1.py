"""Resultados de Fórmula 1 vía ESPN (gratis, sin key ni cuota).

Los tipsters apenas meten picks de coches, pero cuando llegan son del
tipo "Menos de 19.5 coches" (clasificados que acaban la carrera) o
"Verstappen gana". El scoreboard `racing/f1` de ESPN lista un evento
por Gran Premio con una competición por sesión (FP1/FP2/FP3/Qual/
Sprint/Race); la carrera es `type.abbreviation == "Race"`.

Dos detalles de la API que condicionan el diseño:

- El scoreboard ordena a los 20-22 pilotos por clasificación, pero NO
  marca abandonos: `order` va 1..N también para los retirados (quedan
  al final). El estado real de cada piloto cuelga de
  `competitors[].status.$ref` en la core API — una llamada por piloto,
  ~22 consultas en paralelo solo para picks F1 (rarísimos).
- `STATUS_CLASSIFIED` = acabó y fue clasificado; `STATUS_RETIRED` y
  cualquier otro estado = no acabó. "Coches" = clasificados.

Mercados cubiertos: over/under de coches clasificados ("Classified
Cars") y ganador del GP (winner modelado como local 1-0). Aplazados
vía `_VOIDED_STATUSES` igual que el resto de ESPN.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from app.core.logging import get_logger
from app.services.results.base import (
    MISSED_TTL_PROVISIONAL,
    MatchResult,
    MatchState,
    MatchStats,
    fold_name,
    is_missed,
    is_rate_limited,
    mark_missed,
    miss_is_provisional,
)
from app.services.results.espn import (
    _MONTH_OFFSETS,
    _VOIDED_STATUSES,
    EspnCoreProvider,
    _competition_date,
    _competitor_name,
    _shift_month,
)

logger = get_logger("app.results.espn_f1")

_CORE_URL = "https://sports.core.api.espn.com/v2/sports/racing/leagues"
_MIN_EVENT_SCORE = 0.6
# El tipster publica el slip el día de la carrera o la víspera; los GP
# están separados ~2 semanas, así que ±10 días no puede confundir
# eventos si el nombre casa.
_DATE_TOLERANCE = timedelta(days=10)
# `type.abbreviation` de la sesión competitiva (el resto son
# entrenamientos, clasificación y sprint).
_RACE_ABBREVIATION = "Race"
# Estado por piloto que cuenta como "coche que acaba" (las casas pagan
# los clasificados oficiales; retirados/DSQ/DNS no cuentan).
_CLASSIFIED_STATUS = "STATUS_CLASSIFIED"

# Tokens sin carga para el matching ("gp italia", "gran premio de
# italia", "f1 gp de italia" -> mismo conjunto de localización).
_GP_DROP_TOKENS = {
    "gp",
    "gran",
    "premio",
    "grand",
    "prix",
    "de",
    "del",
    "la",
    "el",
    "f1",
    "f2",
    "formula",
    "formula1",
    "one",
    "uno",
    "en",
    "y",
    "e",
    "the",
    "a",
    "an",
}
# Localización del GP como la escribe el tipster -> formas aceptadas en
# el nombre ESPN (la sede puede llamarse en inglés — "Italian Grand
# Prix" — o local — "Gran Premio d'Italia"). Varias formas por token:
# basta que UNA aparezca en el nombre del evento.
_GP_LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "italia": ("italian", "italia"),
    "italy": ("italian", "italia"),
    "azerbaiyan": ("azerbaijan", "azerbaiyan"),
    "azerbaijan": ("azerbaijan", "azerbaiyan"),
    "espana": ("spanish", "espana", "espanya"),
    "spain": ("spanish", "espana"),
    "mexico": ("mexico", "mexican"),
    "monaco": ("monaco",),
    "bretana": ("british", "bretana"),
    "britain": ("british",),
    "british": ("british",),
    "hungria": ("hungarian", "hungria"),
    "hungary": ("hungarian", "hungria"),
    "belgica": ("belgian", "belgica"),
    "belgium": ("belgian", "belgica"),
    "holanda": ("dutch", "holanda"),
    "netherlands": ("dutch",),
    "singapur": ("singapore", "singapur"),
    "singapore": ("singapore", "singapur"),
    "brasil": ("brazilian", "brasil", "brazil"),
    "brazil": ("brazilian", "brasil", "brazil"),
    "vegas": ("vegas",),
    "catar": ("qatar", "catar"),
    "qatar": ("qatar", "catar"),
    "dhabi": ("dhabi",),
    "australia": ("australian", "australia"),
    "china": ("chinese", "china"),
    "japon": ("japanese", "japon"),
    "japan": ("japanese", "japon"),
    "barein": ("bahrain", "barein"),
    "bahrain": ("bahrain", "barein"),
    "saudi": ("saudi", "arabian"),
    "arabia": ("saudi", "arabian"),
    "miami": ("miami",),
    "imola": ("imola", "emilia"),
    "emilia": ("emilia", "romagna"),
    "romagna": ("romagna", "emilia"),
    "canada": ("canadian", "canada"),
    "austria": ("austrian", "austria"),
    "unidos": ("united", "states"),
    "states": ("united", "states"),
    "usa": ("united", "states"),
    "francia": ("french", "francia"),
    "france": ("french", "francia"),
    "jerez": ("jerez",),
    "portugal": ("portuguese", "portugal"),
    "turquia": ("turkish", "turquia"),
    "turkey": ("turkish", "turquia"),
    "rusia": ("russian", "rusia"),
    "russia": ("russian", "rusia"),
    "alemania": ("german", "alemania"),
    "germany": ("german", "alemania"),
}


def _name_tokens(text: str) -> list[str]:
    """Tokens alfanuméricos del nombre ya plegado (sin acentos)."""
    return re.findall(r"[a-z0-9]+", fold_name(text))


def _hint_location_tokens(hint: str) -> list[str]:
    """Tokens de localización del hint: sin 'gp/gran premio/formula'."""
    return [t for t in _name_tokens(hint) if t not in _GP_DROP_TOKENS]


def _event_score(hint: str, event_name: str) -> float:
    """0-1: cuántos tokens de localización del hint salen en el evento.

    "GP AZERBAIYÁN" -> ["azerbaiyan"] -> aceptado por ("azerbaijan",
    "azerbaiyan") ⊆ tokens de "Qatar Airways Azerbaijan Grand Prix".
    Todos los tokens deben casar para nota alta; parcial si falta
    alguno (evita casar "GP Miami" con cualquier GP).
    """
    location = _hint_location_tokens(hint)
    if not location:
        return 0.0
    event_tokens = set(_name_tokens(event_name))
    matched = 0
    for token in location:
        options = _GP_LOCATION_ALIASES.get(token, (token,))
        if any(option in event_tokens for option in options):
            matched += 1
    if matched == len(location):
        return 0.85
    direct = SequenceMatcher(None, " ".join(location), fold_name(event_name)).ratio()
    return min(direct, 0.5) if matched else direct * 0.5


def _race_competition(event: dict) -> Optional[dict]:
    """La competición `Race` del GP (FP/Qual/Sprint se descartan)."""
    for competition in event.get("competitions") or []:
        if (competition.get("type") or {}).get("abbreviation") == _RACE_ABBREVIATION:
            return competition
    return None


class EspnF1Provider(EspnCoreProvider):
    """Fórmula 1 vía scoreboard `racing/f1` + statuses de la core API.

    La parte genérica (matching por hint + tolerancia de fecha, miss
    keys, orquestación de find_match/stats/postponed) vive aquí y se
    apoya en hooks `_fetch_races`/`_race_*` que cada provider implementa
    — `jolpica_f1.py` los sobrescribe para la API Ergast-compatible.
    """

    SUPPORTED_SPORTS = frozenset({"automovilismo"})
    _SPORT_PATH = "racing"
    _LEAGUES = ("f1",)
    _SPORT_TAG = "automovilismo"
    _DATE_TOLERANCE = _DATE_TOLERANCE

    # --- Hooks por provider (ESPN abajo, Jolpica en su módulo) ----------

    async def _fetch_races(self, year: int, month: int) -> list[dict]:
        """Carreras del mes como dicts opacos `{"event", "race"}`."""
        board = await self._scoreboard("f1", f"{year}{month:02d}")
        races = []
        for event in (board or {}).get("events") or []:
            race = _race_competition(event)
            if race is not None:
                races.append({"event": event, "race": race})
        return races

    def _race_name(self, race: dict) -> str:
        return race["event"].get("name") or ""

    def _race_date(self, race: dict) -> Optional[datetime]:
        return _competition_date(race["race"])

    async def _race_completed(self, race: dict) -> bool:
        return self._is_completed(race["race"])

    def _race_postponed_status(self, race: dict) -> Optional[str]:
        """Estado void (`postponed`/`cancelled`) o None si se jugó."""
        status_name = ((race["race"].get("status") or {}).get("type") or {}).get("name")
        return _VOIDED_STATUSES.get(status_name or "")

    async def _race_result(self, race: dict) -> Optional[tuple[str, str]]:
        """(ganador, segundo) por orden de clasificación."""
        ordered = sorted(
            race["race"].get("competitors") or [],
            key=lambda c: c.get("order") or 999,
        )
        if len(ordered) < 2:
            return None
        return _competitor_name(ordered[0]), _competitor_name(ordered[1])

    async def _classified_count(self, race: dict) -> Optional[int]:
        """Pilotos que acabaron la carrera (None = dato incompleto)."""
        statuses = await self._competitor_statuses(
            str(race["event"].get("id") or ""), str(race["race"].get("id") or "")
        )
        if statuses is None:
            return None
        return sum(1 for s in statuses if s == _CLASSIFIED_STATUS)

    # --- Matching genérico ----------------------------------------------

    async def _find_race(self, date: datetime, team_hint: str) -> Optional[dict]:
        """La carrera del GP que casa con el hint (dict del provider)."""
        hint = team_hint.strip()
        if not hint or is_rate_limited(self.NAME):
            return None
        provisional = miss_is_provisional(date)
        miss_key = (
            f"{self.NAME}|{self._SPORT_TAG}|v1|{date.strftime('%Y-%m-%d')}"
            f"|{hint.lower()}"
        )
        if provisional:
            miss_key += "|prov"
        if is_missed(miss_key, MISSED_TTL_PROVISIONAL if provisional else None):
            return None

        best: Optional[dict] = None
        best_score = 0.0
        for shifted in (_shift_month(date, off) for off in _MONTH_OFFSETS):
            for race in await self._fetch_races(shifted.year, shifted.month):
                race_date = self._race_date(race)
                if (
                    race_date is not None
                    and abs(race_date - date) > self._DATE_TOLERANCE
                ):
                    continue
                score = _event_score(hint, self._race_name(race))
                if score > best_score:
                    best_score = score
                    best = race

        if best is None or best_score < _MIN_EVENT_SCORE:
            mark_missed(miss_key)
            return None
        return best

    async def _competitor_statuses(
        self, event_id: str, race_id: str
    ) -> Optional[list[Optional[str]]]:
        """`status.type.name` de cada piloto de la carrera.

        La core API solo lo expone por `$ref` individual: una llamada
        por piloto en paralelo. None si alguna llamada falla — un
        conteo parcial no es fiable para "más/menos de X coches".
        """
        detail = await self._get_json(
            f"{_CORE_URL}/f1/events/{event_id}/competitions/{race_id}",
            "f1",
            {"lang": "en", "region": "us"},
        )
        if detail is None:
            return None
        refs = [
            ((comp.get("status") or {}).get("$ref") or "").replace(
                "http://", "https://"
            )
            for comp in detail.get("competitors") or []
        ]
        if not refs or any(not ref for ref in refs):
            return None
        statuses = await asyncio.gather(
            *(self._get_json(ref, "f1", {}) for ref in refs)
        )
        if any(status is None for status in statuses):
            return None
        return [((status.get("type") or {}).get("name")) for status in statuses]

    # --- Interfaz ResultsProvider --------------------------------------

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Ganador del GP modelado como "local 1-0 visitante": el
        ganador es `home` y el segundo `away` — así "Verstappen gana"
        se resuelve con la maquinaria de ganador existente."""
        race = await self._find_race(date, team_hint)
        if race is None or not await self._race_completed(race):
            return None
        result = await self._race_result(race)
        if result is None:
            return None
        winner, runner_up = result
        return MatchResult(
            home_team=winner, away_team=runner_up, home_score=1, away_score=0
        )

    async def find_match_stats(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchStats]:
        """ "Classified Cars": pilotos que acabaron la carrera.

        Sirve a "más/menos de X coches" — el único mercado F1 que
        meten los tipsters seguidos hoy.
        """
        race = await self._find_race(date, team_hint)
        if race is None or not await self._race_completed(race):
            return None
        classified = await self._classified_count(race)
        if classified is None:
            return None
        return MatchStats(
            home_team=self._race_name(race),
            away_team="",
            values={"Classified Cars": (classified, 0)},
        )

    async def find_postponed_match(
        self, date: datetime, team_hint: str
    ) -> Optional[MatchState]:
        """GP aplazado/cancelado (raro en F1, pero el estado existe)."""
        race = await self._find_race(date, team_hint)
        if race is None:
            return None
        status = self._race_postponed_status(race)
        if status is None:
            return None
        return MatchState(home_team=self._race_name(race), away_team="", status=status)
