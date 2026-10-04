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
    # Guardamos el límite diario observado en los headers: la vista de
    # sistema lo muestra como "llamadas/límite" sin hardcodear caps.
    if limit != "?":
        try:
            _load_state()["limits"][provider_name] = int(limit)
            _save_state()
        except ValueError:
            pass
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


# Acrónimos/apodos que el tipster usa y los providers no: se expanden
# antes de comparar nombres. Lista conservadora — solo variantes sin
# ambigüedad ("inter" queda fuera: puede ser Inter de Milán, Inter
# Miami o Inter de Limeira). Compartido entre la capa de resultados
# (`footapi_stats`) y la de cuotas (`snapshotter._lookup_hint`).
TEAM_ALIASES = {
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "paris fc femenino": "paris fc",
    "man utd": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham",
    "wolves": "wolverhampton",
    "depò": "deportivo",
    "depo": "deportivo",
    "barça": "barcelona",
    "barca": "barcelona",
    "atm": "atletico madrid",
    "atleti": "atletico madrid",
    "betis": "real betis",
    "sevilla fc": "sevilla",
    "real madrid cf": "real madrid",
    "inter milan": "inter",
    "milan": "ac milan",
    "river": "river plate",
    "boca": "boca juniors",
    "bayern": "bayern munich",
    "dortmund": "borussia dortmund",
    "napoles": "napoli",
    "roma": "as roma",
    "deportivo de la coruña": "la coruna",
    "deportivo de la coruna": "la coruna",
    "deportivo la coruña": "la coruna",
    "deportivo la coruna": "la coruna",
    "deportivo de a coruña": "la coruna",
    "deportivo de a coruna": "la coruna",
    "vfb stuttgart": "stuttgart",
    "cologne": "fc koln",
    "fc colonia": "fc koln",
    "saint-truidense": "st truiden",
    "sint-truidense": "st truiden",
    "st truidense": "st truiden",
    "sint truidense": "st truiden",
    "union saint-gilloise": "union st gilloise",
    "union saint gilloise": "union st gilloise",
    "union sg": "union st gilloise",
}


def translate_team_aliases(text: str) -> str:
    """Expande aliases de equipo en un hint: 'psg - barça' ->
    'paris saint germain - barcelona' (palabra entera, en minúsculas).

    Se aplica al HINT del pick (nunca al nombre del provider) antes de
    `match_score` o de una búsqueda por nombre — un alias ambiguo no
    casa por sí solo porque `match_score` sigue exigiendo 0.6 y ±1 día.
    """
    out = (text or "").lower()
    for alias, full in TEAM_ALIASES.items():
        out = re.sub(rf"\b{re.escape(alias)}\b", full, out)
    return out


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
    # Fecha real del evento según el proveedor: los que responden con
    # listados de "días cercanos" pueden devolver un partido de otra
    # fecha — con esto el sondeo de fecha escribe la exacta.
    event_date: Optional[datetime] = None


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
# - "rate_limited": proveedor -> fecha ISO (cuota del día agotada: se
#   salta hasta mañana) o timestamp ISO (cooldown por ritmo, o aparcado
#   hasta el reset real del periodo — planes mensuales).
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
        _STATE.setdefault("loops", {})
        _STATE.setdefault("limits", {})
        _STATE.setdefault("void_rechecks", {})
        _STATE.setdefault("summaries", {})
    return _STATE


