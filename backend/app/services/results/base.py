"""Interfaz común para proveedores de resultados deportivos.

Cada proveedor sabe consultar su propia API y devolver el resultado de
un partido concreto, sin conocer nada de `ParsedPick` ni de picks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, Protocol

import httpx

from app.core.logging import get_logger

logger = get_logger("app.results.base")


# --- Parecido entre la pista de equipo del pick y nombres reales ------
#
# El hint puede ser un equipo suelto ("Alavés"), ambos ("Alavés -
# Valencia", "Elche vs Real Madrid") o texto con ruido. La comparación
# usa similitud de cadenas más un bonus cuando las palabras de una parte
# son subconjunto del nombre real ("alavés" ⊂ "deportivo alavés"), que
# es justo lo que hace fallar una similitud global estricta.
_WORD_PATTERN = re.compile(r"\w+")
_HINT_SEPARATORS = re.compile(r"\s[-–—/|]\s|\s+vs?\.?\s+", re.IGNORECASE)
_SCORE_SUBSET = 0.85
_SCORE_SUPSET = 0.75


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _tokens(text: str) -> set[str]:
    return set(_WORD_PATTERN.findall(text.lower()))


def _part_score(part: str, team: str) -> float:
    score = _similar(part, team)
    part_tokens = _tokens(part)
    team_tokens = _tokens(team)
    if part_tokens and part_tokens <= team_tokens:
        # "alavés" ⊂ "deportivo alavés" (subset total).
        score = max(score, _SCORE_SUBSET)
    elif team_tokens and team_tokens <= part_tokens:
        # El hint lleva el nombre entero más ruido ("valencia cf 21:00").
        score = max(score, _SCORE_SUPSET)
    return score


def split_team_hint(team_hint: str) -> list[str]:
    """Partes del hint: ["Alavés", "Valencia"] para "Alavés - Valencia"."""
    parts = [p.strip() for p in _HINT_SEPARATORS.split(team_hint) if p.strip()]
    return parts or [team_hint.strip()]


def match_score(team_hint: str, home_team: str, away_team: str) -> float:
    """Confianza 0.0-1.0 de que el fixture (home vs away) es el del hint.

    - Una parte: el mejor parecido con cualquiera de los dos equipos.
    - Dos o más partes: exige que las dos primeras casen cada una con un
      equipo distinto (min del mejor emparejamiento) — así "Alavés -
      Valencia" casa con "Deportivo Alavés - Valencia CF" y un nombre
      suelto no puede colarse en el partido equivocado.
    """
    parts = split_team_hint(team_hint)
    if len(parts) == 1:
        return max(_part_score(parts[0], home_team), _part_score(parts[0], away_team))
    direct = min(_part_score(parts[0], home_team), _part_score(parts[1], away_team))
    cross = min(_part_score(parts[0], away_team), _part_score(parts[1], home_team))
    return max(direct, cross)


def match_reversed(team_hint: str, home_team: str, away_team: str) -> bool:
    """True si el hint casa mejor con el fixture en orden inverso.

    Sirve para mercados donde el orden local/visitante importa
    (resultado exacto "2-1"): si el tipster escribió el evento al
    revés ("Betis - Levante"), el marcador predicho va también al
    revés respecto al del proveedor.
    """
    parts = split_team_hint(team_hint)
    if len(parts) < 2:
        return False
    direct = min(_part_score(parts[0], home_team), _part_score(parts[1], away_team))
    cross = min(_part_score(parts[0], away_team), _part_score(parts[1], home_team))
    return cross > direct


@dataclass
class MatchResult:
    """Resultado final de un partido, ya normalizado entre proveedores.

    En tenis `home_score`/`away_score` son sets ganados y `sets` lleva
    los juegos de cada set orientados igual que home/away
    (`[(juegos_home_set1, juegos_away_set1), ...]`): permite mercados
    de juegos (over/under, hándicap). None si el proveedor no lo da.
    """

    home_team: str
    away_team: str
    home_score: int
    away_score: int
    sets: Optional[list[tuple[int, int]]] = None


@dataclass
class MatchEvents:
    """Eventos del partido ya normalizados (goles, asistencias, tarjetas).

    Sirve para mercados de jugador ("X marca", "X marca o asiste",
    "X recibe tarjeta"). `participants` son todos los jugadores que
    aparecen en algún evento (incluidos cambios) — permite distinguir
    "jugó y no marcó" de "no consta que jugara" (la casa anularía).
    Solo lo devuelven proveedores con endpoint de eventos (hoy:
    API-Football `/fixtures/events`).
    """

    home_team: str
    away_team: str
    scorers: list[str]
    assisters: list[str]
    booked: list[str]
    participants: list[str]


@dataclass
class MatchStats:
    """Estadísticas de un partido ya normalizadas (córners, tarjetas...).

    `values` mapea el tipo de estadística del proveedor ("Corner Kicks",
    "Yellow Cards", "Total Shots"...) a `(valor_local, valor_visitante)`.
    Solo la devuelven los proveedores con endpoint de estadísticas
    (hoy: API-Football `/fixtures/statistics`).
    """

    home_team: str
    away_team: str
    values: dict[str, tuple[int, int]]


@dataclass
class MatchState:
    """Fixture localizado que NO llegó a jugarse (aplazado/cancelado).

    Sirve para anular picks: si el partido no se disputa dentro de la
    ventana que da la casa (~24-72 h según la casa), la apuesta se
    devuelve. `status` va normalizado a "postponed" | "cancelled".
    Los parados a mitad de juego (suspended/abandoned/interrupted) NO
    entran aquí: con marcador parcial algunos mercados ya quedan
    decididos y la casa los paga igualmente — esos siguen pendientes.
    """

    home_team: str
    away_team: str
    status: str


@dataclass
class MatchPlayers:
    """Jugadores que disputaron minutos en un partido.

    Lo devuelve `/fixtures/players` de API-Football: en mercados de
    jugador permite distinguir "jugó sin hacer nada reseñable" (fallo)
    de "no jugó" (la casa anula). Si el proveedor no da este dato, un
    jugador sin eventos queda pendiente — nunca se asume que no jugó.

    `stats` mapea nombre de jugador -> estadísticas aplanadas de la API
    ("shots.total", "shots.on", "fouls.committed", "goals.total"...):
    sirve para props de jugador con número ("X más de 1.5 tiros").
    """

    home_team: str
    away_team: str
    played: list[str]
    stats: dict[str, dict[str, int]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.stats is None:
            self.stats = {}


class ResultsProvider(Protocol):
    """Un proveedor de resultados deportivos (football-data.org, API-Football...)."""

    # Deportes canónicos (minúsculas, sin tilde) que el proveedor sabe
    # resolver: "futbol", "tenis", "baloncesto"... El verificador solo
    # consulta a un proveedor los picks de los deportes que cubre — un
    # pick de tenis nunca debe buscarse en una API de fútbol.
    SUPPORTED_SPORTS: frozenset

    async def find_match(self, date: datetime, team_hint: str) -> Optional[MatchResult]:
        """Busca un partido finalizado en la fecha dada que involucre a
        un equipo parecido a `team_hint`. Devuelve None si no lo
        encuentra (partido no cubierto, aún no jugado, o error de API)."""
        ...


# --- Estado persistente de cuotas y fallos de los proveedores ---------
#
# Los proveedores de pago/cuota (RapidAPI: ~50 req/día) guardan aquí dos
# cosas en un JSON junto al backend para sobrevivir a los reinicios de
# uvicorn (--reload reinicia en cada guardado y resetearía la memoria):
#
# - "rate_limited": proveedor -> fecha ISO en que agotó su cuota
#   (403/429). Ese día se salta sin ni siquiera llamar; al cambiar de
#   día vuelve a intentarse.
# - "missed": clave de búsqueda (p. ej. "2026-09-15|alcaraz" en
#   proveedor) -> fecha ISO en que se comprobó sin resultado. Una
#   búsqueda que falló no se repite durante _MISSED_TTL_DAYS: los
#   resultados históricos no aparecen días después, así que reintentar
#   solo quema cuota.
#
# Sin esto, cada reinicio del backend volvía a gastar la cuota diaria
# reintentando picks irresolubles hasta el 429.
_STATE_FILE = Path(__file__).resolve().parents[3] / "provider_state.json"
_MISSED_TTL = timedelta(days=15)

_STATE: dict[str, dict[str, str]] | None = None


def _load_state() -> dict[str, dict[str, str]]:
    global _STATE
    if _STATE is None:
        try:
            _STATE = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _STATE = {}
        _STATE.setdefault("rate_limited", {})
        _STATE.setdefault("missed", {})
    return _STATE


def _save_state() -> None:
    today = date_type.today().isoformat()
    cutoff = (date_type.today() - _MISSED_TTL).isoformat()
    state = _load_state()
    # Podar entradas viejas para que el archivo no crezca sin límite.
    state["rate_limited"] = {
        k: v for k, v in state["rate_limited"].items() if v >= today
    }
    state["missed"] = {k: v for k, v in state["missed"].items() if v >= cutoff}
    try:
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        logger.warning("[RESULTS] No se pudo guardar provider_state.json: %s", exc)


def mark_rate_limited(provider_name: str) -> None:
    """Marca un proveedor como sin cuota por el resto del día."""
    _load_state()["rate_limited"][provider_name] = date_type.today().isoformat()
    _save_state()


def is_rate_limited(provider_name: str) -> bool:
    """True si el proveedor agotó su cuota hoy (429/403) y hay que saltarlo."""
    return (
        _load_state()["rate_limited"].get(provider_name)
        == date_type.today().isoformat()
    )


def mark_missed(key: str) -> None:
    """Recuerda que `key` ya se buscó sin resultado (ver _MISSED_TTL)."""
    _load_state()["missed"][key] = date_type.today().isoformat()
    _save_state()


def is_missed(key: str) -> bool:
    """True si `key` ya se buscó sin resultado hace menos de _MISSED_TTL."""
    checked_on = _load_state()["missed"].get(key)
    if checked_on is None:
        return False
    return checked_on >= (date_type.today() - _MISSED_TTL).isoformat()


def rate_limit_from(exc: BaseException) -> bool:
    """True si la excepción HTTP es un problema de cuota/acceso (403, 429)."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
        403,
        429,
    )
