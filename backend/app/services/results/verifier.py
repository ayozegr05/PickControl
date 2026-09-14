"""Verificación automática de picks pendientes contra resultados reales.

Estrategia (MVP, solo mercado "ganador" en fútbol):
1. Busca ParsedPick con `es_apuesta=True`, `acierto` aún sin verificar y
   `fecha_evento` en el pasado (el partido ya se habrá jugado).
2. Solo intenta verificar picks cuya `seleccion` sea del tipo "Equipo X
   gana" (mercado de ganador), porque es el único que se puede resolver
   sin ambigüedad comparando el marcador final.
3. Consulta primero football-data.org (ligas top); si no encuentra el
   partido, intenta con API-Football (más cobertura, límite más bajo).
4. Si ningún proveedor encuentra el partido, el pick queda pendiente
   para revisión manual (no se inventa un resultado).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import select

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.services.results.api_football import ApiFootballProvider
from app.services.results.base import MatchResult, ResultsProvider
from app.services.results.football_data import FootballDataProvider

logger = get_logger("app.results.verifier")

_WIN_KEYWORDS = ["gana", "ganará", "ganara", "ganador", "vence"]
_MIN_TEAM_SIMILARITY = 0.6


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _extract_predicted_team(seleccion: str) -> Optional[str]:
    """Extrae el nombre del equipo/jugador de una selección tipo "X gana".

    Devuelve None si la selección no es un mercado de "ganador" simple
    (p. ej. hándicaps, over/under, correct score...), que de momento no
    se verifican automáticamente.
    """
    low = seleccion.lower()
    for keyword in _WIN_KEYWORDS:
        idx = low.find(keyword)
        if idx != -1:
            team = seleccion[:idx].strip(" -:¡!")
            return team or None
    return None


def _resolve_winner(match: MatchResult) -> Optional[str]:
    """Devuelve el nombre del equipo ganador, o None si hubo empate."""
    if match.home_score == match.away_score:
        return None
    return match.home_team if match.home_score > match.away_score else match.away_team


async def _get_providers() -> list[ResultsProvider]:
    settings = get_settings()
    providers: list[ResultsProvider] = []
    if settings.football_data_api_key:
        providers.append(FootballDataProvider(settings.football_data_api_key))
    if settings.api_football_key:
        providers.append(
            ApiFootballProvider(settings.api_football_key, settings.api_football_host)
        )
    return providers


async def verify_pick(
    pick: ParsedPick, providers: list[ResultsProvider]
) -> Optional[bool]:
    """Intenta verificar un único pick. None si no se puede resolver aún."""
    if not pick.fecha_evento or not pick.seleccion:
        return None

    predicted_team = _extract_predicted_team(pick.seleccion)
    if not predicted_team:
        # Mercado no soportado todavía (hándicap, over/under...): manual.
        return None

    for provider in providers:
        try:
            match = await provider.find_match(pick.fecha_evento, predicted_team)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[RESULTS_VERIFIER] Error consultando proveedor para pick id=%s: %s",
                pick.id,
                exc,
            )
            continue

        if not match:
            continue

        winner = _resolve_winner(match)
        if winner is None:
            return False  # empate: la apuesta a "gana" falla

        return _similar(predicted_team, winner) >= _MIN_TEAM_SIMILARITY

    return None


async def verify_pending_picks() -> int:
    """Revisa todos los picks pendientes de verificar y actualiza los que pueda.

    Devuelve cuántos picks se verificaron automáticamente en esta pasada.
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
            .where(ParsedPick.fecha_evento != None)  # noqa: E711
            .where(ParsedPick.fecha_evento < now)
        )
        pending = list(result.all())

        logger.info(
            "[RESULTS_VERIFIER] Picks pendientes de verificar (con fecha pasada): %s",
            len(pending),
        )

        for pick in pending:
            acierto = await verify_pick(pick, providers)
            if acierto is not None:
                pick.acierto = acierto
                pick.verificado_por = "auto"
                session.add(pick)
                verified_count += 1
                logger.info(
                    "[RESULTS_VERIFIER] Pick id=%s ('%s') verificado: acierto=%s",
                    pick.id,
                    pick.seleccion,
                    acierto,
                )

        await session.commit()

    logger.info(
        "[RESULTS_VERIFIER] Verificación completada: %s/%s picks resueltos.",
        verified_count,
        len(pending),
    )
    return verified_count