def _save_state() -> None:
    today = utc_now().date().isoformat()
    cutoff = (utc_now().date() - _MISSED_TTL).isoformat()
    state = _load_state()
    # Merge con disco antes de escribir: el backend mantiene `_STATE`
    # en memoria y un dump ciego pisaría las marcas de procesos
    # paralelos (backfill manual, scripts) que escribieron entretanto.
    # En conflicto por la MISMA clave gana la copia en memoria — es la
    # escritura más reciente (un max() resucitaría bloqueos acortados
    # tras resetear la racha de backoff).
    try:
        disk = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        disk = {}
    for section in (
        "rate_limited",
        "missed",
        "limits",
        "void_rechecks",
        "loops",
        "summaries",
    ):
        state[section] = {**(disk.get(section) or {}), **state.get(section, {})}
    merged_calls: dict[str, dict[str, int]] = {
        d: dict(p) for d, p in (disk.get("calls") or {}).items()
    }
    for day, providers in (state.get("calls") or {}).items():
        target = merged_calls.setdefault(day, {})
        for name, n in providers.items():
            target[name] = max(int(target.get(name, 0)), int(n))
    state["calls"] = merged_calls
    # Podar entradas viejas para que el archivo no crezca sin límite.
    state["rate_limited"] = {
        k: v for k, v in state["rate_limited"].items() if v >= today
    }
    state["missed"] = {k: v for k, v in state["missed"].items() if v >= cutoff}
    # Contadores de llamadas: retener solo la última semana.
    calls_cutoff = (utc_now().date() - timedelta(days=7)).isoformat()
    state["calls"] = {k: v for k, v in state["calls"].items() if k >= calls_cutoff}
    # Registro del barrido de anuladas: último mes.
    rechecks_cutoff = (utc_now().date() - timedelta(days=30)).isoformat()
    state["void_rechecks"] = {
        k: v for k, v in state["void_rechecks"].items() if k >= rechecks_cutoff
    }
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
    today = utc_now().date().isoformat()
    calls = _load_state()["calls"].setdefault(today, {})
    calls[provider_name] = int(calls.get(provider_name, 0)) + 1
    _save_state()


# Cooldown corto cuando el 403/429 fue por RITMO (ráfaga), no por
# cuota: RapidAPI free tier también limita req/min, y aparcar el
# provider todo el día por un pico dejaba llamadas sin usar.
_RATE_LIMIT_COOLDOWN = timedelta(minutes=15)
_RATE_LIMIT_COOLDOWN_MAX = timedelta(hours=1)


def _cooldown_from(response: httpx.Response | None) -> timedelta | None:
    """Cooldown a aplicar si el 403/429 fue por ritmo y no por cuota.

    - `retry-after` del server (capado a 1 h, mínimo 60 s).
    - `x-ratelimit-requests-remaining` > 0: el propio RapidAPI admite
      que queda cuota, así que el 429 era de ráfaga -> 15 min.
    Sin response/headers o remaining=0 -> None: cuota real agotada.
    """
    if response is None:
        return None
    headers = getattr(response, "headers", None) or {}
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            secs = min(
                max(float(retry_after), 60.0),
                _RATE_LIMIT_COOLDOWN_MAX.total_seconds(),
            )
            return timedelta(seconds=secs)
        except ValueError:
            pass
    remaining = headers.get("x-ratelimit-requests-remaining")
    if remaining is not None:
        try:
            if int(remaining) > 0:
                return _RATE_LIMIT_COOLDOWN
        except ValueError:
            pass
    return None


def _quota_reset_at(response: httpx.Response | None) -> datetime | None:
    """Instante real del reset de cuota según RapidAPI.

    `x-ratelimit-requests-reset` trae los segundos que faltan para
    renovar el periodo de facturación de la suscripción: horas en
    planes diarios, semanas en mensuales (p.ej. 200 req/mes → reset
    en ~20 días). Devuelve el datetime del reset o None si el header
    no viene — el llamador cae entonces al aparcado clásico del día.
    """
    if response is None:
        return None
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("x-ratelimit-requests-reset")
    if raw is None:
        return None
    try:
        secs = float(raw)
    except ValueError:
        return None
    if secs <= 0:
        return None
    return utc_now() + timedelta(seconds=secs)


def mark_rate_limited(
    provider_name: str, response: httpx.Response | None = None
) -> None:
    """Marca un proveedor como bloqueado.

    Con `response` se distingue cuota real de límite de ritmo: si el
    server admite que queda cuota (`remaining`>0 o `retry-after`), el
    bloqueo es un cooldown de minutos guardado como timestamp; sin esa
    evidencia se aparca el día entero (fecha ISO) como siempre. Solo la
    primera marca de día completo dispara push a admins — un cooldown
    no notifica: en una ráfaga sería spam.
    """
    today = utc_now().date().isoformat()
    state = _load_state()["rate_limited"]
    if state.get(provider_name) == today:
        return  # aparcado el día entero: no degradar a cooldown
    cooldown = _cooldown_from(response)
    if cooldown is None:
        # Cuota realmente agotada. RapidAPI reporta el instante del
        # reset (`x-ratelimit-requests-reset`): en planes mensuales
        # puede ser dentro de semanas — aparcar "solo hoy" gastaba
        # una llamada inútil cada día hasta el reset (caso oddspapi).
        reset_at = _quota_reset_at(response)
        if reset_at is not None:
            prev = state.get(provider_name)
            already_parked = False
            if prev and "T" in prev:
                try:
                    already_parked = datetime.fromisoformat(prev) > utc_now()
                except ValueError:
                    already_parked = False
            state[provider_name] = reset_at.isoformat()
            _save_state()
            logger.info(
                "[QUOTA] %s: cuota del periodo agotada — aparcado hasta %s UTC",
                provider_name,
                reset_at.strftime("%Y-%m-%d %H:%M"),
            )
            if not already_parked:
                _notify_rate_limited(provider_name)
            return
        state[provider_name] = today
        _save_state()
        _notify_rate_limited(provider_name)
        return
    until = utc_now() + cooldown
    state[provider_name] = until.isoformat()
    _save_state()
    logger.info(
        "[QUOTA] %s: 403/429 con cuota restante — cooldown hasta %s UTC",
        provider_name,
        until.strftime("%H:%M"),
    )


