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
from app.services.results.api_tennis import ApiTennisProvider, _pair_similar
from app.services.results.base import (
    MatchEvents,
    MatchPlayers,
    MatchResult,
    MatchStats,
    ResultsProvider,
    match_reversed,
)
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

# Si el partido quedó aplazado/cancelado, la casa devuelve la apuesta
# cuando no se reprograma dentro de su ventana (~24-72 h según la casa;
# usamos la más conservadora). Solo cuenta el aplazamiento "limpio"
# (POSTPONED/CANCELLED): un partido parado a mitad (SUSP/ABD) puede
# tener mercados ya decididos que la casa paga igualmente — esos
# siguen pendientes.
_POSTPONED_VOID_AFTER = timedelta(hours=72)


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
# Se conserva "/": en tenis separa a los miembros de una pareja de
# dobles ("Alcaraz / Munar") y el matching por parejas lo necesita.
_NON_TEAM_CHARS = re.compile(r"[^\w\sÁÉÍÓÚÑáéíóúñ.'/-]", re.UNICODE)
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


def _handicap_outcome(team_score: int, opponent_score: int, linea: float) -> str:
    """ "win" | "lose" | "push" de aplicar la línea al equipo."""
    adjusted = team_score + linea
    if adjusted > opponent_score:
        return "win"
    if adjusted < opponent_score:
        return "lose"
    return "push"


def _resolve_asian_handicap(
    match: MatchResult, predicted_team: str, linea: float
) -> tuple[Optional[bool], bool]:
    """Resuelve un pick de hándicap asiático.

    Devuelve (acierto, anulada). Con líneas enteras puede haber "push"
    (empate técnico tras aplicar la línea) → (None, True).

    Cuartos de línea (±0.25, ±0.75): la casa la parte en dos medias
    apuestas (p. ej. +0.25 = mitad a 0, mitad a +0.5). Combinando:
    win+win → acierto, lose+lose → fallo, win+push → acierto (media
    ganada + media devuelta), lose+push → fallo (media perdida + media
    devuelta). El modelo binario no expresa "media apuesta", así que se
    aproxima al signo del resultado neto.
    """
    home_similarity = _similar(predicted_team, match.home_team)
    away_similarity = _similar(predicted_team, match.away_team)

    if max(home_similarity, away_similarity) < _MIN_TEAM_SIMILARITY:
        return None, False

    if home_similarity >= away_similarity:
        team_score, opponent_score = match.home_score, match.away_score
    else:
        team_score, opponent_score = match.away_score, match.home_score

    # Cuarto de línea -> dos medias apuestas linea±0.25.
    if int(round(linea * 4)) % 2 == 1:
        lines = (linea - 0.25, linea + 0.25)
    else:
        lines = (linea,)
    outcomes = [_handicap_outcome(team_score, opponent_score, ln) for ln in lines]

    if all(o == "win" for o in outcomes):
        return True, False
    if all(o == "lose" for o in outcomes):
        return False, False
    if all(o == "push" for o in outcomes):
        return None, True
    # Mezcla posible solo win+push o lose+push (win+lose es imposible:
    # las medias difieren en 0.5).
    return ("win" in outcomes), False


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


# --- Mercados de jugador ("Raphinha marca", "X marca o asiste") -------
#
# Se resuelven con los eventos del partido (API-Football
# /fixtures/events). Regla de las casas: si el jugador no participa la
# apuesta se anula — por eso, cuando el jugador no consta en ningún
# evento, se consulta además /fixtures/players (lista de los que
# disputaron minutos): consta que jugó -> fallo; consta que no jugó ->
# anulada; sin datos fiables -> pendiente (nunca se asume que no jugó).
_PLAYER_SCORER_PATTERN = re.compile(
    r"\bmarca\b|\banotar?[áa]?\b|goleador|scorer", re.IGNORECASE
)
_PLAYER_ASSIST_PATTERN = re.compile(r"\basist", re.IGNORECASE)
_PLAYER_CARD_PATTERN = re.compile(
    r"recibe\s+tarjeta|recibir[aá]\s+tarjeta|amonestad|sancionad|"
    r"ver[aá]\s+tarjeta|se\s+le\s+muestra\s+tarjeta|\bcarded\b",
    re.IGNORECASE,
)
# Relleno de los slips que no forma parte del nombre del jugador.
_PLAYER_MARKET_NOISE = re.compile(
    r"jugador\s+que|marca\s+o\s+asiste|marca\s+en\s+cualquier\s+momento|"
    r"marca\s+gol(?:es)?|\bmarca\b|\banotar?[áa]?\b|goleador|scorer|"
    r"anytime|primer|segundo|[úu]ltimo|recibe|recibir[aá]|amonestad[oa]?|"
    r"sancionad[oa]?|ver[aá]|se\s+le\s+muestra|\btarjeta\b|en\s+cualquier\s+momento",
    re.IGNORECASE,
)


