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
        _STATE.setdefault("loops", {})
        _STATE.setdefault("limits", {})
        _STATE.setdefault("void_rechecks", {})
    return _STATE


def _save_state() -> None:
    today = utc_now().date().isoformat()
    cutoff = (utc_now().date() - _MISSED_TTL).isoformat()
    state = _load_state()
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
    }


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
    raw = _load_state()["rate_limited"].get(provider_name)
    if not raw:
        return False
    if "T" not in raw:
        return raw == utc_now().date().isoformat()
    try:
        return datetime.fromisoformat(raw) > utc_now()
    except ValueError:
        return False


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


def rate_limit_from(exc: BaseException) -> bool:
    """True si la excepción HTTP es un problema de cuota/acceso (403, 429)."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
        403,
        429,
    )