_RETRY_HINT = re.compile(r"(?:retry|try again) in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?")


def retry_hint_seconds(resp: httpx.Response) -> Optional[float]:
    """Segundos que pide esperar un 429 ("retry/try again in XhYmZs"
    en el cuerpo o header Retry-After), o None si no trae hint."""
    raw = (getattr(resp, "headers", None) or {}).get("retry-after")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    match = _RETRY_HINT.search(resp.text or "")
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (
        float(group) if group else 0.0 for group in match.groups()
    )
    total = hours * 3600 + minutes * 60 + seconds
    return total or None


def mark_cooldown(provider_name: str, until: datetime) -> None:
    """Registra un cooldown ya calculado por el provider para la vista
    de sistema (p. ej. el de 20 min de Gemini por RPM del free tier).

    A diferencia de `mark_rate_limited` no decide nada — el provider ya
    se pausó solo; esto solo lo hace visible en `providers_snapshot`
    como bloqueo activo hasta `until`. Nunca notifica ni aparca el día.
    """
    _load_state()["rate_limited"][provider_name] = until.isoformat()
    _save_state()


def mark_rate_limited_escalating(
    provider_name: str, base_hours: int = 24, max_hours: int = 168
) -> None:
    """Bloqueo con backoff progresivo para baneos de reputación.

    A diferencia de `mark_rate_limited` (fin de cuota: se aparca el
    día), un baneo de Cloudflare por IP empeora si se insiste a diario
    — el toque periódico renueva la señal de bot ante el edge. Cada
    bloqueo consecutivo multiplica x3 el descanso (24h -> 72h -> 7d
    máx.); la racha vive en `rl_streak` y la resetea
    `clear_rate_limit_streak` tras la primera respuesta buena.
    """
    state = _load_state()
    streaks = state.setdefault("rl_streak", {})
    streak = int(streaks.get(provider_name, "0")) + 1
    streaks[provider_name] = str(streak)
    hours = min(base_hours * 3 ** (streak - 1), max_hours)
    until = utc_now() + timedelta(hours=hours)
    state["rate_limited"][provider_name] = until.isoformat()
    _save_state()
    logger.info(
        "[QUOTA] %s: baneo #%d — descanso de %dh hasta %s UTC",
        provider_name,
        streak,
        hours,
        until.strftime("%Y-%m-%d %H:%M"),
    )


def clear_rate_limit_streak(provider_name: str) -> None:
    """Resetea la racha de backoff tras una respuesta buena."""
    state = _load_state()
    if state.get("rl_streak", {}).pop(provider_name, None) is not None:
        _save_state()


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


def count_purpose_call(mark: str, purpose: str) -> None:
    """Llamada por propósito ('cuotas'/'resultados'/'stats').

    Desglosa `calls` para la vista de sistema: una fila `groq-odds`
    puede mostrar cuántas llamadas fueron de cuotas y cuántas de
    resultados sin mezclar mundos. Persistido bajo `call_purpose`
    (`{fecha: {marca: {propósito: n}}}`).
    """
    if not purpose:
        return
    today = utc_now().date().isoformat()
    per = _load_state().setdefault("call_purpose", {}).setdefault(today, {})
    marks = per.setdefault(mark, {})
    marks[purpose] = int(marks.get(purpose, 0)) + 1
    _save_state()