def _extract_player_name(seleccion: str) -> Optional[str]:
    """Nombre del jugador tras quitar el texto del mercado del slip."""
    cleaned = _PLAYER_MARKET_NOISE.sub(" ", seleccion)
    cleaned = _clean_team_name(cleaned)
    return cleaned or None


def _detect_player_market(
    seleccion: str, mercado: Optional[str]
) -> Optional[tuple[str, str]]:
    """("scorer"|"scorer_or_assist"|"booked", jugador) si la selección
    es un mercado de jugador; None si no lo es."""
    text = f"{seleccion or ''} {mercado or ''}"
    player = _extract_player_name(seleccion or "")
    if not player:
        return None
    if _PLAYER_CARD_PATTERN.search(text):
        return ("booked", player)
    if _PLAYER_SCORER_PATTERN.search(text):
        mode = "scorer_or_assist" if _PLAYER_ASSIST_PATTERN.search(text) else "scorer"
        return (mode, player)
    return None


def _player_name_matches(hint: str, name: str) -> bool:
    """El jugador del pick casa con el nombre del evento.

    Acepta subconjunto de tokens ("Raphinha" ⊂ "Raphinha Dias",
    "Lewandowski" ⊂ "Robert Lewandowski") además de similitud global.
    """
    if _similar(hint, name) >= _MIN_TEAM_SIMILARITY:
        return True
    hint_tokens = set(re.findall(r"\w+", hint.lower()))
    name_tokens = set(re.findall(r"\w+", name.lower()))
    return bool(hint_tokens) and hint_tokens <= name_tokens


def _resolve_player_market(
    events: MatchEvents,
    player: str,
    mode: str,
    played: Optional[list[str]] = None,
) -> tuple[Optional[bool], bool]:
    """Resuelve "X marca" / "X marca o asiste" / "X recibe tarjeta".

    `played` es la lista de jugadores que disputaron minutos
    (/fixtures/players), si el proveedor la tiene: con ella se
    distingue "jugó sin acertar el mercado" (fallo) de "no jugó"
    (anulada — la casa devuelve). Sin ella, un jugador sin eventos
    queda pendiente.
    """

    def hit(names: list[str]) -> bool:
        return any(_player_name_matches(player, n) for n in names)

    if mode in ("scorer", "scorer_or_assist"):
        if hit(events.scorers):
            return True, False
        if mode == "scorer_or_assist" and hit(events.assisters):
            return True, False
    elif mode == "booked" and hit(events.booked):
        return True, False

    # No acertó: fallo si consta que participó (evento o minutos);
    # anulada si consta que no jugó; pendiente si no hay dato fiable.
    if hit(events.participants):
        return False, False
    if played is not None:
        return (False, False) if hit(played) else (None, True)
    return None, False


# --- Props de jugador con número ("X más de 1.5 tiros a puerta") ------
#
# Se resuelven con /fixtures/players de API-Football (estadísticas por
# jugador). Sujeto -> claves aplanadas de la API; el orden importa
# ("tiro a puerta" antes que "tiro", "gol" al final porque "goles" es
# muy genérico). Si el jugador no disputó minutos -> anulada (la casa
# devuelve); stat ausente -> 0.
_PLAYER_PROP_SUBJECTS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (
        re.compile(
            r"tiro[s]?\s+a\s+(?:puerta|porter[ií]a)|on\s+target|a\s+porter[ií]a",
            re.IGNORECASE,
        ),
        ("shots.on",),
    ),
    (re.compile(r"tiro|disparo|shot", re.IGNORECASE), ("shots.total",)),
    (re.compile(r"falta|foul", re.IGNORECASE), ("fouls.committed",)),
    (re.compile(r"tarjeta|card", re.IGNORECASE), ("cards.yellow", "cards.red")),
    (re.compile(r"asist", re.IGNORECASE), ("goals.assists",)),
    (re.compile(r"gol", re.IGNORECASE), ("goals.total",)),
)
# "2 o más tiros" = total >= 2 = over 1.5.
_AT_LEAST_PATTERN = re.compile(r"(\d+)\s*o\s+m[aá]s", re.IGNORECASE)
_LINE_TEXT_PATTERN = re.compile(
    r"(?:m[aá]s|menos|over|under)\s+de\s+(\d+(?:[.,]\d+)?)", re.IGNORECASE
)
# Relleno que no forma parte del nombre del jugador en un prop.
_PROP_NOISE = re.compile(
    r"\d+\s*o\s+m[aá]s|m[aá]s\s+de|menos\s+de|\bover\b|\bunder\b|"
    r"\d+(?:[.,]\d+)?|tiro[s]?|a\s+(?:puerta|porter[ií]a)|dispar\w*|"
    r"falta[s]?|tarjeta[s]?|gol(?:es)?|asistenc\w*|cometid\w*|recibid\w*|"
    r"total\s+de|que\s+(?:marca|anota|recibe)|jugador|\by\b|\be\b|\bo\b",
    re.IGNORECASE,
)


