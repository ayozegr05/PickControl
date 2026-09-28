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

import asyncio
import re
from contextvars import ContextVar
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.pick import Acierto, Pick
from app.services.notifications.push import notify_settled_picks
from app.services.results.allsports_tennis import AllSportsTennisProvider
from app.services.results.api_basketball import ApiBasketballProvider
from app.services.results.api_football import ApiFootballProvider
from app.services.results.api_tennis import ApiTennisProvider, _pair_similar
from app.services.results.base import (
    MatchEvents,
    MatchPlayers,
    MatchResult,
    MatchStats,
    ResultsProvider,
    fold_name,
    match_reversed,
)
from app.services.results.espn import (
    EspnBasketballProvider,
    EspnProvider,
    EspnTennisProvider,
    _translate_countries,
)
from app.services.results.espn_f1 import EspnF1Provider
from app.services.results.footapi_stats import FootApiStatsProvider
from app.services.results.football24h import Football24hProvider
from app.services.results.football_data import FootballDataProvider
from app.services.results.gemini_research import GeminiResearchProvider
from app.services.results.jolpica_f1 import JolpicaF1Provider
from app.services.results.rapidapi_tennis import RapidApiTennisProvider
from app.services.results.scores365 import Scores365Provider
from app.services.results.sofascore_basketball import SofascoreBasketballProvider
from app.services.results.sofascore_native import (
    SofaScoreNativeResultsProvider,
    direct_transport,
    rapidapi_transport,
    sofascore6_transport,
)
from app.services.results.tennisapi1 import TennisApi1Provider
from app.services.results.transfermarkt import TransfermarktProvider

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
    "automovilismo": "automovilismo",
    "automovilismo f1": "automovilismo",
    "formula 1": "automovilismo",
    "fórmula 1": "automovilismo",
    "formula1": "automovilismo",
    "fórmula1": "automovilismo",
    "formula uno": "automovilismo",
    "f1": "automovilismo",
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
_HANDICAP_KEYWORDS = re.compile(
    r"h[aá]nd(?:icap)?\.?\s*asi[aá]tico|goles?\s+de\s+ventaja",
    re.IGNORECASE,
)
_LINEA_PATTERN = re.compile(r"[+-]\s?\d+(?:[.,]\d+)?")

# Sujeto de una línea over/under que NO se puede resolver con el
# marcador final: córners, tarjetas, tiros, faltas, juegos, sets...
# Verificar "Menos de 11.0 córners" contra los goles del partido sería
# un falso resultado (casi siempre sale "acierto"). Se queda pendiente
# para revisión manual hasta tener un proveedor de estadísticas.
_OU_NON_GOALS_PATTERN = re.compile(
    r"c[oó]rner|esquina|tarjeta|card|booking|amarilla|roja|tiro|shot|"
    r"falta|foul|fuera\s+de\s+juego|offside|juego|set|punto|coche|saque|"
    r"remate",
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
    # F1: "menos de 19.5 coches" = pilotos que acaban la carrera
    # (clasificados). Ningún provider de fútbol devuelve la clave —
    # solo EspnF1Provider.
    (
        re.compile(r"coches?|cars?|clasificados", re.IGNORECASE),
        ("Classified Cars",),
    ),
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
# En baloncesto los puntos SÍ son el marcador (a diferencia de tenis,
# donde "juegos"/"puntos" no salen del resultado final). La guarda
# `_OU_NON_GOALS_PATTERN` atrapa "punto" por tenis; para basket hay que
# dejar pasar los over/under de puntos a la resolución por marcador,
# mientras rebotes/asistencias/triples siguen siendo irresolubles.
_BASKET_POINTS_WORD = re.compile(r"puntos?|points?|pts", re.IGNORECASE)

_DOUBLE_CHANCE_PATTERN = re.compile(
    r"doble\s+oportunidad|double\s*chance|doble\s+resultado", re.IGNORECASE
)
_BTTS_YES_PATTERN = re.compile(
    r"ambos\s+(?:equipos?\s+)?(?:marcan|marcar[áa]n|anotan|anotar[áa]n|anotar[aá])|"
    r"both\s+teams\s+to\s+score|\bbtts\b",
    re.IGNORECASE,
)
_BTTS_NO_PATTERN = re.compile(
    r"no\s+(?:anotan|anotar[áa]n|marcan|marcar[áa]n)\s+ambos|"
    r"ambos\s+equipos?\s+no\s+(?:marcan|marcar[áa]n|anotan|anotar[áa]n)|"
    r"al\s+menos\s+un\s+equipo\s+no\s+(?:marca|anota)",
    re.IGNORECASE,
)
_DRAW_NO_BET_PATTERN = re.compile(
    r"resultado\s+sin\s+empate|empate\s+no\s+v[aá]lido|draw\s+no\s+bet|"
    r"empate.{0,10}apuesta\s+no\s+v[aá]lida",
    re.IGNORECASE,
)
_DRAW_SEL_PATTERN = re.compile(
    # Selección = el empate en sí ("Empate", "X", "Tablas al descanso").
    # Excluye "Empate o/y Betis" (doble oportunidad) y "Empate no válido".
    r"^\s*(?:empate\b(?!\s+(?:o|y|e|no|ni)\b)|x\b|tablas\b)",
    re.IGNORECASE,
)
_HT_FT_PATTERN = re.compile(
    # "Gana la 1ª parte y el partido" (HT/FT): exige el mismo ganador
    # al descanso y al final.
    r"(?:gana|win)\b[^.]{0,30}\bpartido\b|\bpartido\b[^.]{0,30}\b(?:gana|win)\b",
    re.IGNORECASE,
)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, fold_name(a), fold_name(b)).ratio()


def _clean_team_name(raw: str) -> str:
    """Quita markdown, emojis y flechas típicas de Telegram del nombre."""
    cleaned = _MARKDOWN_NOISE.sub("", raw)
    cleaned = _NON_TEAM_CHARS.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip(" -:¡!.")


# Restos de una selección "Gana X" que en realidad son el nombre del
# mercado, no un equipo: "Ganará el encuentro" -> "el encuentro".
_GENERIC_WIN_SUBJECT = re.compile(
    r"(?:(?:el|la|los|las|su|tu|este|ese)\s+)?"
    r"(?:encuentro|partido|combate|pelea|evento|choque|juego)\s*",
    re.IGNORECASE,
)


def _is_market_name(team: str) -> bool:
    """El "equipo" extraído es el propio nombre del mercado ("Ganará el
    encuentro", "el encuentro"): la selección no dice quién gana."""
    low = team.lower().strip(" .")
    if _GENERIC_WIN_SUBJECT.fullmatch(low):
        return True
    return bool(re.match(rf"(?:{'|'.join(map(re.escape, _WIN_KEYWORDS))})\b", low))


