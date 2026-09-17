"""Verificación automática de picks pendientes contra resultados reales.

Estrategia (mercados soportados: ganador, hándicap asiático, over/under,
doble oportunidad, ambos marcan, empate no válido):
1. Busca ParsedPick con `es_apuesta=True`, `acierto` aún sin verificar y
   `fecha_evento` en el pasado (el partido ya se habrá jugado).
2. Según el `mercado`, resuelve de forma distinta:
   - "ganador": compara el equipo predicho contra el ganador real.
   - "hándicap asiático": aplica la línea (con signo) al marcador del
     equipo antes de comparar. Con líneas enteras puede haber "push"
     (empate técnico) → se marca como `anulada`, no como acierto/fallo.
   - "over/under": compara el total de goles contra la línea, o los
     goles del equipo si la selección lo nombra ("Betis más de 1.5").
     Solo se resuelven líneas de GOLES: córners, tarjetas, tiros,
     primera parte, etc. quedan para revisión manual — verificarlas con
     el marcador sería un falso resultado. También puede haber "push"
     con líneas enteras.
   - "doble oportunidad": "1X" (local o empate), "X2" (empate o
     visitante), "12" (cualquiera gana) o "Equipo y/o empate".
   - "ambos marcan" (BTTS): sí/no según marquen ambos equipos.
   - "empate no válido" / "resultado sin empate" / "draw no bet":
     empate → `anulada` en vez de fallo.
3. Consulta primero football-data.org (ligas top); si no encuentra el
   partido, intenta con API-Football (más cobertura, límite más bajo).
4. Si ningún proveedor encuentra el partido, o el mercado no está
   soportado, el pick queda pendiente para revisión manual (no se
   inventa un resultado).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.services.results.api_football import ApiFootballProvider
from app.services.results.api_tennis import ApiTennisProvider
from app.services.results.base import MatchResult, MatchStats, ResultsProvider
from app.services.results.football_data import FootballDataProvider
from app.services.results.rapidapi_tennis import RapidApiTennisProvider
from app.services.results.tennisapi1 import TennisApi1Provider

logger = get_logger("app.results.verifier")

_WIN_KEYWORDS = ["gana", "ganará", "ganara", "ganador", "vence"]
# Mercados en los que la "selección" es solo el nombre del equipo (sin
# verbo "gana") pero el mercado en sí ya implica un resultado directo.
_NO_VERB_WIN_MARKETS = [
    "resultado sin empate",
    "empate no válido",
    "draw no bet",
    "ganador",
    "gana el partido",
    "vencedor",
    "moneyline",
]
_MIN_TEAM_SIMILARITY = 0.6

# Alias de `deporte` a su nombre canónico (minúsculas, sin tilde). Cada
# proveedor declara los canónicos que cubre en `SUPPORTED_SPORTS`. Un
# pick de tenis/baloncesto nunca se consulta contra APIs de fútbol: con
# la similitud laxa de nombres podría "verificarse" contra un partido
# que nada tiene que ver (falso positivo real ya detectado). Si ningún
# proveedor cubre el deporte, el pick queda pendiente de revisión manual.
_SPORT_ALIASES = {
    "fútbol": "futbol",
    "futbol": "futbol",
    "football": "futbol",
    "soccer": "futbol",
    "fútbol sala": "futbol",
    "futsal": "futbol",
    "tenis": "tenis",
    "tennis": "tenis",
    "atp": "tenis",
    "wta": "tenis",
    "itf": "tenis",
    "challenger": "tenis",
    "baloncesto": "baloncesto",
    "basketball": "baloncesto",
    "basket": "baloncesto",
    "nba": "baloncesto",
    "acb": "baloncesto",
    "euroliga": "baloncesto",
    "euroleague": "baloncesto",
}

# Edad máxima del evento para seguir intentando la verificación
# automática. Un pick que lleva más de este tiempo sin resolverse (liga
# no cubierta, nombre irreconocible, mercado no soportado) se reintentaba
# en cada ciclo quemando llamadas a las APIs para nada; pasada esta
# ventana queda pendiente de corrección manual.
_MAX_VERIFICATION_AGE = timedelta(days=14)
# Periodo de gracia para picks creados hace poco (p. ej. recuperados por
# el catch-up o por un reproceso de raws antiguos): aunque su
# `fecha_evento` ya esté fuera de la ventana, merecen sus primeras
# pasadas — para partidos antiguos el resultado es estático, así que un
# par de ciclos bastan para resolverlos o descartarlos.
_RECENTLY_CREATED_GRACE = timedelta(hours=6)


def _should_attempt_verification(pick: ParsedPick, now: datetime) -> bool:
    """True si el pick merece un intento de verificación en esta pasada.

    Dentro de la ventana siempre; fuera de ella solo durante el periodo
    de gracia desde que se creó el pick (cubre los recuperados tarde).
    """
    if pick.fecha_evento >= now - _MAX_VERIFICATION_AGE:
        return True
    created = pick.created_at or pick.fecha_evento
    return created >= now - _RECENTLY_CREATED_GRACE


def _normalize_sport(deporte: Optional[str]) -> Optional[str]:
    """Deporte canónico del pick, o None si no hay deporte informado."""
    if not deporte:
        return None
    return _SPORT_ALIASES.get(deporte.strip().lower())


def _providers_for_sport(
    deporte: Optional[str], providers: list[ResultsProvider]
) -> list[ResultsProvider]:
    """Proveedores que cubren el deporte del pick.

    - Deporte reconocido: solo los proveedores que lo declaran en
      `SUPPORTED_SPORTS`. Si ninguno lo cubre (o no hay key configurada),
      la lista sale vacía y el pick queda pendiente.
    - Deporte desconocido: lista vacía — nunca se consulta a ciegas.
    - Sin deporte: todos los proveedores en su orden de configuración
      (los de fútbol van primero, así que un pick de tenis sin deporte
      solo llega a la API de tenis si los de fútbol no lo encontraron).
    """
    if not deporte:
        return list(providers)
    sport = _SPORT_ALIASES.get(deporte.strip().lower())
    if sport is None:
        return []
    return [
        p for p in providers if sport in getattr(p, "SUPPORTED_SPORTS", frozenset())
    ]


_MARKDOWN_NOISE = re.compile(r"[*_~`]+")
_NON_TEAM_CHARS = re.compile(r"[^\w\sÁÉÍÓÚÑáéíóúñ.'-]", re.UNICODE)
_HANDICAP_KEYWORDS = re.compile(r"h[aá]nd(?:icap)?\.?\s*asi[aá]tico", re.IGNORECASE)
_LINEA_PATTERN = re.compile(r"[+-]\s?\d+(?:[.,]\d+)?")

# Sujeto de una línea over/under que NO se puede resolver con el
# marcador final: córners, tarjetas, tiros, faltas, juegos, sets...
# Verificar "Menos de 11.0 córners" contra los goles del partido sería
# un falso resultado (casi siempre sale "acierto"). Se queda pendiente
# para revisión manual hasta tener un proveedor de estadísticas.
_OU_NON_GOALS_PATTERN = re.compile(
    r"c[oó]rner|esquina|tarjeta|card|booking|amarilla|roja|tiro|shot|"
    r"falta|foul|fuera\s+de\s+juego|offside|juego|set|punto|coche|saque",
    re.IGNORECASE,
)
# "Más de 0.5 goles 1ª parte": el marcador final no dice nada del
# descanso — no resoluble con los proveedores actuales.
_FIRST_HALF_PATTERN = re.compile(
    r"1[ªaer°]?\s*(?:parte|tiempo|mitad)|primer(?:a)?\s+(?:parte|tiempo|mitad)|"
    r"first\s+half|descanso",
    re.IGNORECASE,
)
_GOALS_PATTERN = re.compile(r"gol", re.IGNORECASE)
# Sujeto no-goleador -> tipos de estadística de API-Football
# (/fixtures/statistics). El orden importa: "amarilla"/"roja" antes que
# la "tarjeta" genérica, y "tiro a puerta" antes que "tiro".
_STAT_SUBJECTS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (re.compile(r"amarilla", re.IGNORECASE), ("Yellow Cards",)),
    (re.compile(r"roja", re.IGNORECASE), ("Red Cards",)),
    (
        re.compile(r"tarjeta|card|booking", re.IGNORECASE),
        ("Yellow Cards", "Red Cards"),
    ),
    (re.compile(r"c[oó]rner|esquina", re.IGNORECASE), ("Corner Kicks",)),
    (
        re.compile(r"tiro\s+a\s+puerta|on\s+(?:target|goal)", re.IGNORECASE),
        ("Shots on Goal",),
    ),
    (re.compile(r"tiro|shot|disparo", re.IGNORECASE), ("Total Shots",)),
    (re.compile(r"falta|foul", re.IGNORECASE), ("Fouls",)),
    (re.compile(r"fuera\s+de\s+juego|offside", re.IGNORECASE), ("Offsides",)),
)


def _stat_keys_for(text: str) -> Optional[tuple[str, ...]]:
    """Tipos de estadística del proveedor para el sujeto del texto."""
    for pattern, keys in _STAT_SUBJECTS:
        if pattern.search(text):
            return keys
    return None


# Separador entre el equipo y la línea en un over/under por equipo
# ("Real Madrid más de 1.5 goles" -> equipo = "Real Madrid").
_OU_SPLIT_PATTERN = re.compile(r"\b(?:m[aá]s|menos|over|under)\b", re.IGNORECASE)
# Línea alta sin mención de goles: en fútbol casi seguro son córners
# (líneas típicas 7.5-12.5), no goles. Sin sujeto explícito no se
# puede saber, así que se deja pendiente en vez de arriesgar.
_AMBIGUOUS_LINE_MIN = 5.0

_DOUBLE_CHANCE_PATTERN = re.compile(
    r"doble\s+oportunidad|double\s*chance|doble\s+resultado", re.IGNORECASE
)
_BTTS_YES_PATTERN = re.compile(
    r"ambos\s+(?:equipos?\s+)?(?:marcan|anotan)|both\s+teams\s+to\s+score|\bbtts\b",
    re.IGNORECASE,
)
_BTTS_NO_PATTERN = re.compile(
    r"no\s+(?:anotan|marcan)\s+ambos|ambos\s+equipos?\s+no\s+(?:marcan|anotan)|"
    r"al\s+menos\s+un\s+equipo\s+no\s+(?:marca|anota)",
    re.IGNORECASE,
)
_DRAW_NO_BET_PATTERN = re.compile(
    r"resultado\s+sin\s+empate|empate\s+no\s+v[aá]lido|draw\s+no\s+bet|"
    r"empate.{0,10}apuesta\s+no\s+v[aá]lida",
    re.IGNORECASE,
)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _clean_team_name(raw: str) -> str:
    """Quita markdown, emojis y flechas típicas de Telegram del nombre."""
    cleaned = _MARKDOWN_NOISE.sub("", raw)
    cleaned = _NON_TEAM_CHARS.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip(" -:¡!.")


def _extract_predicted_team(
    seleccion: str, mercado: Optional[str] = None
) -> Optional[str]:
    """Extrae el nombre del equipo/jugador de una selección de "ganador".

    Soporta tanto "Equipo gana" como "Gana Equipo". Si la selección es
    solo el nombre del equipo pero el `mercado` ya indica un resultado
    directo (p. ej. "resultado sin empate", "ganador"), también se
    acepta. Devuelve None para mercados que no se pueden resolver así
    (hándicap, over/under...), que se manejan con sus propios
    extractores.
    """
    low = seleccion.lower()

    # Patrón "Equipo gana" (la palabra clave aparece al final).
    for keyword in _WIN_KEYWORDS:
        idx = low.find(keyword)
        if idx > 0:
            team = _clean_team_name(seleccion[:idx])
            if team:
                return team

    # Patrón "Gana Equipo" (la palabra clave aparece al principio).
    for keyword in _WIN_KEYWORDS:
        if low.startswith(keyword):
            team = _clean_team_name(seleccion[len(keyword) :])
            if team:
                return team

    # Selección = solo el nombre del equipo, pero el mercado ya implica
    # "gana" (p. ej. "resultado sin empate").
    if mercado and any(m in mercado.lower() for m in _NO_VERB_WIN_MARKETS):
        team = _clean_team_name(seleccion)
        if team:
            return team

    return None


def _extract_handicap_team(seleccion: str) -> Optional[str]:
    """Extrae el equipo de una selección de hándicap asiático.

    P. ej. "Real Sociedad B Hándicap Asiático +1.5" -> "Real Sociedad B".
    """
    without_line = _LINEA_PATTERN.sub("", seleccion)
    without_keyword = _HANDICAP_KEYWORDS.sub("", without_line)
    team = _clean_team_name(without_keyword)
    return team or None


# La dirección se detecta por palabra completa: "under" es subcadena
# de "over/under" (valor típico del campo `mercado`), así que un match
# de subcadena convertía cualquier "más de" en under. Y si el texto
# contiene las dos ("over/under goles"), no dice la dirección: None.
_UNDER_PATTERN = re.compile(r"\bunder\b|\bmenos\b", re.IGNORECASE)
_OVER_PATTERN = re.compile(r"\bover\b|\bm[aá]s\b", re.IGNORECASE)


def _detect_over_under_direction(text: str) -> Optional[str]:
    """Detecta si una selección/mercado de over-under es "over" o "under"."""
    has_under = _UNDER_PATTERN.search(text) is not None
    has_over = _OVER_PATTERN.search(text) is not None
    if has_under and not has_over:
        return "under"
    if has_over and not has_under:
        return "over"
    return None


def _resolve_winner(match: MatchResult) -> Optional[str]:
    """Devuelve el nombre del equipo ganador, o None si hubo empate."""
    if match.home_score == match.away_score:
        return None
    return match.home_team if match.home_score > match.away_score else match.away_team


def _resolve_asian_handicap(
    match: MatchResult, predicted_team: str, linea: float
) -> tuple[Optional[bool], bool]:
    """Resuelve un pick de hándicap asiático.

    Devuelve (acierto, anulada). Con líneas enteras puede haber "push"
    (empate técnico tras aplicar la línea) → (None, True).
    """
    home_similarity = _similar(predicted_team, match.home_team)
    away_similarity = _similar(predicted_team, match.away_team)

    if max(home_similarity, away_similarity) < _MIN_TEAM_SIMILARITY:
        return None, False

    if home_similarity >= away_similarity:
        team_score, opponent_score = match.home_score, match.away_score
    else:
        team_score, opponent_score = match.away_score, match.home_score

    adjusted = team_score + linea
    if adjusted > opponent_score:
        return True, False
    if adjusted < opponent_score:
        return False, False
    return None, True  # push: apuesta anulada/devuelta


def _compare_over_under(
    total: int, direction: str, linea: float
) -> tuple[Optional[bool], bool]:
    """Compara un total (goles, córners...) contra la línea apostada."""
    if direction == "over":
        if total > linea:
            return True, False
        if total < linea:
            return False, False
        return None, True
    # direction == "under"
    if total < linea:
        return True, False
    if total > linea:
        return False, False
    return None, True


def _resolve_over_under(
    match: MatchResult, direction: str, linea: float
) -> tuple[Optional[bool], bool]:
    """Resuelve un pick de over/under sobre el total de goles del partido.

    Devuelve (acierto, anulada). Con líneas enteras puede haber "push".
    """
    return _compare_over_under(match.home_score + match.away_score, direction, linea)


def _stat_total(
    stats: MatchStats, keys: tuple[str, ...], team: Optional[str]
) -> Optional[int]:
    """Suma de la(s) estadística(s): total del partido o de un equipo.

    None si el proveedor no trajo ninguna de las claves pedidas o el
    equipo no se reconoce en el fixture.
    """
    if not any(key in stats.values for key in keys):
        return None

    def side_total(idx: int) -> int:
        return sum(stats.values.get(key, (0, 0))[idx] for key in keys)

    if team is None:
        return side_total(0) + side_total(1)
    home_similarity = _similar(team, stats.home_team)
    away_similarity = _similar(team, stats.away_team)
    if max(home_similarity, away_similarity) < _MIN_TEAM_SIMILARITY:
        return None
    return side_total(0) if home_similarity >= away_similarity else side_total(1)


def _resolve_stat_over_under(
    stats: MatchStats,
    keys: tuple[str, ...],
    team: Optional[str],
    direction: str,
    linea: float,
) -> tuple[Optional[bool], bool]:
    """Over/under sobre una estadística del partido (córners, tarjetas...)."""
    total = _stat_total(stats, keys, team)
    if total is None:
        return None, False
    return _compare_over_under(total, direction, linea)


def _match_team_scores(match: MatchResult, team: str) -> tuple[int, int] | None:
    """Goles (equipo, rival) si `team` se parece a alguno de los dos."""
    home_similarity = _similar(team, match.home_team)
    away_similarity = _similar(team, match.away_team)
    if max(home_similarity, away_similarity) < _MIN_TEAM_SIMILARITY:
        return None
    if home_similarity >= away_similarity:
        return match.home_score, match.away_score
    return match.away_score, match.home_score


def _resolve_team_over_under(
    match: MatchResult, team: str, direction: str, linea: float
) -> tuple[Optional[bool], bool]:
    """Over/under sobre los goles de UN equipo ("Betis más de 1.5")."""
    scores = _match_team_scores(match, team)
    if scores is None:
        return None, False
    goals, _ = scores
    return _compare_over_under(goals, direction, linea)


def _extract_team_total_team(seleccion: str) -> Optional[str]:
    """Equipo de un over/under por equipo, si la selección lo nombra.

    "Real Madrid más de 1.5 goles" -> "Real Madrid"; "Más de 2.5
    goles" (sin equipo delante) -> None = total del partido.
    """
    split = _OU_SPLIT_PATTERN.search(seleccion)
    if not split or split.start() == 0:
        return None
    team = _clean_team_name(seleccion[: split.start()])
    return team or None


def _resolve_double_chance(
    match: MatchResult, seleccion: str
) -> tuple[Optional[bool], bool]:
    """Doble oportunidad: "1X" / "X2" / "12" o "Equipo y/o empate"."""
    low = seleccion.strip().lower()
    if re.fullmatch(r"1x", low):
        return match.home_score >= match.away_score, False
    if re.fullmatch(r"x2", low):
        return match.away_score >= match.home_score, False
    if re.fullmatch(r"12", low) or "cualquiera" in low:
        return match.home_score != match.away_score, False
    # "Levante y empate" / "Empate o Betis": gana si el equipo gana o empata.
    team = _clean_team_name(
        re.sub(
            r"\bempate\b|\by\b|\bo\b|\bor\b|\band\b|\be\b", " ", seleccion, flags=re.I
        )
    )
    if not team:
        return None, False
    scores = _match_team_scores(match, team)
    if scores is None:
        return None, False
    team_score, opponent_score = scores
    return team_score >= opponent_score, False


def _resolve_btts(match: MatchResult, yes: bool) -> tuple[bool, bool]:
    """Ambos equipos marcan: yes=True exige gol de los dos."""
    both_scored = match.home_score > 0 and match.away_score > 0
    return (both_scored if yes else not both_scored), False


async def _get_providers() -> list[ResultsProvider]:
    settings = get_settings()
    providers: list[ResultsProvider] = []
    if settings.football_data_api_key:
        providers.append(FootballDataProvider(settings.football_data_api_key))
    if settings.api_football_key:
        providers.append(
            ApiFootballProvider(settings.api_football_key, settings.api_football_host)
        )
    # Tenis va después de los de fútbol: sin `deporte` informado solo se
    # consulta si los de fútbol no encontraron el partido. Y dentro de
    # tenis, TheSportsDB (gratis) primero y RapidAPI (cuota limitada) de
    # último recurso para Challenger/ITF.
    if settings.api_tennis_key:
        providers.append(ApiTennisProvider(settings.api_tennis_key))
    if settings.rapidapi_tennis_key:
        providers.append(
            RapidApiTennisProvider(
                settings.rapidapi_tennis_key, settings.rapidapi_tennis_host
            )
        )
        # Último nivel: tennisapi1 (Sofascore). Misma key de cuenta; su
        # cuota diaria es independiente y solo se gasta si los dos
        # anteriores no encontraron el partido o agotaron la suya.
        providers.append(
            TennisApi1Provider(
                settings.rapidapi_tennis_key, settings.rapidapi_tennisapi1_host
            )
        )
    return providers


async def _find_match_across_providers(
    date: datetime,
    team_hint: str,
    providers: list[ResultsProvider],
    pick_id: Optional[int],
) -> Optional[MatchResult]:
    for provider in providers:
        try:
            match = await provider.find_match(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando proveedor para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if match:
            return match
    return None


async def _find_stats_across_providers(
    date: datetime,
    team_hint: str,
    providers: list[ResultsProvider],
    pick_id: Optional[int],
) -> Optional[MatchStats]:
    """Estadísticas del partido (córners, tarjetas...): solo los
    proveedores que implementan `find_match_stats` (hoy API-Football)."""
    for provider in providers:
        finder = getattr(provider, "find_match_stats", None)
        if finder is None:
            continue
        try:
            stats = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando stats para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if stats:
            return stats
    return None


async def verify_pick(
    pick: ParsedPick, providers: list[ResultsProvider]
) -> tuple[Optional[bool], bool]:
    """Intenta verificar un único pick.

    Devuelve `(acierto, anulada)`. Si todavía no se puede resolver
    (partido no encontrado, mercado no soportado...), `(None, False)`.
    """
    if not pick.fecha_evento or not pick.seleccion:
        return None, False

    providers_for_sport = _providers_for_sport(pick.deporte, providers)
    if not providers_for_sport:
        return None, False

    sport = _normalize_sport(pick.deporte)
    mercado_low = (pick.mercado or "").lower()
    es_handicap = "hándicap" in mercado_low or "handicap" in mercado_low
    es_over_under = "over" in mercado_low or "under" in mercado_low

    if sport == "tenis" and (es_handicap or es_over_under):
        # En tenis los proveedores devuelven sets ganados, no juegos:
        # un "Más de 20.5 juegos" o un hándicap de juegos no se puede
        # resolver con esos números. Solo el mercado "ganador" aplica.
        return None, False

    if any(
        m in mercado_low
        for m in ("combinada", "combinado", "acumulador", "múltiple", "multiple")
    ):
        # Una combinada no se resuelve con un solo marcador: hay que
        # verificar cada selección por separado. Sin eso, la rama de
        # "ganador" solo comprobaría la primera y daría un falso
        # resultado. Manual hasta modelar las selecciones sueltas.
        return None, False

    # Mercados de fútbol que se resuelven solo con el marcador final.
    combined = f"{pick.mercado or ''} {pick.seleccion or ''}"
    if sport in ("futbol", None):
        if _DOUBLE_CHANCE_PATTERN.search(combined):
            sel_clean = (pick.seleccion or "").strip().lower()
            if re.fullmatch(r"1x|x2|12", sel_clean):
                # "1X"/"X2"/"12" no nombran equipo: localizar por evento.
                team_hint = pick.evento
            else:
                # "Levante y empate" -> "Levante" como pista de equipo.
                team_hint = (
                    _clean_team_name(
                        re.sub(
                            r"\bempate\b|\by\b|\bo\b|\bor\b|\band\b|\be\b",
                            " ",
                            pick.seleccion or "",
                            flags=re.I,
                        )
                    )
                    or pick.evento
                )
            if not team_hint:
                return None, False
            match = await _find_match_across_providers(
                pick.fecha_evento, team_hint, providers_for_sport, pick.id
            )
            if not match:
                return None, False
            return _resolve_double_chance(match, pick.seleccion or "")

        btts_no = _BTTS_NO_PATTERN.search(combined)
        btts_yes = _BTTS_YES_PATTERN.search(combined)
        if btts_yes and re.search(r"(?:-|:)\s*no\b", combined, re.IGNORECASE):
            # Formato de slip "Ambos equipos marcan - No".
            btts_no, btts_yes = btts_yes, None
        if btts_no or btts_yes:
            team_hint = pick.evento
            if not team_hint:
                return None, False
            match = await _find_match_across_providers(
                pick.fecha_evento, team_hint, providers_for_sport, pick.id
            )
            if not match:
                return None, False
            return _resolve_btts(match, yes=bool(btts_yes) and not btts_no)

    if es_handicap and pick.linea is not None:
        team_hint = _extract_handicap_team(pick.seleccion)
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers_for_sport, pick.id
        )
        if not match:
            return None, False
        return _resolve_asian_handicap(match, team_hint, pick.linea)

    if es_over_under and pick.linea is not None:
        direction = _detect_over_under_direction(
            pick.seleccion
        ) or _detect_over_under_direction(pick.mercado or "")
        if not direction:
            return None, False
        ou_text = f"{pick.seleccion or ''} {pick.mercado or ''}"
        if _FIRST_HALF_PATTERN.search(ou_text):
            # "Más de 0.5 goles 1ª parte": ni el marcador ni las stats
            # cubren el descanso — pendiente.
            return None, False
        # Over/under es sobre el total del partido o sobre un equipo
        # concreto; en ambos casos necesitamos un nombre de equipo para
        # localizar el partido en la API.
        team_total_team = _extract_team_total_team(pick.seleccion or "")
        # El evento ("Elche - Real Madrid") es la mejor pista cuando la
        # selección no nombra equipo: el texto de la línea ("Más de 2.5
        # goles") no se parece a ningún equipo y ensucia la búsqueda.
        team_hint = (
            team_total_team or pick.evento or _extract_handicap_team(pick.seleccion)
        )
        if not team_hint:
            return None, False
        if _OU_NON_GOALS_PATTERN.search(ou_text):
            # Córners/tarjetas/tiros...: no resoluble con el marcador,
            # pero API-Football sí tiene /fixtures/statistics (solo
            # dentro de la ventana de fechas del plan gratis).
            stat_keys = _stat_keys_for(ou_text)
            if not stat_keys:
                # Sujeto sin estadística equivalente (juegos, sets,
                # coches...): pendiente.
                return None, False
            stats = await _find_stats_across_providers(
                pick.fecha_evento, team_hint, providers_for_sport, pick.id
            )
            if not stats:
                return None, False
            return _resolve_stat_over_under(
                stats, stat_keys, team_total_team, direction, pick.linea
            )
        if pick.linea >= _AMBIGUOUS_LINE_MIN and not _GOALS_PATTERN.search(ou_text):
            # Línea alta sin la palabra "gol": en fútbol casi seguro son
            # córners, no goles. Mejor pendiente que mal verificado.
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers_for_sport, pick.id
        )
        if not match:
            return None, False
        if team_total_team:
            return _resolve_team_over_under(
                match, team_total_team, direction, pick.linea
            )
        return _resolve_over_under(match, direction, pick.linea)

    # Mercado "ganador" simple (o "resultado sin empate" con selección =
    # solo el nombre del equipo).
    predicted_team = _extract_predicted_team(pick.seleccion, pick.mercado)
    if not predicted_team:
        # Mercado no soportado todavía: manual.
        return None, False

    es_mercado_sin_empate = bool(
        _DRAW_NO_BET_PATTERN.search(mercado_low)
        or _DRAW_NO_BET_PATTERN.search(pick.seleccion or "")
    )

    match = await _find_match_across_providers(
        pick.fecha_evento, predicted_team, providers_for_sport, pick.id
    )
    if not match:
        return None, False

    winner = _resolve_winner(match)
    if winner is None:
        if es_mercado_sin_empate:
            # "Resultado sin empate" anula/devuelve la apuesta en caso
            # de empate en vez de perderla.
            return None, True
        return False, False  # empate: la apuesta a "gana" falla

    return _similar(predicted_team, winner) >= _MIN_TEAM_SIMILARITY, False


async def verify_pending_picks() -> int:
    """Revisa todos los picks pendientes de verificar y actualiza los que pueda.

    Devuelve cuántos picks se verificaron automáticamente en esta pasada
    (incluye tanto acierto/fallo como anuladas).
    """
    providers = await _get_providers()
    if not providers:
        logger.info(
            "[RESULTS_VERIFIER] Sin API keys de resultados configuradas "
            "(FOOTBALL_DATA_API_KEY / API_FOOTBALL_KEY); verificación "
            "automática desactivada."
        )
        return 0

    now = utc_now()
    verified_count = 0

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ParsedPick)
            .where(ParsedPick.es_apuesta == True)  # noqa: E712
            .where(ParsedPick.acierto == None)  # noqa: E711
            .where(ParsedPick.anulada == False)  # noqa: E712
            .where(ParsedPick.fecha_evento != None)  # noqa: E711
            .where(ParsedPick.fecha_evento < now)
        )
        # El filtro de edad se aplica en Python: además de la ventana de
        # 14 días hay un periodo de gracia para picks recién creados
        # (recuperados tarde por el catch-up), difícil de expresar en SQL
        # sin OR de columnas. El conjunto pendiente es pequeño.
        pending = [p for p in result.all() if _should_attempt_verification(p, now)]
        # Recientes primero: los mercados de estadísticas (córners,
        # tarjetas...) solo se pueden consultar en la ventana ±1 día de
        # API-Football gratis — si la cuota se agota a mitad de pasada,
        # que se queden fuera los picks viejos, no los que caducan.
        pending.sort(key=lambda p: p.fecha_evento, reverse=True)

        logger.info(
            "[RESULTS_VERIFIER] Picks pendientes de verificar (con fecha pasada): %s",
            len(pending),
        )

        for pick in pending:
            acierto, anulada = await verify_pick(pick, providers)
            if acierto is not None or anulada:
                pick.acierto = acierto
                pick.anulada = anulada
                pick.verificado_por = "auto"
                session.add(pick)
                verified_count += 1
                logger.info(
                    "[RESULTS_VERIFIER] Pick id=%s ('%s') verificado: "
                    "acierto=%s anulada=%s",
                    pick.id,
                    pick.seleccion,
                    acierto,
                    anulada,
                )

        await session.commit()

    logger.info(
        "[RESULTS_VERIFIER] Verificación completada: %s/%s picks resueltos.",
        verified_count,
        len(pending),
    )
    return verified_count