def _extract_prop_player_name(seleccion: str) -> Optional[str]:
    """Nombre del jugador en un prop con número.

    Dos formatos: "Ante Budimir más de 1.5 tiros" (nombre delante) y
    "2 o más tiros a portería - Ante Budimir" (nombre tras guion). Se
    limpia cada trozo separado por "-", ":" o "+" y se devuelve el más
    largo que quede.
    """
    candidates = []
    for chunk in re.split(r"[-:+]", seleccion):
        name = _clean_team_name(_PROP_NOISE.sub(" ", chunk))
        if name:
            candidates.append(name)
    return max(candidates, key=len) if candidates else None


def _detect_player_prop(
    seleccion: str,
    mercado: Optional[str],
    linea: Optional[float],
    evento: Optional[str],
) -> Optional[tuple[str, tuple[str, ...], str, float]]:
    """(jugador, stat_keys, direction, linea) si la selección es un
    prop de jugador con número; None si no lo es.

    Guarda anti-falso-positivo: si el "jugador" extraído son tokens del
    propio evento ("Real Madrid más de 1.5 tiros"), es una apuesta de
    equipo mal clasificada — no un prop.
    """
    text = f"{seleccion or ''} {mercado or ''}"
    stat_keys = next(
        (keys for pattern, keys in _PLAYER_PROP_SUBJECTS if pattern.search(text)),
        None,
    )
    if not stat_keys:
        return None

    # La dirección se mira primero en la selección: el mercado suele
    # ser "over/under ..." y contiene las dos palabras (ambiguo).
    direction = _detect_over_under_direction(
        seleccion or ""
    ) or _detect_over_under_direction(mercado or "")
    resolved_linea = linea
    at_least = _AT_LEAST_PATTERN.search(text)
    if at_least:
        direction = "over"
        if resolved_linea is None:
            resolved_linea = int(at_least.group(1)) - 0.5
    if resolved_linea is None:
        m = _LINE_TEXT_PATTERN.search(text)
        if m:
            resolved_linea = float(m.group(1).replace(",", "."))
    if direction is None or resolved_linea is None:
        return None

    player = _extract_prop_player_name(seleccion or "")
    if not player:
        return None
    if evento:
        player_tokens = set(re.findall(r"\w+", player.lower()))
        evento_tokens = set(re.findall(r"\w+", evento.lower()))
        if player_tokens and player_tokens <= evento_tokens:
            return None
    return (player, stat_keys, direction, resolved_linea)


def _resolve_player_prop(
    players: MatchPlayers,
    player: str,
    stat_keys: tuple[str, ...],
    direction: str,
    linea: float,
) -> tuple[Optional[bool], bool]:
    """Over/under sobre una estadística del jugador. Si no disputó
    minutos -> anulada (la casa devuelve). Stat ausente -> 0."""
    name = next((n for n in players.played if _player_name_matches(player, n)), None)
    if name is None:
        return None, True
    total = sum(players.stats.get(name, {}).get(k, 0) for k in stat_keys)
    return _compare_over_under(total, direction, linea)


# --- Resultado exacto ("2-1", "marcador exacto 0-0") -----------------
#
# La selección es el marcador, no un equipo. El orden importa: si el
# evento del pick va al revés que el del proveedor ("Betis - Levante"),
# el marcador predicho también va al revés.
_EXACT_SCORE_MARKET = re.compile(
    r"resultado\s+exacto|marcador\s+(?:exacto|correcto)|correct\s+score",
    re.IGNORECASE,
)
_BARE_SCORELINE = re.compile(r"^\s*\d+\s*[-:]\s*\d+\s*$")
_SCORELINE = re.compile(r"(\d+)\s*[-:]\s*(\d+)")