def _extract_predicted_team(
    seleccion: str, mercado: Optional[str] = None
) -> Optional[str]:
    """Extrae el nombre del equipo/jugador de una selección de "ganador".

    Soporta tanto "Equipo gana" como "Gana Equipo". Si la selección es
    solo el nombre del equipo pero el `mercado` ya indica un resultado
    directo (p. ej. "resultado sin empate", "ganador"), también se
    acepta. Devuelve None para mercados que no se pueden resolver así
    (hándicap, over/under...), que se manejan con sus propios
    extractores, y cuando el texto extraído es solo el nombre del
    mercado ("Ganará el encuentro" en slips bet365).
    """
    low = seleccion.lower()

    # Patrón "Equipo gana" (la palabra clave aparece al final).
    for keyword in _WIN_KEYWORDS:
        idx = low.find(keyword)
        if idx > 0:
            team = _clean_team_name(seleccion[:idx])
            if team and not _is_market_name(team):
                return team

    # Patrón "Gana Equipo" (la palabra clave aparece al principio, con
    # borde de palabra: "gana" no debe comerse el "rá" de "ganará").
    for keyword in _WIN_KEYWORDS:
        kw = re.match(rf"{re.escape(keyword)}\b", low)
        if kw:
            team = _clean_team_name(seleccion[kw.end() :])
            if team and not _is_market_name(team):
                return team

    # Selección = solo el nombre del equipo, pero el mercado ya implica
    # "gana" (p. ej. "resultado sin empate").
    if mercado and any(m in mercado.lower() for m in _NO_VERB_WIN_MARKETS):
        team = _clean_team_name(seleccion)
        if team and not _is_market_name(team):
            return team

    return None


# Selección que solo nombra el mercado: "Ganará el encuentro".
_GENERIC_WIN_PHRASE = re.compile(
    r"\bgan\w*\s+(?:el|la|su|este|ese)\s+" r"(?:encuentro|partido|combate|pelea)\b",
    re.IGNORECASE,
)


def _winner_fallback_team(pick: "ParsedPick") -> Optional[str]:
    """Lado apostado cuando la selección solo trae el nombre del mercado
    ("Ganará el encuentro" en slips bet365): el extractor pone al lado
    apostado primero en `evento` ("A vs B" / "A - B"). None si no aplica
    o el evento no trae enfrentamiento."""
    sel = pick.seleccion or ""
    if (
        not _GENERIC_WIN_PHRASE.search(sel)
        and "ganador" not in (pick.mercado or "").lower()
    ):
        return None
    parts = re.split(
        r"\s+vs\.?\s+|\s+-\s+",
        (pick.evento or "").strip(),
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if len(parts) != 2:
        return None
    return _clean_team_name(parts[0]) or None


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
        return _void("push")
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
        return _void("push")
    # direction == "under"
    if total < linea:
        return True, False
    if total > linea:
        return False, False
    return _void("push")


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
    candidates = {team, _translate_countries(team)}
    home_similarity = max(_similar(t, stats.home_team) for t in candidates)
    away_similarity = max(_similar(t, stats.away_team) for t in candidates)
    if max(home_similarity, away_similarity) < _MIN_TEAM_SIMILARITY:
        return None
    return side_total(0) if home_similarity >= away_similarity else side_total(1)


def _ht_view(match: MatchResult) -> Optional[MatchResult]:
    """El mismo partido visto al DESCANSO: cambia el marcador final por
    el de la 1ª parte para reusar los resolutores de siempre
    (over/under, ganador, doble oportunidad, btts, marcador exacto)."""
    if match.ht_home_score is None or match.ht_away_score is None:
        return None
    return MatchResult(
        home_team=match.home_team,
        away_team=match.away_team,
        home_score=match.ht_home_score,
        away_score=match.ht_away_score,
        status=match.status,
    )


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
    """Goles (equipo, rival) si `team` se parece a alguno de los dos.

    La selección llega en español pero los nombres del resultado pueden
    venir en inglés (selecciones ESPN): se cruza también la traducción
    y gana la mejor similitud, igual que `EspnProvider._score_hint`.
    """
    translated = _translate_countries(team)
    candidates = {team, translated}
    home_similarity = max(_similar(t, match.home_team) for t in candidates)
    away_similarity = max(_similar(t, match.away_team) for t in candidates)
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
    # "Total de córners - Menos de 9.5": el trozo antes de "menos" es el
    # SUJETO del mercado, no un equipo — es un total del partido.
    if team and re.match(r"^(?:total|n[uú]mero|cantidad)\b", team, re.IGNORECASE):
        return None
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
    r"recibe\s+(?:una\s+)?tarjeta|recibir[aá]\s+(?:una\s+)?tarjeta|"
    r"amonestad|sancionad|"
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
    hint_tokens = set(re.findall(r"\w+", fold_name(hint)))
    name_tokens = set(re.findall(r"\w+", fold_name(name)))
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
        return (False, False) if hit(played) else _void("jugador_fuera")
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
            r"(?:tiro|remate)[s]?\s+a\s+(?:puerta|porter[ií]a)|on\s+target|"
            r"a\s+porter[ií]a",
            re.IGNORECASE,
        ),
        ("shots.on",),
    ),
    (re.compile(r"tiro|disparo|shot|remate", re.IGNORECASE), ("shots.total",)),
    (re.compile(r"falta|foul", re.IGNORECASE), ("fouls.committed",)),
    (re.compile(r"tarjeta|card", re.IGNORECASE), ("cards.yellow", "cards.red")),
    (re.compile(r"asist", re.IGNORECASE), ("goals.assists",)),
    (re.compile(r"gol", re.IGNORECASE), ("goals.total",)),
)
# "2 o más tiros" / "2+ remates" (notación de slip) = total >= 2 = over 1.5.
_AT_LEAST_PATTERN = re.compile(r"(\d+)\s*(?:\+|o\s+m[aá]s)", re.IGNORECASE)
_LINE_TEXT_PATTERN = re.compile(
    r"(?:m[aá]s|menos|over|under)\s+de\s+(\d+(?:[.,]\d+)?)", re.IGNORECASE
)
# Relleno que no forma parte del nombre del jugador en un prop.
_PROP_NOISE = re.compile(
    r"\d+\s*o\s+m[aá]s|m[aá]s\s+de|menos\s+de|\bover\b|\bunder\b|"
    r"\d+(?:[.,]\d+)?|tiro[s]?|remate[s]?|a\s+(?:puerta|porter[ií]a)|"
    r"dispar\w*|"
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
        # "2+"/"2 o más" fija la línea a N-0.5 — preferible a la del
        # pick, que puede haberse parseado mal (p. ej. "Romero - 2+").
        direction = "over"
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
        player_tokens = set(re.findall(r"\w+", fold_name(player)))
        evento_tokens = set(re.findall(r"\w+", fold_name(evento)))
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
        return _void("jugador_fuera")
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
# Tenis: mercados de estadística del partido (aces, dobles faltas) —
# no salen del marcador, viven en las tablas de stats de las webs.
_TENNIS_STAT_SUBJECTS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (re.compile(r"\baces?\b", re.IGNORECASE), ("Aces",)),
    (
        re.compile(r"dobles?\s*faltas?|double\s*fault", re.IGNORECASE),
        ("Double Faults",),
    ),
)