def record_extractor_result(name: str, ok: bool) -> None:
    """Éxito/fallo por extractor LLM (groq, cerebras, gemini...).

    `ok` = devolvió texto usable; `fail` = error/timeout/vacío.
    Termómetro de calidad real por endpoint para la vista de
    sistema — decide si un refuerzo de cascada merece la pena.
    """
    today = utc_now().date().isoformat()
    stats = _load_state().setdefault("extract_stats", {}).setdefault(today, {})
    bucket = stats.setdefault(name, {"ok": 0, "fail": 0})
    bucket["ok" if ok else "fail"] += 1
    _save_state()


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
        # "empty" no es un provider: el snapshotter marca así los eventos
        # consultados que no tenían mercados de cuotas.
        if provider == "empty":
            provider = "eventos-sin-mercados"
        missed_by_provider[provider] = missed_by_provider.get(provider, 0) + 1
    # El state guarda fechas UTC (utc_now); filtrar con la fecha local
    # vacía la vista durante las ~2h tras medianoche local en las que
    # aún no cambió el día UTC.
    today = utc_now().date().isoformat()
    calls = state.get("calls", {})
    now = utc_now()
    # Solo bloqueos activos: un cooldown ya expirado (timestamp pasado)
    # sigue en el state hasta la próxima poda, pero no debe pintarse
    # como "sin cuota" en la vista de sistema.
    active_limited = {
        k: v
        for k, v in state["rate_limited"].items()
        if (v > now.isoformat() if "T" in v else v == today)
    }
    return {
        "rate_limited": active_limited,
        "missed_by_provider": missed_by_provider,
        "calls_today": dict(calls.get(today, {})),
        "calls_by_day": dict(calls),
        "daily_limits": dict(state.get("limits", {})),
        # Desglose por propósito y efectividad de los extractores LLM
        # (llm-compat-*, gemini): marca -> {propósito: n} y
        # extractor -> {ok, fail} del día.
        "calls_by_purpose": dict(state.get("call_purpose", {}).get(today, {})),
        "extract_stats": dict(state.get("extract_stats", {}).get(today, {})),
    }


_ODDS_ROWS_DAY_LIMIT = 14


def count_odds_rows_written(n: int) -> None:
    """Cuenta filas de cuotas escritas hoy (métrica por día).

    `odds_snapshots.captured_at` puede ser un timestamp histórico
    real (curvas de OddsPapi), así que no sirve para medir cuánto
    escribió el sistema cada día — este contador sí: lo llaman el
    snapshotter y el backfill una vez por pasada, tras el commit.
    Persistido en `provider_state.json` bajo `odds_rows_by_day`
    (últimos 14 días).
    """
    if n <= 0:
        return
    state = _load_state()
    by_day = state.setdefault("odds_rows_by_day", {})
    today = utc_now().date().isoformat()
    by_day[today] = int(by_day.get(today, 0)) + n
    # Poda: conservar solo los últimos N días.
    if len(by_day) > _ODDS_ROWS_DAY_LIMIT:
        for old in sorted(by_day)[:-_ODDS_ROWS_DAY_LIMIT]:
            del by_day[old]
    _save_state()


def odds_rows_by_day() -> dict[str, int]:
    """Filas de cuotas escritas por día UTC, más reciente primero."""
    by_day = _load_state().get("odds_rows_by_day", {})
    return dict(sorted(by_day.items(), reverse=True))


def record_void_recheck(stats: dict[str, int]) -> None:
    """Registra el resumen de una pasada correctiva de anuladas.

    Acumulado por día UTC en `provider_state.json` ("void_rechecks"):
    {"procesadas", "confirmadas", "corregidas", "sin_datos"}. Es el
    registro que muestra la pantalla de depuración — una línea por día,
    no una por pick.
    """
    today = utc_now().date().isoformat()
    log = _load_state()["void_rechecks"]
    entry = log.setdefault(today, {})
    for key, n in stats.items():
        entry[key] = int(entry.get(key, 0)) + int(n)
    _save_state()


def void_rechecks_snapshot() -> dict[str, dict[str, int]]:
    """Resumen por día del barrido correctivo, más reciente primero."""
    log = _load_state().get("void_rechecks", {})
    return {dia: dict(log[dia]) for dia in sorted(log, reverse=True)[:14]}


def is_rate_limited(provider_name: str) -> bool:
    """True si el proveedor está bloqueado y hay que saltarlo.

    El valor guardado es una fecha ISO (sin cuota: dura todo el día)
    o un timestamp ISO (cooldown por ritmo: dura hasta ese instante —
    ver `mark_rate_limited`)."""
    until = rate_limited_until(provider_name)
    return until is not None and until > utc_now()


