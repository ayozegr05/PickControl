"""Verificación automática de picks pendientes contra resultados reales.

Estrategia (mercados soportados: ganador, hándicap asiático, over/under):
1. Busca ParsedPick con `es_apuesta=True`, `acierto` aún sin verificar y
   `fecha_evento` en el pasado (el partido ya se habrá jugado).
2. Según el `mercado`, resuelve de forma distinta:
   - "ganador": compara el equipo predicho contra el ganador real.
   - "hándicap asiático": aplica la línea (con signo) al marcador del
     equipo antes de comparar. Con líneas enteras puede haber "push"
     (empate técnico) → se marca como `anulada`, no como acierto/fallo.
   - "over/under": compara el total de goles/tantos contra la línea.
     También puede haber "push" con líneas enteras.
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
from app.services.results.base import MatchResult, ResultsProvider
from app.services.results.football_data import FootballDataProvider
from app.services.results.rapidapi_tennis import RapidApiTennisProvider
from app.services.results.tennisapi1 import TennisApi1Provider

logger = get_logger("app.results.verifier")

_WIN_KEYWORDS = ["gana", "ganará", "ganara", "ganador", "vence"]
# Mercados en los que la "selección" es solo el nombre del equipo (sin
# verbo "gana") pero el mercado en sí ya implica un resultado directo.
_NO_VERB_WIN_MARKETS = [
    "resultado sin empate",
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


def _detect_over_under_direction(text: str) -> Optional[str]:
    """Detecta si una selección/mercado de over-under es "over" o "under"."""
    low = text.lower()
    if "under" in low or "menos de" in low:
        return "under"
    if "over" in low or "más de" in low or "mas de" in low:
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


def _resolve_over_under(
    match: MatchResult, direction: str, linea: float
) -> tuple[Optional[bool], bool]:
    """Resuelve un pick de over/under sobre el total de goles del partido.

    Devuelve (acierto, anulada). Con líneas enteras puede haber "push".
    """
    total = match.home_score + match.away_score
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
        # Over/under es sobre el total del partido, no sobre un equipo
        # concreto, pero necesitamos un nombre de equipo para localizar
        # el partido en la API. Probamos con lo que haya en la
        # selección o, si no, con el evento.
        team_hint = _extract_handicap_team(pick.seleccion) or pick.evento
        if not team_hint:
            return None, False
        match = await _find_match_across_providers(
            pick.fecha_evento, team_hint, providers_for_sport, pick.id
        )
        if not match:
            return None, False
        return _resolve_over_under(match, direction, pick.linea)

    # Mercado "ganador" simple (o "resultado sin empate" con selección =
    # solo el nombre del equipo).
    predicted_team = _extract_predicted_team(pick.seleccion, pick.mercado)
    if not predicted_team:
        # Mercado no soportado todavía: manual.
        return None, False

    es_mercado_sin_empate = "resultado sin empate" in mercado_low

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
            .where(ParsedPick.fecha_evento >= now - _MAX_VERIFICATION_AGE)
        )
        pending = list(result.all())

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