def _tennis_stat_keys(text: str) -> Optional[tuple[str, ...]]:
    """Claves de MatchStats para el sujeto-stat del mercado de tenis."""
    for pattern, keys in _TENNIS_STAT_SUBJECTS:
        if pattern.search(text):
            return keys
    return None


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
        return _void("push")
    return ("win" in outcomes), False


# Palabras de torneo en `evento`: "Tenis - Chall. Szczecin" es buen
# contexto para la UI pero un hint inútil para los proveedores — casan
# por nombre de jugador y "Tenis"/"Challenger" no son jugadores (la
# búsqueda miss era garantizada y además quemaba cuota de RapidAPI).
_TENNIS_TOURNAMENT_HINT = re.compile(
    r"\btenis\b|\bchall(?:enger)?\.?\b|\batp\b|\bwta\b|\bitf\b|"
    r"grand\s*slam|\btorneo\b|\bmasters\b",
    re.IGNORECASE,
)


def _tennis_lookup_hint(pick: ParsedPick, fallback: Optional[str]) -> Optional[str]:
    """Hint de búsqueda para proveedores de tenis.

    `evento` solo se usa si trae un enfrentamiento ("A vs B" o "A - B"
    sin palabra de torneo). Si solo trae el torneo, se prefiere el
    jugador extraído de `seleccion` (`fallback`)."""
    evento = (pick.evento or "").strip()
    if not evento:
        return fallback
    if re.search(r"\bvs\.?\b", evento, re.IGNORECASE):
        return evento
    if _TENNIS_TOURNAMENT_HINT.search(evento):
        return fallback or evento
    if " - " in evento:
        return evento
    return fallback or evento


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
        team_hint = _tennis_lookup_hint(pick, team)
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return _void("aplazado")
        if await _rearranged_fixture_void(pick, match, team_hint, providers):
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
        team_hint = _tennis_lookup_hint(pick, team)
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return _void("aplazado")
        if await _rearranged_fixture_void(pick, match, team_hint, providers):
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
        team_hint = _tennis_lookup_hint(pick, player)
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return _void("aplazado")
        if await _rearranged_fixture_void(pick, match, team_hint, providers):
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
        team_hint = _tennis_lookup_hint(pick, _extract_tennis_player_name(seleccion))
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return _void("aplazado")
        if await _rearranged_fixture_void(pick, match, team_hint, providers):
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
        team_hint = _tennis_lookup_hint(pick, player)
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return _void("aplazado")
        if await _rearranged_fixture_void(pick, match, team_hint, providers):
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
    # "25+" / "20 o más" siempre es over — también cuando la línea ya
    # viene precargada (mercado "over/under" solo no desambigua lado).
    plus = re.search(r"(\d+(?:[.,]\d+)?)\s*\+", seleccion)
    o_mas = re.search(r"(\d+(?:[.,]\d+)?)\s+o\s+m[aá]s", seleccion, re.I)
    if plus or o_mas:
        direction = direction or "over"
        if linea is None:
            for mm in (plus, o_mas):
                if mm:
                    linea = float(mm.group(1).replace(",", ".")) - 0.5
                    break
    # Notación bet365 "+7,5 juegos": el mercado es over N. Aplica tanto
    # si falta la línea (la línea es N, no N-0.5) como si falta la
    # dirección ("over/under juegos" no dice lado). Solo cuando el
    # mercado declara un total — un "+1,5" de hándicap no es over.
    m_low = (pick.mercado or "").lower()
    if (
        any(w in m_low for w in ("over", "under", "total", "juegos"))
        and "dicap" not in m_low
    ):
        plus_first = re.search(r"\+(\d+(?:[.,]\d+)?)", seleccion)
        if plus_first:
            if linea is None:
                linea = float(plus_first.group(1).replace(",", "."))
            direction = direction or "over"
    if linea is None:
        return None

    if direction:
        # Mercados de estadística del partido ("25+ aces", "dobles
        # faltas"): no salen del marcador — se consultan las tablas de
        # stats vía providers con `find_match_stats` (Gemini-research).
        stat_keys = _tennis_stat_keys(text)
        if stat_keys:
            team_hint = _tennis_lookup_hint(pick, None)
            if not team_hint:
                return None, False
            stats = await _find_stats_across_providers(
                pick.fecha_evento, team_hint, providers, pick.id
            )
            if not stats:
                return None, False
            return _resolve_stat_over_under(stats, stat_keys, None, direction, linea)
        # Sin sujeto explícito la línea desambigua: los juegos totales
        # nunca bajan de ~12, las líneas de sets son <= 4.5.
        subject = _tennis_subject(text) or ("games" if linea >= 6 else "sets")
        player = _extract_team_total_team(seleccion)
        if player and player[0].isdigit():
            # "20 o más juegos" parte en "más" dejando "20 o" — no es un
            # jugador, es la línea.
            player = None
        team_hint = _tennis_lookup_hint(pick, player)
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers, pick.id
        )
        if not match:
            return None, False
        if match.status in _TENNIS_VOID_STATUSES:
            return _void("aplazado")
        if await _rearranged_fixture_void(pick, match, team_hint, providers):
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
        pick.fecha_evento, _tennis_lookup_hint(pick, player), providers, pick.id
    )
    if not match:
        return None, False
    if match.status in _TENNIS_VOID_STATUSES:
        return None, True
    if await _rearranged_fixture_void(pick, match, player, providers):
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
    # ESPN primero: JSON gratis sin key ni cuota, con marcador FT/HT +
    # stats (córners, tarjetas, tiros, faltas) en la misma respuesta.
    # Descarga a football-data (10 req/min) y a los providers de cuota.
    providers.append(EspnProvider())
    # 365scores: webservice público sin key ni cuota documentada — una
    # llamada por página de día (paginada) trae TODOS los partidos.
    # Va justo tras ESPN: cubre ligas menores que ESPN no lista y
    # descarga a los providers de cuota que van después.
    providers.append(Scores365Provider("futbol"))
    # football24hours.com: listados estáticos de liga/día sin key ni
    # cuota — cubre la cola larga (reservas DK, AFC Cup, ligas menores)
    # que ningún provider de API toca. Free: va antes que los de cuota.
    providers.append(Football24hProvider())
    # transfermarkt.es: livescore por fecha (todas las competiciones,
    # marcador inline) + tabla de stats por match_id. Cloudflare ->
    # FlareSolverr. Mismo tier gratuito que f24h, más cobertura de
    # divisiones inferiores/femenino y stats redundantes en las grandes.
    providers.append(TransfermarktProvider())
    if settings.football_data_api_key:
        providers.append(FootballDataProvider(settings.football_data_api_key))
    if settings.api_football_key:
        providers.append(
            ApiFootballProvider(settings.api_football_key, settings.api_football_host)
        )
    # footapi7 (Sofascore vía RapidAPI): sin ventana de fechas ni plan que
    # la limite. Rescata marcadores de ligas menores y stats (córners,
    # tarjetas, tiros) que API-Football ya no puede consultar fuera de su
    # ventana ±1 día. Cuota diaria propia, separada de la de odds.
    if settings.rapidapi_tennis_key:
        providers.append(
            FootApiStatsProvider(
                settings.rapidapi_tennis_key, settings.rapidapi_footapi_host
            )
        )
    # Tenis va después de los de fútbol: sin `deporte` informado solo se
    # consulta si los de fútbol no encontraron el partido. Y dentro de
    # tenis, TheSportsDB (gratis) primero y RapidAPI (cuota limitada) de
    # último recurso para Challenger/ITF.
    # ESPN tenis primero del deporte: gratis, sin cuota, marcador por
    # sets en ATP/WTA (individuales y dobles).
    providers.append(EspnTennisProvider())
    # 365scores para tenis: cubre ATP/WTA/Challenger/ITF/dobles
    # paginando por día — absorbe el grueso de los misses de los
    # mirrors RapidAPI que van después.
    providers.append(Scores365Provider("tenis"))
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
        # Último recurso: allsportsapi2 (Sofascore). `events/previous`
        # paginado cubre el historial completo del jugador — rescata
        # Challenger/ITF/dobles de hace días que `events/near` de
        # tennisapi1 ya no alcanza. Comparte cuota con el snapshotter
        # de odds (misma suscripción).
        providers.append(
            AllSportsTennisProvider(
                settings.rapidapi_tennis_key, settings.rapidapi_allsports_host
            )
        )
    # Baloncesto: ESPN primero — gratis, sin cuota, cubre NBA/WNBA/NBL/
    # FIBA (sin ACB ni Euroliga: ESPN no las publica; esas siguen en
    # API-Basketball y los mirrors).
    providers.append(EspnBasketballProvider())
    # F1: mismo feed gratis de ESPN (`racing/f1`). Los picks de coches
    # son raros pero existen ("menos de X coches" = clasificados).
    providers.append(EspnF1Provider())
    # Segundo determinista F1: Jolpica/Ergast (clasificación oficial
    # FIA; también gratis y sin key).
    providers.append(JolpicaF1Provider())
    # 365scores para basket: mismo feed por día, sin cuota — antes de
    # API-Basketball (cuota propia) y los mirrors.
    providers.append(Scores365Provider("baloncesto"))
    # API-Basketball (api-sports, cuota propia de 100/día — no toca
    # footapi7): rescata las ligas europeas que ESPN no cubre.
    if settings.api_basketball_key:
        providers.append(
            ApiBasketballProvider(
                settings.api_basketball_key, settings.api_basketball_host
            )
        )
    # Fallback de baloncesto: allsportsapi2 (Sofascore). Sin ventana de
    # fechas — rescata partidos fuera del ±1 día del plan free de
    # API-Basketball. Comparte la cuota diaria de allsportsapi2 con
    # tenis/odds (misma suscripción), pero los picks de basket son tan
    # raros que el gasto real es despreciable.
    if settings.rapidapi_tennis_key:
        providers.append(
            SofascoreBasketballProvider(
                settings.rapidapi_tennis_key, settings.rapidapi_allsports_host
            )
        )
    # Espejos RapidAPI de Sofascore (mismos datos, cuota diaria propia
    # de 100/día cada uno): se gastan ANTES que el acceso directo —
    # preferible agotar cuota renovable a exponer la IP a Cloudflare.
    if getattr(settings, "rapidapi_sportapi7_enabled", False) and (
        settings.rapidapi_tennis_key
    ):
        for sport in ("futbol", "tenis", "baloncesto"):
            providers.append(
                SofaScoreNativeResultsProvider(
                    rapidapi_transport(
                        "sportapi7",
                        settings.rapidapi_sportapi7_host,
                        settings.rapidapi_tennis_key,
                    ),
                    sport,
                )
            )
    if getattr(settings, "rapidapi_sofascore6_enabled", False) and (
        settings.rapidapi_tennis_key
    ):
        for sport in ("futbol", "tenis", "baloncesto"):
            providers.append(
                SofaScoreNativeResultsProvider(
                    sofascore6_transport(
                        settings.rapidapi_sofascore6_host,
                        settings.rapidapi_tennis_key,
                    ),
                    sport,
                )
            )
    # Último recurso absoluto: API nativa de Sofascore vía curl_cffi.
    # Cuota ilimitada pero es API interna protegida por Cloudflare —
    # cuanto menos volumen reciba, menor riesgo de baneo de IP. Si un
    # día no resuelve algo, se reintenta al día siguiente, no se insiste.
    if getattr(settings, "sofascore_direct_enabled", True):
        for sport in ("futbol", "tenis", "baloncesto"):
            providers.append(SofaScoreNativeResultsProvider(direct_transport(), sport))
    # Investigador de último recurso (Gemini + url_context): SOLO
    # detecta fixtures cancelados/aplazados/walkover con cita de
    # fuente fiable — `find_match` devuelve None, así que su posición
    # en la cascada de resultados es inofensiva y solo trabaja en el
    # camino de aplazados/anuladas cuando todo lo determinista falló.
    if getattr(settings, "google_api_key", None):
        for sport in ("futbol", "tenis", "baloncesto"):
            providers.append(GeminiResearchProvider(sport, settings.google_api_key))
    return providers


