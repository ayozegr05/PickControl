"""Interfaz común para proveedores de resultados deportivos.

Cada proveedor sabe consultar su propia API y devolver el resultado de
un partido concreto, sin conocer nada de `ParsedPick` ni de picks.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional, Protocol

import httpx

from app.core.dates import utc_now
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


def fold_name(text: str) -> str:
    """Minúsculas sin acentos ni diacríticos ("Mérida"→"merida",
    "Šeško"→"sesko", "Garín"→"garin").

    Las APIs deportivas romanizan los nombres y los tipsters escriben
    con acentos: sin plegar, `"garín" in "cristian garin"` es False y
    el partido no casa aunque sea el correcto. Casos reales perdidos:
    "Mérida vs Garín", "Báez", "Čilić", "Džumhur".
    """
    folded = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, fold_name(a), fold_name(b)).ratio()


def log_remaining_quota(provider_name: str, response: httpx.Response) -> None:
    """Cuota restante según headers de RapidAPI en una llamada que ya
    se hizo (`x-ratelimit-requests-remaining`): telemetría gratis —
    avisa cuando quedan pocas llamadas para el día."""
    count_provider_call(provider_name)
    headers = getattr(response, "headers", None) or {}
    remaining = headers.get("x-ratelimit-requests-remaining")
    if remaining is None:
        return
    try:
        left = int(remaining)
    except ValueError:
        return
    limit = headers.get("x-ratelimit-requests-limit") or "?"
    if left <= 20:
        logger.warning(
            "[QUOTA] %s: quedan %s/%s llamadas hoy", provider_name, left, limit
        )
    else:
        logger.debug("[QUOTA] %s: %s/%s", provider_name, left, limit)


def _tokens(text: str) -> set[str]:
    return set(_WORD_PATTERN.findall(fold_name(text)))


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

    `status` marca finales anómalos reportados por el proveedor
    ("retired", "walkover" en tenis): el partido no terminó por la vía
    normal y las casas suelen devolver la apuesta. None = final normal.

    `ht_home_score`/`ht_away_score` son el marcador al DESCANSO (1ª
    parte) cuando el proveedor lo trae: habilita mercados de primera
    parte ("gana la 1ª parte", over/under de goles 1H, empate al
    descanso, descanso/final). None si el proveedor no lo informa.
    """

    home_team: str
    away_team: str
    home_score: int
    away_score: int
    sets: Optional[list[tuple[int, int]]] = None
    status: Optional[str] = None
    ht_home_score: Optional[int] = None
    ht_away_score: Optional[int] = None


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


# `status.type` de Sofascore -> `status` de MatchState. Lo comparten los
# tres providers del mirror Sofascore (tennisapi1, allsportsapi2,
# footapi7). Los parados a mitad (interrupted/abandoned/suspended) NO
# anulan: con marcador parcial la casa paga los mercados ya decididos.
SOFASCORE_VOIDED_STATUSES = {
    "postponed": "postponed",
    "canceled": "cancelled",
    "cancelled": "cancelled",
}


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
# Un "no encontrado" sobre un evento reciente NO es definitivo: el
# verificador intenta el pick en cuanto `fecha_evento < ahora`, o sea
# antes o durante el partido — el resultado simplemente no existe todavía.
# Esos fallos se recuerdan solo unas horas (reintento pocas veces al día,
# no en cada ciclo) bajo una clave distinta ("|prov"): cuando el evento
# cruza `_MISS_DEFINITIVE_AGE` se consulta la clave definitiva, que no
# está marcada, y se hace una última búsqueda seria antes de rendirse.
MISSED_TTL_PROVISIONAL = timedelta(hours=6)
# Edad a partir de la cual la ausencia de resultado sí es definitiva
# (día del partido + tolerancia ±1 del nivel anterior ya pasados).
_MISS_DEFINITIVE_AGE = timedelta(hours=48)


def miss_is_provisional(event_date: datetime) -> bool:
    """True si el evento es tan reciente que un "no encontrado" puede
    deberse a que el partido aún no terminó o el resultado aún no subió
    al proveedor. Pasada `_MISS_DEFINITIVE_AGE` la ausencia es real."""
    return event_date >= utc_now() - _MISS_DEFINITIVE_AGE


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
        _STATE.setdefault("calls", {})
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
    # Contadores de llamadas: retener solo la última semana.
    calls_cutoff = (date_type.today() - timedelta(days=7)).isoformat()
    state["calls"] = {k: v for k, v in state["calls"].items() if k >= calls_cutoff}
    try:
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        logger.warning("[RESULTS] No se pudo guardar provider_state.json: %s", exc)