def _extract_exact_score(
    seleccion: str, mercado: Optional[str]
) -> Optional[tuple[int, int]]:
    """(goles_local, goles_visitante) predichos, o None si no es un
    pick de resultado exacto."""
    text = f"{seleccion or ''} {mercado or ''}"
    explicit = _EXACT_SCORE_MARKET.search(text)
    bare = _BARE_SCORELINE.match(seleccion or "")
    if not explicit and not bare:
        return None
    m = _SCORELINE.search(seleccion or "") or _SCORELINE.search(mercado or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


# --- Tenis: "gana 2-0" es resultado exacto en sets, no solo ganador --
#
# El extractor suele etiquetar estos picks como mercado "ganador" y la
# selección "Alcaraz gana 2-0" extrae jugador="Alcaraz": si se verifica
# solo el ganador, un 2-1 real se marcaría acierto cuando la apuesta
# perdió. Los proveedores devuelven sets ganados en home/away_score, así
# que se puede verificar el marcador exacto de sets.
_TENNIS_SETS_AFTER_VERB = re.compile(
    r"(?:gana\w*|vence\w*|se\s+impone)\s+(\d+)\s*[-:]\s*(\d+)", re.IGNORECASE
)


# Tenis: sujeto del mercado de totales/hándicap — "juegos" o "sets".
_TENNIS_SETS_SUBJECT = re.compile(r"\bsets?\b", re.IGNORECASE)
_TENNIS_GAMES_SUBJECT = re.compile(r"juego\w*|\bgames?\b", re.IGNORECASE)
# Ruido a quitar de la selección para quedarnos con el jugador.
_TENNIS_PLAYER_NOISE = re.compile(
    r"h[aá]ndicap\w*|asi[aá]tic\w*|juego\w*|\bgames?\b|\bsets?\b"
    r"|primer\w*|segund\w*|tercer\w*|cuart\w*|quint\w*"
    r"|\d+(?:er|º|°|do|rd|th)\b|\bel\b|\bla\b|\bdel\b"
    r"|m[aá]s|menos|over|under|gana\w*",
    re.IGNORECASE,
)

# Partidos que el proveedor reporta como terminados sin jugar completo:
# el verificador los anula (las casas suelen devolver la apuesta; si la
# casa del usuario difiere, se corrige a mano).
_TENNIS_VOID_STATUSES = ("retired", "walkover")

# "gana el primer set" / "1er set: Alcaraz" / "set 2: Sinner".
_TENNIS_SET_POSITION = re.compile(
    r"\b(primer[ao]?|segund[ao]|tercer[ao]?|cuart[ao]|quint[ao])\s*set\b"
    r"|\b([1-5])(?:er|º|°|do|rd|th)?\s*set\b"
    r"|\bset\s*([1-5])\b",
    re.IGNORECASE,
)
_TENNIS_SET_WORDS = {
    "primer": 0,
    "primera": 0,
    "primero": 0,
    "segund": 1,
    "tercer": 2,
    "cuart": 3,
    "quint": 4,
}
# Verbo de victoria para distinguir "gana el 1er set" de un total.
_TENNIS_WIN_VERB = re.compile(
    r"\bgan(?:a|ar|ador|adora|e|ó)\b|\bvence\b", re.IGNORECASE
)
_TIEBREAK_PATTERN = re.compile(r"tie\s*-?\s*break|desempate", re.IGNORECASE)


def _tennis_set_index(text: str) -> Optional[int]:
    """Índice 0-based del set nombrado ("primer set" -> 0, "set 3" -> 2)
    o None si no hay posición de set en el texto."""
    m = _TENNIS_SET_POSITION.search(text)
    if not m:
        return None
    word, digit1, digit2 = m.group(1), m.group(2), m.group(3)
    if word:
        low = word.lower()
        for prefix, idx in _TENNIS_SET_WORDS.items():
            if low.startswith(prefix):
                return idx
        return None
    return int(digit1 or digit2) - 1


def _tennis_set_winner_index(match: MatchResult, set_idx: int) -> Optional[int]:
    """0 = home, 1 = away para el ganador del set N. None si el set no
    consta o quedó incompleto."""
    if not match.sets or len(match.sets) <= set_idx:
        return None
    home_games, away_games = match.sets[set_idx]
    if home_games == away_games:
        return None
    return 0 if home_games > away_games else 1


def _tennis_had_tiebreak(match: MatchResult) -> Optional[bool]:
    """True si algún set acabó 7-6. None sin desglose por sets."""
    if not match.sets:
        return None
    return any({h, a} == {6, 7} for h, a in match.sets)


def _player_games_in_set(
    match: MatchResult, player: str, set_idx: int
) -> Optional[tuple[int, int]]:
    """Juegos (jugador, rival) en un set concreto. None si el set no
    consta o el jugador no casa con ningún participante."""
    if not match.sets or len(match.sets) <= set_idx:
        return None
    home_games, away_games = match.sets[set_idx]
    side = _tennis_side(match, player)
    if side is None:
        return None
    if side == 0:
        return home_games, away_games
    return away_games, home_games


def _tennis_subject(text: str) -> Optional[str]:
    """ "sets" | "games" | None si el mercado no dice a cuál aplica."""
    if _TENNIS_SETS_SUBJECT.search(text):
        return "sets"
    if _TENNIS_GAMES_SUBJECT.search(text):
        return "games"
    return None


def _extract_tennis_player_name(seleccion: str) -> Optional[str]:
    """Nombre del jugador en una selección de tenis ("Alcaraz -1.5 sets"
    -> "Alcaraz"). Quita línea, palabras de mercado y verbos."""
    cleaned = _LINEA_PATTERN.sub("", seleccion or "")
    cleaned = _TENNIS_PLAYER_NOISE.sub(" ", cleaned)
    return _clean_team_name(cleaned) or None


def _tennis_side(match: MatchResult, name: str) -> Optional[int]:
    """0 = home, 1 = away para el jugador/pareja del pick. Matching
    consciente de dobles: una pareja solo casa si TODOS sus miembros
    aparecen en la pista, y una pista con pareja no casa con un
    nombre individual."""
    home_sim = _pair_similar(name, match.home_team)
    away_sim = _pair_similar(name, match.away_team)
    if max(home_sim, away_sim) < _MIN_TEAM_SIMILARITY:
        return None
    return 0 if home_sim >= away_sim else 1


def _tennis_scores(match: MatchResult, name: str) -> Optional[tuple[int, int]]:
    """Sets (jugador/pareja, rival) con matching consciente de dobles."""
    side = _tennis_side(match, name)
    if side is None:
        return None
    if side == 0:
        return match.home_score, match.away_score
    return match.away_score, match.home_score


def _player_games(match: MatchResult, player: str) -> Optional[tuple[int, int]]:
    """Juegos (jugador/pareja, rival) sumando el desglose por sets.
    None si el proveedor no lo trajo o no casa con ningún participante."""
    if not match.sets:
        return None
    home_games = sum(h for h, _ in match.sets)
    away_games = sum(a for _, a in match.sets)
    side = _tennis_side(match, player)
    if side is None:
        return None
    if side == 0:
        return home_games, away_games
    return away_games, home_games


def _handicap_result(player_score: int, opp_score: int, linea: float):
    """Hándicap sobre un marcador cualquiera (goles, sets, juegos):
    devuelve (acierto, anulada) partiendo líneas de cuarto en dos medias."""
    q = int(round(linea * 4))
    lines = (linea - 0.25, linea + 0.25) if q % 2 == 1 else (linea,)
    outcomes = [_handicap_outcome(player_score, opp_score, line) for line in lines]
    if all(o == "win" for o in outcomes):
        return True, False
    if all(o == "lose" for o in outcomes):
        return False, False
    if all(o == "push" for o in outcomes):
        return None, True
    return ("win" in outcomes), False


async def _verify_tennis_pick(
    pick: ParsedPick, providers: list[ResultsProvider]
) -> Optional[tuple[Optional[bool], bool]]:
    """Resuelve mercados de tenis: resultado exacto en sets ("gana 2-0"),
    over/under de juegos o sets, hándicap de juegos o sets, ganador de
    un set concreto y tiebreak sí/no.

    Devuelve None si el pick no entra en ninguno (cae al "ganador").
    Usa `MatchResult.sets` (juegos por set) para los mercados de juegos;
    si el proveedor no lo trajo, pendiente. Si el proveedor reporta
    retirada/walkover (`match.status`), el pick se anula sea cual sea
    el mercado — si la casa difiere, se corrige a mano.
    """
    seleccion = pick.seleccion or ""
    text = f"{seleccion} {pick.mercado or ''}"

    # Correct score en juegos ("gana 6-4 6-2"): va ANTES del marcador de
    # sets — si no, "6-4" se leería como un marcador de sets imposible.
    games_pred = _extract_tennis_games_prediction(seleccion, pick.mercado)
    if games_pred:
        team, pred_pairs, player_oriented = games_pred
        team_hint = pick.evento or team
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return None, True
        if not match.sets:
            return None, False
        player_side = _tennis_side(match, team)
        if player_side is None:
            return None, False
        # El marcador exacto exige mismo número de sets.
        if len(match.sets) != len(pred_pairs):
            return False, False
        reversed_order = bool(pick.evento) and match_reversed(
            pick.evento, match.home_team, match.away_team
        )
        player_first_in_event = (player_side == 0) != reversed_order
        for i, (a, b) in enumerate(pred_pairs):
            if player_oriented:
                pred_p, pred_o = a, b
            else:
                pred_p, pred_o = (a, b) if player_first_in_event else (b, a)
            home_g, away_g = match.sets[i]
            act_p, act_o = (home_g, away_g) if player_side == 0 else (away_g, home_g)
            if (act_p, act_o) != (pred_p, pred_o):
                return False, False
        return True, False

    sets_pred = _extract_tennis_sets_prediction(seleccion, pick.mercado)
    if sets_pred:
        team, s1, s2, player_oriented = sets_pred
        team_hint = pick.evento or team
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return None, True
        scores = _tennis_scores(match, team)
        if scores is None:
            return None, False
        player_sets, opp_sets = scores
        if player_sets <= opp_sets:
            return False, False  # el jugador predicho perdió
        if player_oriented:
            pred_ps, pred_os = s1, s2
        else:
            # Marcador en orden del evento: pasarlo a sets del jugador
            # según el lado que ocupa (y si el evento va al revés).
            player_is_home = _tennis_side(match, team) == 0
            reversed_order = bool(pick.evento) and match_reversed(
                pick.evento, match.home_team, match.away_team
            )
            if player_is_home != reversed_order:
                pred_ps, pred_os = s1, s2
            else:
                pred_ps, pred_os = s2, s1
        return (player_sets == pred_ps and opp_sets == pred_os), False

    # "Alcaraz gana el primer set" / mercado "1er set" + jugador: se
    # resuelve con los juegos del set concreto. Exige verbo de victoria
    # y ausencia de línea para no capturar totales ni hándicaps
    # ("más de 2.5 sets", "gana -1.5 sets").
    set_idx = _tennis_set_index(text)
    set_winner_market = _TENNIS_WIN_VERB.search(text) or _TENNIS_SET_POSITION.search(
        pick.mercado or ""
    )
    if set_idx is not None and pick.linea is None and set_winner_market:
        player = _extract_tennis_player_name(seleccion)
        team_hint = pick.evento or player
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return None, True
        winner_side = _tennis_set_winner_index(match, set_idx)
        if winner_side is None or not player:
            return None, False  # set no jugado o sin desglose
        player_side = _tennis_side(match, player)
        if player_side is None:
            return None, False
        return player_side == winner_side, False

    # "Habrá tiebreak" / "tiebreak en el partido: sí" — deducible de un
    # set 7-6 en el desglose.
    if _TIEBREAK_PATTERN.search(text):
        team_hint = pick.evento or _extract_tennis_player_name(seleccion)
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return None, True
        had_tiebreak = _tennis_had_tiebreak(match)
        if had_tiebreak is None:
            return None, False
        bet_no = bool(re.search(r"\bno\b|\bsin\b", seleccion, re.IGNORECASE))
        return had_tiebreak != bet_no, False

    # "X gana un set" / "gana al menos un set": el jugador/pareja gana
    # al menos un set del partido.
    if re.search(r"\bgana\w*\s+(?:al\s+menos\s+)?un\s+set\b", seleccion, re.I):
        player = _extract_tennis_player_name(
            re.sub(r"\bgana\w*.*$", "", seleccion, flags=re.I)
        )
        team_hint = pick.evento or player
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return None, True
        if not player:
            return None, False
        scores = _tennis_scores(match, player)
        if scores is None:
            return None, False
        return scores[0] >= 1, False

    # Línea escrita como "21+" o "20 o más" = over N-0.5.
    linea = pick.linea
    direction = _detect_over_under_direction(seleccion) or _detect_over_under_direction(
        pick.mercado or ""
    )
    if linea is None:
        plus = re.search(r"(\d+(?:[.,]\d+)?)\s*\+", seleccion)
        o_mas = re.search(r"(\d+(?:[.,]\d+)?)\s+o\s+m[aá]s", seleccion, re.I)
        for mm in (plus, o_mas):
            if mm:
                linea = float(mm.group(1).replace(",", ".")) - 0.5
                direction = direction or "over"
                break
    if linea is None:
        return None

    if direction:
        # Sin sujeto explícito la línea desambigua: los juegos totales
        # nunca bajan de ~12, las líneas de sets son <= 4.5.
        subject = _tennis_subject(text) or ("games" if linea >= 6 else "sets")
        player = _extract_team_total_team(seleccion)
        if player and player[0].isdigit():
            # "20 o más juegos" parte en "más" dejando "20 o" — no es un
            # jugador, es la línea.
            player = None
        team_hint = player or pick.evento
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return None, True
        # Over/under sobre un set concreto ("más de 9.5 juegos en el
        # 1er set"): se compara solo ese set, no el total del partido.
        if set_idx is not None:
            if not match.sets or len(match.sets) <= set_idx:
                return None, False
            if player:
                in_set = _player_games_in_set(match, player, set_idx)
                if in_set is None:
                    return None, False
                return _compare_over_under(in_set[0], direction, linea)
            set_total = sum(match.sets[set_idx])
            return _compare_over_under(set_total, direction, linea)
        if subject == "sets":
            if player:
                scores = _tennis_scores(match, player)
                if scores is None:
                    return None, False
                return _compare_over_under(scores[0], direction, linea)
            return _compare_over_under(
                match.home_score + match.away_score, direction, linea
            )
        if player:
            totals = _player_games(match, player)
            if totals is None:
                return None, False
            return _compare_over_under(totals[0], direction, linea)
        if not match.sets:
            return None, False
        total_games = sum(h + a for h, a in match.sets)
        return _compare_over_under(total_games, direction, linea)

    # Línea sin dirección over/under -> hándicap. Si el mercado pinta
    # over/under pero no se detecta el lado (p. ej. mercado
    # "over/under juegos" + selección "Alcaraz"), pendiente: resolverlo
    # como hándicap sería un resultado inventado.
    if _OVER_PATTERN.search(text) or _UNDER_PATTERN.search(text):
        return None, False
    # Sujeto del hándicap: explícito ("sets"/"juegos") o por magnitud
    # siguiendo la convención de las casas — |línea| <= 1.5 suele ser
    # sets, >= 3.5 juegos. El rango intermedio es ambiguo -> pendiente.
    player = _extract_tennis_player_name(seleccion)
    subject = _tennis_subject(text)
    if subject is None:
        magnitude = abs(linea)
        if magnitude <= 1.5:
            subject = "sets"
        elif magnitude >= 3.5:
            subject = "games"
    if not player or subject is None:
        return None, False
    match = await _find_match_across_providers(
        pick.fecha_evento, pick.evento or player, providers, pick.id
    )
    if not match:
        return None, False
    if match.status in _TENNIS_VOID_STATUSES:
        return None, True
    if set_idx is not None:
        # Hándicap de juegos dentro de un set ("-1.5 juegos 1er set").
        in_set = _player_games_in_set(match, player, set_idx)
        if in_set is None:
            return None, False
        return _handicap_result(in_set[0], in_set[1], linea)
    if subject == "sets":
        scores = _tennis_scores(match, player)
        if scores is None:
            return None, False
        return _handicap_result(scores[0], scores[1], linea)
    totals = _player_games(match, player)
    if totals is None:
        return None, False
    return _handicap_result(totals[0], totals[1], linea)


def _extract_tennis_games_prediction(
    seleccion: str, mercado: Optional[str]
) -> Optional[tuple[str, list[tuple[int, int]], bool]]:
    """(jugador, [(juegos_a, juegos_b) por set], player_oriented) para un
    correct score EN JUEGOS ("Alcaraz gana 6-4 6-2").

    Se distingue del marcador de sets por el número de pares: >=2 pares
    "N-M" son juegos por set; un solo par es el marcador de sets (lo
    maneja `_extract_tennis_sets_prediction`). Orientación igual que en
    sets: con verbo ("gana") el marcador va con el jugador; vía mercado
    "resultado exacto" describe el partido en orden del evento.
    """
    sel = seleccion or ""
    m = _TENNIS_SETS_AFTER_VERB.search(sel)
    if m:
        pairs = _SCORELINE.findall(sel[m.start() :])
        if len(pairs) >= 2:
            team = _clean_team_name(sel[: m.start()])
            if team:
                return team, [(int(a), int(b)) for a, b in pairs], True
    if mercado and _EXACT_SCORE_MARKET.search(mercado):
        src = mercado if len(_SCORELINE.findall(mercado)) >= 2 else sel
        pairs = _SCORELINE.findall(src)
        if len(pairs) >= 2:
            team = _clean_team_name(sel)
            if team:
                return team, [(int(a), int(b)) for a, b in pairs], False
    return None


def _extract_tennis_sets_prediction(
    seleccion: str, mercado: Optional[str]
) -> Optional[tuple[str, int, int, bool]]:
    """(jugador, s1, s2, player_oriented) si la selección lleva
    marcador de sets; None si es un "ganador" a secas.

    - "Sinner gana 2-1": el verbo ata el marcador al jugador nombrado
      (player_oriented=True → Sinner 2 sets, rival 1).
    - "Sinner" + mercado "resultado exacto 1-2": el marcador describe
      el partido en orden del evento (player_oriented=False → hay que
      ver en qué lado está el jugador).
    """
    m = _TENNIS_SETS_AFTER_VERB.search(seleccion or "")
    if m:
        team = _clean_team_name((seleccion or "")[: m.start()])
        if team:
            return team, int(m.group(1)), int(m.group(2)), True
    # "Alcaraz" + mercado "resultado exacto 2-0" / "correct score".
    if mercado and _EXACT_SCORE_MARKET.search(mercado):
        m = _SCORELINE.search(mercado)
        if m:
            team = _clean_team_name(seleccion or "")
            if team:
                return team, int(m.group(1)), int(m.group(2)), False
    return None


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


async def _find_events_across_providers(
    date: datetime,
    team_hint: str,
    providers: list[ResultsProvider],
    pick_id: Optional[int],
) -> Optional[MatchEvents]:
    """Eventos del partido (goles/tarjetas/cambios): solo los
    proveedores que implementan `find_match_events` (hoy API-Football)."""
    for provider in providers:
        finder = getattr(provider, "find_match_events", None)
        if finder is None:
            continue
        try:
            events = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando events para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if events:
            return events
    return None


async def _find_players_across_providers(
    date: datetime,
    team_hint: str,
    providers: list[ResultsProvider],
    pick_id: Optional[int],
):
    """Jugadores con minutos disputados: solo los proveedores que
    implementan `find_match_players` (hoy API-Football)."""
    for provider in providers:
        finder = getattr(provider, "find_match_players", None)
        if finder is None:
            continue
        try:
            players = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando players para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if players:
            return players
    return None


async def _find_postponed_across_providers(
    date: datetime,
    team_hint: str,
    providers: list[ResultsProvider],
    pick_id: Optional[int],
):
    """Partido aplazado/cancelado: solo los proveedores que implementan
    `find_postponed_match` (football-data y API-Football). Reusa las
    listas de fixtures ya cacheadas — no cuesta llamadas extra."""
    for provider in providers:
        finder = getattr(provider, "find_postponed_match", None)
        if finder is None:
            continue
        try:
            state = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando aplazados para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if state:
            return state
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

    if any(
        m in mercado_low
        for m in ("combinada", "combinado", "acumulador", "múltiple", "multiple")
    ):
        # Una combinada no se resuelve con un solo marcador: hay que
        # verificar cada selección por separado. Sin eso, la rama de
        # "ganador" solo comprobaría la primera y daría un falso
        # resultado. Manual hasta modelar las selecciones sueltas.
        return None, False

    if sport == "tenis":
        result = await _verify_tennis_pick(pick, providers_for_sport)
        if result is not None:
            return result
        # Mercado no reconocido en tenis: cae al "ganador" de abajo.

    if pick.evento and pick.fecha_evento < utc_now() - _POSTPONED_VOID_AFTER:
        # Pasada la ventana de reprogramación de la casa (~72 h) y el
        # partido consta aplazado/cancelado: la apuesta se devuelve,
        # sea cual sea el mercado. Si se jugó, esto no encuentra nada
        # y la verificación sigue su curso normal.
        state = await _find_postponed_across_providers(
            pick.fecha_evento, pick.evento, providers_for_sport, pick.id
        )
        if state:
            logger.info(
                "[RESULTS_VERIFIER] Pick id=%s anulado: partido %s (%s vs %s).",
                pick.id,
                state.status,
                state.home_team,
                state.away_team,
            )
            return None, True

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

        # Resultado exacto ("2-1", "marcador exacto 0-0"): la selección
        # es el marcador; el evento da la orientación local/visitante.
        exact = _extract_exact_score(pick.seleccion or "", pick.mercado)
        if exact:
            if not pick.evento:
                return None, False
            match = await _find_match_across_providers(
                pick.fecha_evento, pick.evento, providers_for_sport, pick.id
            )
            if not match:
                return None, False
            pred_home, pred_away = exact
            if match_reversed(pick.evento, match.home_team, match.away_team):
                pred_home, pred_away = pred_away, pred_home
            return (
                match.home_score == pred_home and match.away_score == pred_away
            ), False

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
            if stat_keys:
                stats = await _find_stats_across_providers(
                    pick.fecha_evento, team_hint, providers_for_sport, pick.id
                )
                if stats:
                    result = _resolve_stat_over_under(
                        stats, stat_keys, team_total_team, direction, pick.linea
                    )
                    if result != (None, False):
                        return result
            # Sujeto sin stat de equipo, o el "equipo" no casó con
            # ninguno: quizá es un prop de jugador ("Budimir más de
            # 1.5 tiros a puerta").
            prop = _detect_player_prop(
                pick.seleccion or "", pick.mercado, pick.linea, pick.evento
            )
            if prop and pick.evento:
                player, prop_keys, prop_dir, prop_linea = prop
                players = await _find_players_across_providers(
                    pick.fecha_evento, pick.evento, providers_for_sport, pick.id
                )
                if players is not None:
                    return _resolve_player_prop(
                        players, player, prop_keys, prop_dir, prop_linea
                    )
            return None, False
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

    # Mercados de jugador ("Raphinha marca", "X marca o asiste", "X
    # recibe tarjeta"): se resuelven con los eventos del partido.
    player_market = _detect_player_market(pick.seleccion or "", pick.mercado)
    if player_market:
        mode, player = player_market
        if not pick.evento:
            return None, False
        events = await _find_events_across_providers(
            pick.fecha_evento, pick.evento, providers_for_sport, pick.id
        )
        if not events:
            return None, False
        result = _resolve_player_market(events, player, mode)
        if result != (None, False):
            return result
        # El jugador no consta en ningún evento: consultar quién
        # disputó minutos para distinguir fallo (jugó sin acertar) de
        # anulada (no jugó -> la casa devuelve). Solo se llama en este
        # caso ambiguo — si el endpoint no da datos, pendiente.
        players = await _find_players_across_providers(
            pick.fecha_evento, pick.evento, providers_for_sport, pick.id
        )
        if players is None:
            return None, False
        return _resolve_player_market(events, player, mode, players.played)

    # Props de jugador con número pero mercado sin over/under (p. ej.
    # mercado="tiros a portería", seleccion="2 o más - Ante Budimir").
    prop = _detect_player_prop(
        pick.seleccion or "", pick.mercado, pick.linea, pick.evento
    )
    if prop:
        if not pick.evento:
            return None, False
        player, prop_keys, prop_dir, prop_linea = prop
        players = await _find_players_across_providers(
            pick.fecha_evento, pick.evento, providers_for_sport, pick.id
        )
        if players is None:
            return None, False
        return _resolve_player_prop(players, player, prop_keys, prop_dir, prop_linea)

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
    if match.status in _TENNIS_VOID_STATUSES:
        # Retirada/walkover (tenis): la casa suele devolver la apuesta.
        return None, True

    winner = _resolve_winner(match)
    if winner is None:
        if es_mercado_sin_empate:
            # "Resultado sin empate" anula/devuelve la apuesta en caso
            # de empate en vez de perderla.
            return None, True
        return False, False  # empate: la apuesta a "gana" falla

    if sport == "tenis":
        # Matching por parejas: "Alcaraz / Munar" casa con el dobles,
        # pero "Alcaraz" solo no.
        return _pair_similar(predicted_team, winner) >= _MIN_TEAM_SIMILARITY, False
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