# Concurrencia: hasta _PICK_CONCURRENCY picks se verifican en paralelo,
# pero cada proveedor solo atiende _PROVIDER_CONCURRENCY llamadas
# simultáneas — protege la cuota diaria frente a ráfagas (429) que
# tumbarían la suscripción hasta mañana.
_PICK_CONCURRENCY = 3
_PROVIDER_CONCURRENCY = 2
_provider_sems: dict[str, asyncio.Semaphore] = {}

# Provider que aportó el último dato dentro de un `verify_pick`:
# ContextVar porque los picks se verifican en tareas concurrentes —
# cada task lleva su propio valor. Lo escriben los `_find_*` al
# recibir un resultado y lo lee el wrapper de `verify_pick` para
# rellenar `pick.verificado_provider` (atribución "best effort": en
# mercados de stats gana el provider de estadísticas, que se consulta
# después del de marcador).
_LAST_PROVIDER_HIT: ContextVar[Optional[str]] = ContextVar(
    "last_provider_hit", default=None
)

# Motivo del último void dentro de un `verify_pick`: "push" (empate
# técnico en línea entera / "resultado sin empate" empatado),
# "aplazado" (cancelado/aplazado/walkover/fixture reordenado) o
# "jugador_fuera" (prop cuyo jugador no disputó minutos). Lo escribe
# `_void` en cada retorno de anulada y lo lee el wrapper de
# `verify_pick` para rellenar `pick.motivo_anulada`.
_LAST_VOID_REASON: ContextVar[Optional[str]] = ContextVar(
    "last_void_reason", default=None
)


