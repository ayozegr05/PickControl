"""Job periódico de snapshots de cuotas de mercado.

Corre cada `odds_snapshot_interval_minutes` en segundo plano (ver
`lifecycle.py`). Por ciclo:

1. Resuelve `parsed_picks.odds_event_id` para picks de tenis/fútbol
   con evento en ventana (futuros o terminados hace <48 h — el
   cierre sigue disponible post-partido). El id se guarda una vez:
   las capturas siguientes solo cuestan la llamada de odds.
2. Agrupa los picks por evento (tres canales con el mismo partido =
   UNA llamada) y decide si toca capturar:
   - Ningún snapshot -> primera captura (≈ cuota al publicar: el
     listener importa el pick casi en tiempo real).
   - Empieza en <3 h y no hay captura dentro de esa ventana ->
     captura de cierre.
   - Evento ya empezado/acabado sin captura posterior al inicio ->
     recuperación post-partido (la API sigue devolviendo apertura +
     cierre). La propia captura escrita desactiva el reintento.
3. Inserta TODAS las opciones de todos los mercados en
   `odds_snapshots` (append-only): el mapeo pick->mercado se hace al
   comparar, así mapeos futuros no necesitan nuevas llamadas.

Cadena de proveedores por deporte (misma semántica que el
verificador: un 403/429 lo marca sin cuota y se pasa al siguiente):

- futbol:     espn (pickcenter/DraftKings, gratis) -> allsportsapi2 ->
              espejos RapidAPI -> Sofascore directo
- baloncesto: espn (NBA/WNBA/NBL/FIBA; sin ACB ni Euroliga)
- tenis:      allsportsapi2 (dedicada) -> tennisapi1 (scavenger, mismo
              espacio de ids Sofascore)

Los espacios de ids no se mezclan: cada provider declara su
`ID_PREFIX` ("espn:" / "sofascore:") y el ciclo solo le pasa a capturar
ids de su propia familia — un `espn:*` nunca llega a Sofascore.

Así las suscripciones compartidas solo se gastan cuando la dedicada
se agota — los resultados siempre tienen prioridad en su cuota.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds.base import EventRef, OddsProvider
from app.services.odds.espn_odds import EspnOddsProvider
from app.services.odds.sofascore import SofaScoreOddsProvider
from app.services.results.base import is_missed, mark_missed
from app.services.results.sofascore_native import (
    SofaScoreNativeOddsProvider,
    direct_transport,
    rapidapi_transport,
    sofascore6_transport,
)
from app.services.results.verifier import (
    _SPORT_ALIASES,
    _extract_predicted_team,
    _tennis_lookup_hint,
)

logger = get_logger("app.odds.snapshotter")

# Ventana hacia atrás para recuperar el cierre de eventos acabados
# (la API sigue devolviendo apertura+cierre post-partido).
_PAST_WINDOW = timedelta(hours=48)
# Ventana hacia delante: picks publicados con más antelación son
# raros y resolverlos quema búsquedas que casi nunca están en el
# feed del proveedor.
_FUTURE_WINDOW = timedelta(days=14)
# Captura de cierre: dentro de las ~3 h previas al inicio.
_CLOSING_WINDOW = timedelta(hours=3)
# TTL de "sin mercados" para no re-pedir odds de un evento que el
# proveedor no cuote (partidos menores sin mercado en bet365).
_NO_ODDS_TTL = timedelta(hours=6)


async def _get_odds_providers() -> list[OddsProvider]:
    """Cadena de proveedores de odds en orden de preferencia.

    allsportsapi2 va primero (cuota dedicada); tennisapi1 solo de
    scavenger para tenis — comparte espacio de ids Sofascore, así que
    un `odds_event_id` resuelto por cualquiera sirve al otro.
    """
    settings = get_settings()
    providers: list[OddsProvider] = []
    # ESPN primero para fútbol/basket: pickcenter (DraftKings) gratis,
    # sin key ni cuota. Solo trae ganador/hándicap/total — los mercados
    # ricos (córners, BTTS, props) siguen necesitando Sofascore abajo.
    providers.append(EspnOddsProvider())
    # Sofascore directo primero: cuota ilimitada (API nativa vía
    # curl_cffi) — absorbe el grueso de resoluciones y capturas; las
    # suscripciones RapidAPI quedan como respaldo si Cloudflare cierra.
    # Espejos RapidAPI primero: cuota renovable antes que exponer la
    # IP a Cloudflare. El directo queda de último recurso — absorbe el
    # desbordamiento cuando las cuotas diarias se secan.
    if settings.rapidapi_tennis_key:
        providers.append(
            SofaScoreOddsProvider(
                name="allsportsapi2",
                api_key=settings.rapidapi_tennis_key,
                api_host=settings.rapidapi_allsports_host,
                sports=frozenset({"tenis", "futbol"}),
            )
        )
        providers.append(
            SofaScoreOddsProvider(
                name="tennisapi1",
                api_key=settings.rapidapi_tennis_key,
                api_host=settings.rapidapi_tennisapi1_host,
                sports=frozenset({"tenis"}),
            )
        )
        # Espejo sportapi7 (rutas nativas, cuota diaria propia) — solo
        # si el usuario lo suscribió y activó el flag.
        if getattr(settings, "rapidapi_sportapi7_enabled", False):
            providers.append(
                SofaScoreNativeOddsProvider(
                    rapidapi_transport(
                        "sportapi7",
                        settings.rapidapi_sportapi7_host,
                        settings.rapidapi_tennis_key,
                    ),
                    sports=frozenset({"tenis", "futbol"}),
                )
            )
        # Espejo sofascore6 (rutas propias) — otro respaldo de cuota.
        if getattr(settings, "rapidapi_sofascore6_enabled", False):
            providers.append(
                SofaScoreNativeOddsProvider(
                    sofascore6_transport(
                        settings.rapidapi_sofascore6_host,
                        settings.rapidapi_tennis_key,
                    ),
                    sports=frozenset({"tenis", "futbol"}),
                )
            )
    # Último recurso: API nativa (ilimitada pero protegida por
    # Cloudflare). Solo ve el tráfico que desborda las cuotas.
    if getattr(settings, "sofascore_direct_enabled", True):
        providers.append(
            SofaScoreNativeOddsProvider(
                direct_transport(), sports=frozenset({"tenis", "futbol"})
            )
        )
    return providers


def _canonical_sport(deporte: Optional[str]) -> Optional[str]:
    if not deporte:
        return None
    return _SPORT_ALIASES.get(deporte.strip().lower())


def _lookup_hint(pick: ParsedPick, sport: str) -> Optional[str]:
    """Pista de búsqueda del evento para el proveedor de odds.

    Con `evento` tipo "A vs B" / "A - B" es la mejor pista (casa con
    los dos equipos). En tenis, un `evento` que solo trae el torneo
    ("Tenis - Chall. X") cede al jugador de `seleccion` — la misma
    lógica que `_tennis_lookup_hint` del verificador.
    """
    fallback = (
        _extract_predicted_team(pick.seleccion or "", pick.mercado)
        or (pick.seleccion or "").strip()
    )
    if sport == "tenis":
        return _tennis_lookup_hint(pick, fallback)
    evento = (pick.evento or "").strip()
    return evento or fallback or None


async def _find_event_across_providers(
    sport: str, date: datetime, hint: str, providers: list[OddsProvider]
) -> Optional[EventRef]:
    for provider in providers:
        if sport not in getattr(provider, "SUPPORTED_SPORTS", frozenset()):
            continue
        try:
            ref = await provider.find_event(sport, date, hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ODDS] Error resolviendo evento en %s (%s): %s",
                getattr(provider, "NAME", "?"),
                hint,
                exc,
            )
            continue
        if ref is not None:
            return ref
    return None


async def _fetch_odds_across_providers(
    sport: str, event_ext_id: str, providers: list[OddsProvider]
):
    for provider in providers:
        if sport not in getattr(provider, "SUPPORTED_SPORTS", frozenset()):
            continue
        # Espacios de ids por familia: un id "espn:*" no es válido en
        # Sofascore ni viceversa — saltar sin llamar (ni siquiera se
        # intenta; un split(":",1) crudo extraería basura).
        prefix = getattr(provider, "ID_PREFIX", None)
        if prefix and not event_ext_id.startswith(prefix):
            continue
        try:
            choices = await provider.fetch_odds(event_ext_id, sport)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ODDS] Error pidiendo odds en %s (%s): %s",
                getattr(provider, "NAME", "?"),
                event_ext_id,
                exc,
            )
            continue
        if choices is not None:
            return provider, choices
    return None, None


def _needs_snapshot(
    last_capture: Optional[datetime], event_start: datetime, now: datetime
) -> bool:
    """Reglas de captura por evento (ver docstring del módulo)."""
    if last_capture is None:
        return True
    if now >= event_start - _CLOSING_WINDOW and last_capture < event_start - (
        _CLOSING_WINDOW
    ):
        return True
    # Post-partido: una sola recuperación; cuando existe una captura
    # posterior al inicio la condición deja de cumplirse sola.
    return event_start < now and last_capture < event_start


async def _resolve_missing_event_ids(
    session: AsyncSession,
    picks: list[ParsedPick],
    providers: list[OddsProvider],
) -> int:
    """Rellena `odds_event_id` de los picks pendientes y registra el
    evento en `odds_events`.

    Un pick ya enlazado cuyo evento no esté registrado (enlaces de
    antes de existir la tabla) se re-resuelve con la misma búsqueda:
    cuesta una vez y luego el pick deja de entrar en esta consulta.
    """
    registered = set(
        (await session.execute(select(OddsEvent.event_ext_id))).scalars().all()
    )
    resolved = 0
    for pick in picks:
        if pick.odds_event_id and pick.odds_event_id in registered:
            continue
        sport = _canonical_sport(pick.deporte)
        if sport not in ("tenis", "futbol", "baloncesto"):
            continue
        hint = _lookup_hint(pick, sport)
        if not hint:
            continue
        ref = await _find_event_across_providers(
            sport, pick.fecha_evento, hint, providers
        )
        if ref is None:
            continue
        pick.odds_event_id = ref.event_ext_id
        session.add(pick)
        # Registro del evento (home/away hacen falta para casar la
        # selección del pick con la opción del mercado al comparar).
        if ref.event_ext_id not in registered:
            session.add(
                OddsEvent(
                    event_ext_id=ref.event_ext_id,
                    sport=sport,
                    home_team=ref.home_team,
                    away_team=ref.away_team,
                    start=ref.start,
                )
            )
            registered.add(ref.event_ext_id)
        resolved += 1
        logger.info(
            "[ODDS] pick id=%s -> evento %s (%s vs %s)",
            pick.id,
            ref.event_ext_id,
            ref.home_team,
            ref.away_team,
        )
    return resolved


async def run_odds_snapshot_cycle() -> dict[str, int]:
    """Un ciclo del snapshotter. Devuelve contadores para logs/tests."""
    providers = await _get_odds_providers()
    stats = {"candidatos": 0, "resueltos": 0, "capturas": 0, "filas": 0}
    if not providers:
        return stats

    now = utc_now()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ParsedPick)
            .where(ParsedPick.es_apuesta == True)  # noqa: E712
            .where(ParsedPick.es_combinada == False)  # noqa: E712
            .where(ParsedPick.deporte != None)  # noqa: E711
            .where(ParsedPick.fecha_evento != None)  # noqa: E711
            .where(ParsedPick.fecha_evento >= now - _PAST_WINDOW)
            .where(ParsedPick.fecha_evento <= now + _FUTURE_WINDOW)
        )
        picks = [
            p
            for p in result.scalars().all()
            if _canonical_sport(p.deporte) in ("tenis", "futbol", "baloncesto")
        ]
        stats["candidatos"] = len(picks)

        stats["resueltos"] = await _resolve_missing_event_ids(session, picks, providers)

        # Eventos con pick: una captura alimenta todos los picks que
        # lo comparten (dedup por evento, no por canal).
        events: dict[str, dict] = {}
        for pick in picks:
            if not pick.odds_event_id:
                continue
            slot = events.setdefault(
                pick.odds_event_id,
                {
                    "sport": _canonical_sport(pick.deporte),
                    "start": pick.fecha_evento,
                    "pick_id": pick.id,
                },
            )
            slot["start"] = min(slot["start"], pick.fecha_evento)

        for event_ext_id, info in events.items():
            last = await session.execute(
                select(func.max(OddsSnapshot.captured_at)).where(
                    OddsSnapshot.event_ext_id == event_ext_id
                )
            )
            last_capture = last.scalar_one_or_none()
            if not _needs_snapshot(last_capture, info["start"], now):
                continue
            miss_key = f"odds|empty|{event_ext_id}"
            if is_missed(miss_key, _NO_ODDS_TTL):
                continue
            provider, choices = await _fetch_odds_across_providers(
                info["sport"], event_ext_id, providers
            )
            if provider is None:
                continue  # error de API en toda la cadena: reintento luego
            if not choices:
                mark_missed(miss_key)
                continue
            for choice in choices:
                session.add(
                    OddsSnapshot(
                        provider=provider.NAME,
                        event_ext_id=event_ext_id,
                        market_name=choice.market_name,
                        choice_group=choice.choice_group,
                        choice_name=choice.choice_name,
                        cuota=choice.cuota,
                        cuota_apertura=choice.cuota_apertura,
                        is_live=choice.is_live,
                        suspended=choice.suspended,
                        parsed_pick_id=info["pick_id"],
                    )
                )
                stats["filas"] += 1
            stats["capturas"] += 1
            logger.info(
                "[ODDS] %s: %d opciones guardadas vía %s",
                event_ext_id,
                len(choices),
                provider.NAME,
            )

        await session.commit()

    logger.info(
        "[ODDS] ciclo: %d candidatos, %d eventos resueltos, %d capturas, %d filas",
        stats["candidatos"],
        stats["resueltos"],
        stats["capturas"],
        stats["filas"],
    )
    return stats