def count_provider_call(provider_name: str) -> None:
    """Cuenta una llamada HTTP real al proveedor (métrica por día).

    Persistido en `provider_state.json` bajo `calls` — alimenta
    `providers_snapshot` para la vista de sistema. Sirve para
    vigilar el volumen ante Cloudflare (sofascore_direct) y el
    consumo real de cada suscripción.
    """
    today = date_type.today().isoformat()
    calls = _load_state()["calls"].setdefault(today, {})
    calls[provider_name] = int(calls.get(provider_name, 0)) + 1
    _save_state()


def mark_rate_limited(provider_name: str) -> None:
    """Marca un proveedor como sin cuota por el resto del día.

    Solo la PRIMERA marca del día dispara el push a admins (un provider
    caído recibe 429 en cada intento; re-notificar sería spam).
    """
    today = date_type.today().isoformat()
    state = _load_state()["rate_limited"]
    if state.get(provider_name) == today:
        return
    state[provider_name] = today
    _save_state()
    _notify_rate_limited(provider_name)


def _notify_rate_limited(provider_name: str) -> None:
    """Programa el push "cuota agotada" si hay un loop async corriendo.

    `mark_rate_limited` también se llama desde scripts síncronos y tests:
    sin loop no hay push (el estado ya quedó persistido), y en tests
    (`NODE_ENV=test`) se salta siempre para no tocar la BD real.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    from app.core.config import get_settings  # import perezoso: base es util base
    from app.services.notifications.push import notify_provider_rate_limited

    if get_settings().node_env == "test":
        return
    loop.create_task(notify_provider_rate_limited(provider_name))


def providers_snapshot() -> dict:
    """Estado actual de providers para la vista de sistema (solo admin).

    `missed_by_provider` agrega las claves de miss (`provider|fecha|evento`,
    o `odds|provider|...` para misses del snapshotter de cuotas).
    """
    state = _load_state()
    missed_by_provider: dict[str, int] = {}
    for key in state["missed"]:
        prov_key = key[5:] if key.startswith("odds|") else key
        provider = prov_key.split("|", 1)[0]
        missed_by_provider[provider] = missed_by_provider.get(provider, 0) + 1
    today = date_type.today().isoformat()
    calls = state.get("calls", {})
    return {
        "rate_limited": dict(state["rate_limited"]),
        "missed_by_provider": missed_by_provider,
        "calls_today": dict(calls.get(today, {})),
        "calls_by_day": dict(calls),
    }


def is_rate_limited(provider_name: str) -> bool:
    """True si el proveedor agotó su cuota hoy (429/403) y hay que saltarlo."""
    return (
        _load_state()["rate_limited"].get(provider_name)
        == date_type.today().isoformat()
    )


def mark_missed(key: str) -> None:
    """Recuerda que `key` ya se buscó sin resultado (ver _MISSED_TTL).

    Se guarda con hora (no solo fecha) para que los TTL provisionales
    sub-diarios (`_MISSED_TTL_PROVISIONAL`) tengan granularidad real.
    """
    _load_state()["missed"][key] = utc_now().isoformat()
    _save_state()


def is_missed(key: str, ttl: Optional[timedelta] = None) -> bool:
    """True si `key` ya se buscó sin resultado dentro del TTL.

    - `ttl=None` (definitivo): compara por fecha con `_MISSED_TTL` (15 días).
    - `ttl` explícito (provisional): compara por timestamp — permite
      ventanas de horas para reintentos de partidos recién terminados.
    """
    checked_on = _load_state()["missed"].get(key)
    if checked_on is None:
        return False
    if ttl is None:
        return checked_on >= (date_type.today() - _MISSED_TTL).isoformat()
    return checked_on >= (utc_now() - ttl).isoformat()


def rate_limit_from(exc: BaseException) -> bool:
    """True si la excepción HTTP es un problema de cuota/acceso (403, 429)."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
        403,
        429,
    )