def _void(reason: str) -> tuple[None, bool]:
    """Marca un retorno anulado con su motivo para `verify_pick`."""
    _LAST_VOID_REASON.set(reason)
    return None, True


def _provider_label(provider: ResultsProvider) -> str:
    return getattr(provider, "NAME", None) or type(provider).__name__


def _is_research_provider(provider: ResultsProvider) -> bool:
    """Provider de investigación web (Gemini): cada consulta le cuesta
    una búsqueda real de cuota muy limitada — va ÚLTIMO incluso dentro
    de la fase de aplazados, solo cuando los deterministas ya fallaron."""
    return getattr(provider, "NAME", "").startswith("gemini")


def _postponed_state_hint(pick: ParsedPick, sport: Optional[str]) -> str:
    """Pista para el chequeo de aplazados (en tenis el `evento` puede
    ser solo el torneo: se completa con el jugador de la selección)."""
    if sport == "tenis":
        return (
            _tennis_lookup_hint(pick, _extract_tennis_player_name(pick.seleccion or ""))
            or ""
        )
    return pick.evento or ""


def _provider_sem(provider: ResultsProvider) -> asyncio.Semaphore:
    """Semáforo por suscripción: `NAME` agrupa providers que comparten
    cuota (allsportsapi2 sirve tenis, basket y odds — misma llave)."""
    name = _provider_label(provider)
    if name not in _provider_sems:
        _provider_sems[name] = asyncio.Semaphore(_PROVIDER_CONCURRENCY)
    return _provider_sems[name]


_MIN_LOOKUP_HINT_LEN = 3


def _lookup_hint_usable(team_hint: str, pick_id: Optional[int]) -> bool:
    """False si la pista es vacía o de 1-2 letras ("b" de una extracción
    rota): ningún jugador/equipo real se busca así — pasarla a /search
    quema una llamada por provider para devolver 400 o ruido."""
    if len((team_hint or "").strip()) >= _MIN_LOOKUP_HINT_LEN:
        return True
    logger.debug(
        "[RESULTS_VERIFIER] Pick id=%s: hint %r demasiado corto; se omite",
        pick_id,
        team_hint,
    )
    return False