def rate_limited_until(provider_name: str) -> datetime | None:
    """Instante hasta el que el provider está aparcado, o None.

    Fecha ISO desnuda -> fin de ese día UTC; timestamp -> el instante.
    Sirve para distinguir cooldown de ritmo (minutos) de quota
    agotada (días/semanas, p. ej. reset mensual de RapidAPI)."""
    raw = _load_state()["rate_limited"].get(provider_name)
    if not raw:
        return None
    if "T" not in raw:
        try:
            return datetime.fromisoformat(raw) + timedelta(days=1)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


_LOOP_HISTORY_MAX = 10


def record_loop_summary(loop_name: str, summary: dict) -> None:
    """Guarda el mini-resumen de la última pasada de un loop
    (p. ej. el backfill de cuotas: revisados, filas, llamadas).

    Persistido en `provider_state.json` bajo `summaries` — la
    pantalla de depuración muestra qué hizo la última pasada sin
    tener que abrir logs del contenedor. Además acumula en
    `summaries_history` las últimas `_LOOP_HISTORY_MAX` pasadas
    (más reciente al final) para poder comparar ritmo de trabajo
    entre ejecuciones."""
    state = _load_state()
    entry = {"at": utc_now().isoformat(), **summary}
    state.setdefault("summaries", {})[loop_name] = entry
    history = state.setdefault("summaries_history", {}).setdefault(loop_name, [])
    history.append(entry)
    del history[:-_LOOP_HISTORY_MAX]
    _save_state()


def loop_summary(loop_name: str) -> dict | None:
    """Último resumen registrado para `loop_name`, o None."""
    return _load_state().get("summaries", {}).get(loop_name)


def loop_history(loop_name: str) -> list[dict]:
    """Últimas pasadas registradas para `loop_name` (más reciente al
    final), o lista vacía si el loop aún no resumió ninguna."""
    return _load_state().get("summaries_history", {}).get(loop_name, [])


def mark_missed(key: str) -> None:
    """Recuerda que `key` ya se buscó sin resultado (ver _MISSED_TTL).

    Se guarda con hora (no solo fecha) para que los TTL provisionales
    sub-diarios (`_MISSED_TTL_PROVISIONAL`) tengan granularidad real.
    """
    _load_state()["missed"][key] = utc_now().isoformat()
    _save_state()


def loop_due(loop_name: str, interval_seconds: float) -> bool:
    """True si el loop de mantenimiento debe correr ya.

    Los loops de `lifecycle.py` persisten su última pasada aquí para
    que un restart/redeploy no dispare ciclos extra: antes, cada
    rebuild relanzaba verifier+snapshotter+rescate+backfill al boot y
    quemaba cuota de providers (y OpenAI) sin necesidad. Sin marca
    previa devuelve True — el primer arranque real corre igual.
    """
    raw = _load_state()["loops"].get(loop_name)
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return True
    return (utc_now() - last).total_seconds() >= interval_seconds


def mark_loop_ran(loop_name: str) -> None:
    """Registra la pasada completada de un loop (ver `loop_due`).

    Solo se marca tras un ciclo sin excepción: un ciclo fallido no
    cuenta y se reintenta en el siguiente intervalo como antes.
    """
    _load_state()["loops"][loop_name] = utc_now().isoformat()
    _save_state()


def clear_missed(key: str) -> None:
    """Borra la marca de búsqueda fallida de `key`.

    Se usa cuando cambia la premisa que hizo fallar la búsqueda — p.
    ej. el backfill corrige `fecha_evento` con la fecha real del
    fixture: las marcas "nofixture" dejadas buscando en el día
    equivocado ya no son válidas y hay que reintentar."""
    state = _load_state()
    if key in state["missed"]:
        del state["missed"][key]
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
        return checked_on >= (utc_now().date() - _MISSED_TTL).isoformat()
    return checked_on >= (utc_now() - ttl).isoformat()


def count_missed_prefix(prefix: str) -> int:
    """Cuántas marcas `missed` cuelgan de un prefijo (p. ej.
    'llm-odds|done|') — acumulado de procesados para la vista."""
    return sum(1 for k in _load_state().get("missed", {}) if k.startswith(prefix))


def rate_limit_from(exc: BaseException) -> bool:
    """True si la excepción HTTP es un problema de cuota/acceso (403, 429)."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
        403,
        429,
    )