async def _find_match_across_providers(
    date: datetime,
    team_hint: str,
    providers: list[ResultsProvider],
    pick_id: Optional[int],
) -> Optional[MatchResult]:
    if not _lookup_hint_usable(team_hint, pick_id):
        return None
    for provider in providers:
        try:
            async with _provider_sem(provider):
                match = await provider.find_match(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando proveedor para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if match:
            _LAST_PROVIDER_HIT.set(_provider_label(provider))
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
    if not _lookup_hint_usable(team_hint, pick_id):
        return None
    for provider in providers:
        finder = getattr(provider, "find_match_stats", None)
        if finder is None:
            continue
        try:
            async with _provider_sem(provider):
                stats = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando stats para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if stats:
            _LAST_PROVIDER_HIT.set(_provider_label(provider))
            return stats
    return None


async def _find_stats_1h_across_providers(
    date: datetime,
    team_hint: str,
    providers: list[ResultsProvider],
    pick_id: Optional[int],
) -> Optional[MatchStats]:
    """Estadísticas de la PRIMERA parte (córners/tarjetas 1H): solo los
    proveedores que implementan `find_match_stats_1h` (hoy footapi7 con
    el periodo "1ST" de Sofascore)."""
    if not _lookup_hint_usable(team_hint, pick_id):
        return None
    for provider in providers:
        finder = getattr(provider, "find_match_stats_1h", None)
        if finder is None:
            continue
        try:
            async with _provider_sem(provider):
                stats = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando stats 1H para pick " "id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if stats:
            _LAST_PROVIDER_HIT.set(_provider_label(provider))
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
    if not _lookup_hint_usable(team_hint, pick_id):
        return None
    for provider in providers:
        finder = getattr(provider, "find_match_events", None)
        if finder is None:
            continue
        try:
            async with _provider_sem(provider):
                events = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando events para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if events:
            _LAST_PROVIDER_HIT.set(_provider_label(provider))
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
    if not _lookup_hint_usable(team_hint, pick_id):
        return None
    for provider in providers:
        finder = getattr(provider, "find_match_players", None)
        if finder is None:
            continue
        try:
            async with _provider_sem(provider):
                players = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando players para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if players:
            _LAST_PROVIDER_HIT.set(_provider_label(provider))
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
            async with _provider_sem(provider):
                state = await finder(date, team_hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando aplazados para pick id=%s: %s",
                pick_id,
                exc,
            )
            continue
        if state:
            _LAST_PROVIDER_HIT.set(_provider_label(provider))
            return state
    return None


async def _rearranged_fixture_void(
    pick: ParsedPick,
    match: MatchResult,
    hint: Optional[str],
    providers: list[ResultsProvider],
) -> bool:
    """El fixture apostado se canceló y el cuadro se rehízo -> anulada.

    Si el feed trae un Cancelled/Postponed de la misma pista (misma
    pareja/jugador, mismo día) cuyos participantes NO son los del
    partido encontrado, el cruce apostado no se disputó: el partido
    jugado corresponde a otro rival (caso Szczecin dobles, #1715).
    Solo tenis: una pareja con un cancelado y un jugado el mismo día
    es reordenación — una doble jornada real tendría ambos Ended.
    """
    if not hint:
        return False
    # La reordenación la delatan los feeds estructurados (filas
    # Cancelled); la investigación web se reserva para el último
    # recurso — preguntarla por cada partido de tenis resuelto
    # quemaría búsquedas en vano.
    deterministic = [p for p in providers if not _is_research_provider(p)]
    state = await _find_postponed_across_providers(
        pick.fecha_evento, hint, deterministic, pick.id
    )
    if state is None:
        return False
    thr = _MIN_TEAM_SIMILARITY
    same_fixture = (
        _pair_similar(state.home_team, match.home_team) >= thr
        and _pair_similar(state.away_team, match.away_team) >= thr
    ) or (
        _pair_similar(state.home_team, match.away_team) >= thr
        and _pair_similar(state.away_team, match.home_team) >= thr
    )
    if same_fixture:
        return False
    logger.info(
        "[RESULTS_VERIFIER] Pick id=%s anulado por fixture reordenado: "
        "apostado %s vs %s (%s), jugado %s vs %s.",
        pick.id,
        state.home_team,
        state.away_team,
        state.status,
        match.home_team,
        match.away_team,
    )
    _LAST_VOID_REASON.set("aplazado")
    return True


async def verify_pick(
    pick: ParsedPick, providers: list[ResultsProvider]
) -> tuple[Optional[bool], bool]:
    """Intenta verificar un único pick.

    Devuelve `(acierto, anulada)`. Si todavía no se puede resolver
    (partido no encontrado, mercado no soportado...), `(None, False)`.

    Cuando decide un resultado anota además `pick.verificado_provider`
    con el provider que aportó el dato decisivo (persiste quien
    commitea; las correcciones manuales no pasan por aquí y quedan en
    NULL).
    """
    token = _LAST_PROVIDER_HIT.set(None)
    token_reason = _LAST_VOID_REASON.set(None)
    try:
        acierto, anulada = await _verify_pick_result(pick, providers)
        if (
            acierto is None
            and not anulada
            and pick.evento
            and pick.fecha_evento is not None
            and pick.fecha_evento < utc_now() - _POSTPONED_VOID_AFTER
        ):
            # Nada resolvió el pick viejo: última pregunta posible —
            # ¿consta aplazado en la web? Solo la investigación (Gemini)
            # puede encontrarlo fuera de las APIs estructuradas; si la
            # carrera normal hubiera resuelto, esta llamada no existiría.
            sport = _normalize_sport(pick.deporte)
            research = [
                p
                for p in _providers_for_sport(pick.deporte, providers)
                if _is_research_provider(p)
            ]
            if research:
                state = await _find_postponed_across_providers(
                    pick.fecha_evento,
                    _postponed_state_hint(pick, sport),
                    research,
                    pick.id,
                )
                if state is not None:
                    acierto, anulada = _void("aplazado")
        if acierto is not None or anulada:
            pick.verificado_provider = _LAST_PROVIDER_HIT.get()
            pick.motivo_anulada = _LAST_VOID_REASON.get() if anulada else None
        return acierto, anulada
    finally:
        _LAST_PROVIDER_HIT.reset(token)
        _LAST_VOID_REASON.reset(token_reason)


async def _verify_pick_result(
    pick: ParsedPick, providers: list[ResultsProvider]
) -> tuple[Optional[bool], bool]:
    """Cuerpo real de `verify_pick` (ver su docstring)."""
    if pick.es_combinada:
        # El padre de una combinada nunca se verifica contra APIs: sus
        # patas se resuelven como picks normales y el padre se liquida
        # en conjunto con `settle_combinada`.
        return None, False

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
        # Mercado "combinada" sin patas modeladas (p. ej. picks antiguos
        # no migrados): no se puede resolver con un solo marcador —
        # queda para revisión manual o para el backfill.
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
        # En tenis `evento` puede ser solo el torneo ("CHALLENGER X
        # DOBLES") — mismo hint que el camino de resultados: el jugador
        # o pareja extraído de la selección.
        state_hint = _postponed_state_hint(pick, sport)
        # Solo deterministas aquí: el investigador web (Gemini) cuesta
        # una búsqueda real por pick viejo — se reserva para cuando
        # toda la resolución normal ya falló (ver verify_pick).
        deterministic = [p for p in providers_for_sport if not _is_research_provider(p)]
        state = await _find_postponed_across_providers(
            pick.fecha_evento, state_hint, deterministic, pick.id
        )
        if state:
            logger.info(
                "[RESULTS_VERIFIER] Pick id=%s anulado: partido %s (%s vs %s).",
                pick.id,
                state.status,
                state.home_team,
                state.away_team,
            )
            return _void("aplazado")

    # Mercados de fútbol que se resuelven solo con el marcador final.
    combined = f"{pick.mercado or ''} {pick.seleccion or ''}"
    is_first_half = bool(_FIRST_HALF_PATTERN.search(combined))
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
            if is_first_half:
                match = _ht_view(match)
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
            if is_first_half:
                match = _ht_view(match)
                if not match:
                    return None, False
            return _resolve_btts(match, yes=bool(btts_yes) and not btts_no)

        # "Empate" / "X" / "Tablas" (también "al descanso" con 1ª parte):
        # la selección no nombra equipo — el cruce viene de `evento`.
        if _DRAW_SEL_PATTERN.search(pick.seleccion or ""):
            if not pick.evento:
                return None, False
            match = await _find_match_across_providers(
                pick.fecha_evento, pick.evento, providers_for_sport, pick.id
            )
            if not match:
                return None, False
            if is_first_half:
                match = _ht_view(match)
                if not match:
                    return None, False
            return match.home_score == match.away_score, False

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
            if is_first_half:
                match = _ht_view(match)
                if not match:
                    return None, False
            pred_home, pred_away = exact
            if match_reversed(pick.evento, match.home_team, match.away_team):
                pred_home, pred_away = pred_away, pred_home
            return (
                match.home_score == pred_home and match.away_score == pred_away
            ), False

    if es_handicap:
        # La línea puede venir solo dentro de la selección ("Barcelona
        # -2 goles de ventaja"): el extractor OCR a veces no la separa
        # al campo `linea`. Se intenta recuperar del texto.
        linea_h = pick.linea
        if linea_h is None and pick.seleccion:
            m_line = _LINEA_PATTERN.search(pick.seleccion)
            if m_line:
                try:
                    linea_h = float(m_line.group(0).replace(" ", "").replace(",", "."))
                except ValueError:
                    linea_h = None
        if linea_h is None:
            return None, False
        team_hint = _extract_handicap_team(pick.seleccion)
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers_for_sport, pick.id
        )
        if not match:
            return None, False
        if is_first_half:
            match = _ht_view(match)
            if not match:
                return None, False
        return _resolve_asian_handicap(match, team_hint, linea_h)

    if es_over_under and pick.linea is not None:
        ou_text = f"{pick.seleccion or ''} {pick.mercado or ''}"
        direction = _detect_over_under_direction(
            pick.seleccion
        ) or _detect_over_under_direction(pick.mercado or "")
        if not direction and _AT_LEAST_PATTERN.search(ou_text):
            # Notación de slip "2+ faltas" = "2 o más" = over.
            direction = "over"
        if not direction:
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
        if sport == "baloncesto" and not _BASKET_POINTS_WORD.search(ou_text):
            # Rebotes, asistencias, triples... no salen del marcador y no
            # hay endpoint de stats de basket: pendiente. Sin esta guarda
            # se resolverían contra el total de puntos (falso resultado).
            return None, False
        non_goals_subjects = _OU_NON_GOALS_PATTERN.findall(ou_text)
        if non_goals_subjects and not (
            sport == "baloncesto"
            and all(
                _BASKET_POINTS_WORD.fullmatch(subject) for subject in non_goals_subjects
            )
        ):
            # Prop de jugador primero ("Ivan Romero - 2+ faltas"): si la
            # selección nombra un jugador hay que resolver contra sus
            # stats, no contra las del equipo — la guarda de tokens del
            # evento ya descarta nombres de equipo.
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
            # Córners/tarjetas/tiros de equipo: no resoluble con el
            # marcador, pero API-Football sí tiene /fixtures/statistics
            # (solo dentro de la ventana de fechas del plan gratis). En
            # 1ª parte se usan las stats del periodo "1ST" (footapi7).
            stat_keys = _stat_keys_for(ou_text)
            if stat_keys:
                stats = await (
                    _find_stats_1h_across_providers
                    if is_first_half
                    else _find_stats_across_providers
                )(pick.fecha_evento, team_hint, providers_for_sport, pick.id)
                if stats:
                    result = _resolve_stat_over_under(
                        stats, stat_keys, team_total_team, direction, pick.linea
                    )
                    if result != (None, False):
                        return result
            return None, False
        if (
            pick.linea >= _AMBIGUOUS_LINE_MIN
            and not _GOALS_PATTERN.search(ou_text)
            and not (sport == "baloncesto" and _BASKET_POINTS_WORD.search(ou_text))
        ):
            # Línea alta sin la palabra "gol" (o "puntos" en basket): en
            # fútbol casi seguro son córners, no goles. Antes quedaba
            # pendiente por precaución; ahora, si algún proveedor trae
            # las stats del partido, se resuelve contra "Corner Kicks" —
            # el dato decide, no una suposición. Sin stats sigue
            # pendiente (nunca se resuelve contra el marcador).
            if sport == "futbol":
                stats = await _find_stats_across_providers(
                    pick.fecha_evento, team_hint, providers_for_sport, pick.id
                )
                if stats:
                    result = _resolve_stat_over_under(
                        stats,
                        ("Corner Kicks",),
                        team_total_team,
                        direction,
                        pick.linea,
                    )
                    if result != (None, False):
                        return result
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers_for_sport, pick.id
        )
        if not match:
            return None, False
        if is_first_half:
            match = _ht_view(match)
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
        # Selección = solo nombre del mercado ("Ganará el encuentro"):
        # el lado apostado es el primero del evento.
        predicted_team = _winner_fallback_team(pick)
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
        return _void("aplazado")
    if sport == "tenis" and await _rearranged_fixture_void(
        pick, match, predicted_team, providers_for_sport
    ):
        return _void("aplazado")

    if is_first_half:
        if _HT_FT_PATTERN.search(combined):
            # "Gana la 1ª parte y el partido" (HT/FT): mismo ganador
            # en ambos marcadores.
            ht = _ht_view(match)
            if not ht:
                return None, False
            ht_winner = _resolve_winner(ht)
            ft_winner = _resolve_winner(match)
            if ht_winner is None or ft_winner is None:
                return False, False
            return (
                _similar(predicted_team, ht_winner) >= _MIN_TEAM_SIMILARITY
                and _similar(ht_winner, ft_winner) >= _MIN_TEAM_SIMILARITY
            ), False
        match = _ht_view(match)
        if not match:
            return None, False

    winner = _resolve_winner(match)
    if winner is None:
        if es_mercado_sin_empate:
            # "Resultado sin empate" anula/devuelve la apuesta en caso
            # de empate en vez de perderla.
            return _void("push")
        return False, False  # empate: la apuesta a "gana" falla

    if sport == "tenis":
        # Matching por parejas: "Alcaraz / Munar" casa con el dobles,
        # pero "Alcaraz" solo no.
        return _pair_similar(predicted_team, winner) >= _MIN_TEAM_SIMILARITY, False
    return _similar(predicted_team, winner) >= _MIN_TEAM_SIMILARITY, False


async def settle_combinada(session: AsyncSession, parent: ParsedPick) -> bool:
    """Liquida una combinada a partir del estado de sus patas.

    Reglas de la casa de apuestas:
    - Una pata fallada -> la combinada entera falla, aunque queden
      patas pendientes (ya está muerta).
    - Una pata anulada se EXCLUYE: la combinada sigue con las demás y
      la cuota se recalcula (no se anula todo).
    - Todas las patas anuladas -> combinada anulada.
    - Todas las activas en verde -> acierto.

    `cuota_efectiva` es la cuota real de cobro tras excluir anuladas:
    sin anuladas es la cuota total declarada; con anuladas solo se
    recalcula si TODAS las patas tienen cuota (los slips casi nunca la
    traen por pata) — si no, queda a None y la ganancia no cuenta en
    stats hasta corrección manual (no se inventa).

    Si las patas ya no permiten resolver (p. ej. corrección manual que
    reabre una pata), una combinada resuelta automáticamente vuelve a
    pendiente; un override MANUAL del padre se respeta siempre.

    Devuelve True si el padre quedó actualizado.
    """
    if parent.id is None or parent.verificado_por == "manual":
        return False

    legs = (
        await session.exec(
            select(ParsedPick)
            .where(ParsedPick.combinada_id == parent.id)
            .order_by(ParsedPick.orden)
        )
    ).all()
    if not legs:
        return False

    # Estado objetivo: (acierto, anulada, cuota_efectiva). cuota_efectiva
    # solo es relevante cuando acierto=True.
    target: tuple[Optional[bool], bool, Optional[float]] | None = None

    if any(leg.acierto is False for leg in legs if not leg.anulada):
        target = (False, False, parent.cuota_efectiva)
        detalle = "alguna pata perdida"
    else:
        pendientes = [leg for leg in legs if not leg.anulada and leg.acierto is None]
        activas = [leg for leg in legs if not leg.anulada]
        if pendientes:
            # No resoluble: vuelve a pendiente si estaba resuelta por
            # auto (una corrección manual reabrió una pata).
            target = (None, False, None)
            detalle = f"{len(pendientes)} pata(s) pendiente(s)"
        elif not activas:
            target = (None, True, None)
            detalle = "todas las patas anuladas"
        else:
            if len(activas) == len(legs):
                # Sin anuladas: la cuota efectiva es la total declarada.
                cuota_efectiva = parent.cuota
            elif all(leg.cuota is not None for leg in legs):
                # Recalculable: producto de las cuotas de las patas activas.
                cuota = 1.0
                for leg in activas:
                    cuota *= leg.cuota or 1.0
                cuota_efectiva = round(cuota, 2)
            else:
                # No recalculable (faltan cuotas por pata): no se inventa.
                cuota_efectiva = None
            target = (True, False, cuota_efectiva)
            detalle = (
                f"{len(activas)}/{len(legs)} patas verdes "
                f"({len(legs) - len(activas)} anulada(s))"
            )

    acierto, anulada, cuota_efectiva = target
    # Idempotente: solo se escribe si el estado cambia de verdad.
    resolved = acierto is not None or anulada
    if (
        parent.acierto == acierto
        and parent.anulada == anulada
        and parent.cuota_efectiva == cuota_efectiva
        and (parent.verificado_por == "auto") == resolved
    ):
        return False

    parent.acierto = acierto
    parent.anulada = anulada
    parent.cuota_efectiva = cuota_efectiva
    parent.verificado_por = "auto" if resolved else None
    parent.verificado_at = utc_now() if resolved else None
    session.add(parent)
    logger.info(
        "[RESULTS_VERIFIER] Combinada id=%s: acierto=%s anulada=%s "
        "cuota_efectiva=%s (%s).",
        parent.id,
        acierto,
        anulada,
        cuota_efectiva,
        detalle,
    )
    return True


async def _settle_combinadas(session: AsyncSession) -> list[int]:
    """Pasa de liquidación de combinadas tras verificar las patas.

    Devuelve los ids de las combinadas que cambiaron de estado en esta
    pasada (para el hook de notificaciones). Se procesan también las
    resueltas por "auto": una corrección manual en una pata puede
    reabrir la combinada.
    """
    parents = (
        await session.exec(
            select(ParsedPick)
            .where(ParsedPick.es_combinada == True)  # noqa: E712
            .where(
                (ParsedPick.verificado_por == None)  # noqa: E711
                | (ParsedPick.verificado_por == "auto")
            )
        )
    ).all()
    settled: list[int] = []
    for parent in parents:
        if await settle_combinada(session, parent):
            settled.append(parent.id)
    return settled


async def cascade_user_settlements(session: AsyncSession) -> int:
    """Propaga resultados a apuestas de usuario enlazadas ("Yo también la
    jugué"): toda `picks` pendiente cuyo `parsed_pick_id` ya esté resuelto
    hereda acierto/fallo.

    Solo toca picks en Pending — nunca pisa una corrección manual del
    usuario. Si el pick del canal está anulado (`acierto=None`) no hay
    equivalente en el enum del usuario: se queda pendiente.
    """
    pendientes = (
        await session.exec(
            select(Pick)
            .where(Pick.parsed_pick_id != None)  # noqa: E711
            .where(Pick.acierto == Acierto.PENDING)
        )
    ).all()
    count = 0
    for apuesta in pendientes:
        resuelto = await session.get(ParsedPick, apuesta.parsed_pick_id)
        if resuelto is None or resuelto.acierto is None:
            continue
        apuesta.acierto = Acierto.TRUE if resuelto.acierto else Acierto.FALSE
        apuesta.updated_at = utc_now()
        session.add(apuesta)
        count += 1
    return count


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
    # Ids liquidados en esta pasada (simples + padres de combinadas) —
    # se notifican por push tras el commit. Un pick ya liquidado no
    # vuelve a entrar en `pending`, así que no hay notificaciones
    # duplicadas en pasadas repetidas.
    settled_ids: list[int] = []

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ParsedPick)
            .where(ParsedPick.es_apuesta == True)  # noqa: E712
            .where(ParsedPick.acierto == None)  # noqa: E711
            .where(ParsedPick.anulada == False)  # noqa: E712
            # Los padres de combinadas no se consultan a APIs: se
            # liquidan en `_settle_combinadas` a partir de sus patas.
            .where(ParsedPick.es_combinada == False)  # noqa: E712
            .where(ParsedPick.fecha_evento != None)  # noqa: E711
            .where(ParsedPick.fecha_evento < now)
        )
        # El filtro de edad se aplica en Python: además de la ventana de
        # 14 días hay un periodo de gracia para picks recién creados
        # (recuperados tarde por el catch-up), difícil de expresar en SQL
        # sin OR de columnas. El conjunto pendiente es pequeño.
        #
        # Excepción: las patas de combinadas pendientes se intentan
        # siempre, aunque hayan salido de la ventana — sin ellas el padre
        # no puede liquidar nunca, y los providers de estadísticas no
        # tienen límite de antigüedad.
        pending_parent_ids = set(
            (
                await session.exec(
                    select(ParsedPick.id)
                    .where(ParsedPick.es_combinada == True)  # noqa: E712
                    .where(ParsedPick.acierto == None)  # noqa: E711
                    .where(ParsedPick.anulada == False)  # noqa: E712
                )
            ).all()
        )
        pending = [
            p
            for p in result.all()
            if _should_attempt_verification(p, now)
            or p.combinada_id in pending_parent_ids
        ]
        # Recientes primero: los mercados de estadísticas (córners,
        # tarjetas...) solo se pueden consultar en la ventana ±1 día de
        # API-Football gratis — si la cuota se agota a mitad de pasada,
        # que se queden fuera los picks viejos, no los que caducan.
        pending.sort(key=lambda p: p.fecha_evento, reverse=True)

        logger.info(
            "[RESULTS_VERIFIER] Picks pendientes de verificar (con fecha pasada): %s",
            len(pending),
        )

        # Verificación concurrente: hasta _PICK_CONCURRENCY picks en
        # paralelo (cada proveedor capado por _provider_sem). Las
        # escrituras a la sesión se aplican después, en orden — la
        # sesión async no es segura entre tareas.
        pick_sem = asyncio.Semaphore(_PICK_CONCURRENCY)

        async def _verify_one(p: ParsedPick):
            async with pick_sem:
                try:
                    return p, await verify_pick(p, providers)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[RESULTS_VERIFIER] Error verificando pick id=%s: %s",
                        p.id,
                        exc,
                    )
                    return p, (None, False)

        outcomes = await asyncio.gather(*(_verify_one(p) for p in pending))
        for pick, (acierto, anulada) in outcomes:
            if acierto is not None or anulada:
                pick.acierto = acierto
                pick.anulada = anulada
                pick.verificado_por = "auto"
                pick.verificado_at = utc_now()
                session.add(pick)
                verified_count += 1
                settled_ids.append(pick.id)
                logger.info(
                    "[RESULTS_VERIFIER] Pick id=%s ('%s') verificado: "
                    "acierto=%s anulada=%s",
                    pick.id,
                    pick.seleccion,
                    acierto,
                    anulada,
                )

        # Liquidación de combinadas: con las patas ya verificadas en
        # esta pasada (y las anteriores), cada padre se resuelve en
        # conjunto — una pata perdida la tumba, las anuladas se excluyen.
        combinadas_settled = await _settle_combinadas(session)
        if combinadas_settled:
            settled_ids.extend(combinadas_settled)
            logger.info(
                "[RESULTS_VERIFIER] Combinadas liquidadas en esta pasada: %s",
                len(combinadas_settled),
            )

        cascaded = await cascade_user_settlements(session)
        if cascaded:
            logger.info(
                "[RESULTS_VERIFIER] Apuestas de usuario actualizadas por "
                "cascada: %s",
                cascaded,
            )

        await session.commit()

    # Push "apuesta liquidada" — solo llega a usuarios que marcaron
    # "Yo también la jugué" (el filtro está dentro de la función). Va
    # tras el commit y es best-effort: nunca rompe la verificación.
    if settled_ids:
        await notify_settled_picks(settled_ids)

    logger.info(
        "[RESULTS_VERIFIER] Verificación completada: %s/%s picks resueltos.",
        verified_count,
        len(pending),
    )
    return verified_count
