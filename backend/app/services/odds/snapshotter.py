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
   Eventos enlazados vía ESPN (solo ganador/total/hándicap) se
   enriquecen cruzando a la familia Sofascore: el evento ya está
   localizado, así que córners/tarjetas/BTTS cuestan una búsqueda
   más — se guardan bajo el `event_ext_id` del pick con `provider`
   del espejo que las trajo (`_fetch_cross_family_odds`).

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
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds.apihub import (
    NAME_1XBET as APIHUB_XBET_NAME,
)
from app.services.odds.apihub import (
    NAME_FLASHSCORE as APIHUB_FS_NAME,
)
from app.services.odds.apihub import (
    build_providers as _build_apihub,
)
from app.services.odds.base import EventRef, OddsProvider
from app.services.odds.betsapi import (
    NAME_RAPID as BETSAPI_RAPID_NAME,
)
from app.services.odds.betsapi import (
    BetsApiOddsProvider,
)
from app.services.odds.espn_odds import EspnOddsProvider
from app.services.odds.pulsescore import PulseScoreOddsProvider
from app.services.odds.sofascore import SofaScoreOddsProvider
from app.services.odds.theoddsapi import (
    NAME as THEODDSAPI_NAME,
)
from app.services.odds.theoddsapi import (
    build_provider as _build_theoddsapi,
)
from app.services.odds.theoddsapi import (
    pick_needs_theoddsapi,
)
from app.services.results.base import (
    count_odds_rows_written,
    is_missed,
    is_rate_limited,
    mark_missed,
    match_score,
    translate_team_aliases,
)
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


def _capture_missed(miss_key: str, ttl: timedelta, start: Optional[datetime]) -> bool:
    """Nomatch de captura que caduca al entrar en la ventana de cierre.

    Las casas publican mercados tarde (las cards abren horas antes
    del pitido): un "no encontrado" de 12 h no puede aparcar el
    último intento — dentro de `_CLOSING_WINDOW` se reintenta en
    cada ciclo. Fuera de ella, el TTL normal ahorra cuota.
    """
    if start is not None and utc_now() >= start - _CLOSING_WINDOW:
        return False
    return is_missed(miss_key, ttl)


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
    # The Odds API: props de jugador y líneas alternas (500 créditos/
    # MES — el snapshotter solo le ruta picks que los necesitan).
    theoddsapi = _build_theoddsapi()
    if theoddsapi is not None:
        providers.append(theoddsapi)
    # BetsAPI vía wrapper RapidAPI (plan gratis ~24 req/día): el feed
    # bet365 REAL — congela el prematch de picks nuevos (córners y
    # tarjetas 3-way en líneas enteras, props, escalera alterna). Solo
    # sirve eventos sin empezar: los terminados devuelven el stub sin
    # mercados, para eso está el cliente directo de pago en backfill.
    if settings.rapidapi_tennis_key:
        providers.append(
            BetsApiOddsProvider(
                api_key=settings.rapidapi_tennis_key,
                api_host=settings.rapidapi_betsapi_host,
            )
        )
    # APIHub (1xBet etiquetado + FlashScore): último de la cadena —
    # sus llamadas cuestan cuota mensual, así que solo resuelve lo
    # que la cadena gratuita no encontró; el enriquecimiento de
    # mercados profundos va aparte (`_capture_apihub_odds`).
    providers.extend(_build_apihub())
    return providers


def _build_pulsescore() -> Optional[PulseScoreOddsProvider]:
    """Provider PulseScore para el enriquecimiento de futuros.

    NO entra en la cadena `providers`: su find_event gasta páginas
    del board (500 req/mes) — resolver eventos es trabajo de la
    cadena gratuita. Dos usos controlados: congela mercados
    profundos de eventos ya enlazados (`_capture_pulsescore_odds`)
    y actúa de última red para picks que ninguna cadena resolvió
    (`_resolve_missing_event_ids`, solo eventos próximos y con
    marca por pick para no re-paginar)."""
    settings = get_settings()
    key = getattr(settings, "pulsescore_key", None)
    if not key:
        return None
    return PulseScoreOddsProvider(
        api_key=key,
        api_host=getattr(settings, "pulsescore_host", "api.pulsescore.net"),
        bookmaker=getattr(settings, "pulsescore_bookmaker", "v3/bet365"),
    )


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
    # Aliases del tipster -> nombre oficial ("depò - barça" ->
    # "deportivo - barcelona"): mejora la búsqueda en la API y el
    # match_score local (compartido con results/base.py).
    hint = evento or fallback or None
    return translate_team_aliases(hint) if hint else None


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


async def _fetch_cross_family_odds(
    session: AsyncSession,
    sport: str,
    event_ext_id: str,
    start: Optional[datetime],
    providers: list[OddsProvider],
):
    """Enriquecimiento de un evento enlazado vía ESPN.

    ESPN pickcenter solo devuelve ganador/total/hándicap; el mismo
    partido en Sofascore añade córners, tarjetas, BTTS, doble
    oportunidad... El evento YA está localizado (equipos + fecha
    exactas del `OddsEvent`), así que el extra cuesta una búsqueda en
    la familia Sofascore + un fetch — las filas se escriben bajo el
    `event_ext_id` original con `provider` del espejo que las trajo
    (mismo convenio que `gemini-odds`: el id agrupa el partido real,
    el provider dice quién aportó la evidencia).
    """
    if sport != "futbol" or not event_ext_id.startswith("espn:"):
        return None, None
    sofa_providers = [
        p for p in providers if getattr(p, "ID_PREFIX", None) == "sofascore:"
    ]
    if not sofa_providers or start is None:
        return None, None
    ev = (
        await session.exec(
            select(OddsEvent).where(OddsEvent.event_ext_id == event_ext_id)
        )
    ).first()
    if ev is None or not ev.home_team or not ev.away_team:
        return None, None
    hint = f"{ev.home_team} vs {ev.away_team}"
    ref = await _find_event_across_providers(sport, start, hint, sofa_providers)
    if ref is None or match_score(hint, ref.home_team, ref.away_team) < 0.75:
        return None, None
    return await _fetch_odds_across_providers(sport, ref.event_ext_id, providers)


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


# Orden de prueba en resolución: el deporte declarado primero, el
# resto como fallback cross-sport (el LLM a veces clasifica mal —
# "París - Valencia" basket etiquetado como fútbol).
_RESOLVE_SPORTS = ("futbol", "baloncesto", "tenis")
# canónico -> etiqueta `deporte` de parsed_picks.
_CANONICAL_DEPORTE = {
    "futbol": "fútbol",
    "baloncesto": "baloncesto",
    "tenis": "tenis",
}
# PulseScore como última red de resolución: solo eventos próximos
# (su board lista "soonest first" y no publica semanas por delante)
# y marca por pick+deporte para no re-paginar el board cada ciclo —
# la marca caduca en la ventana de cierre vía `_capture_missed`.
_PULSE_RESOLVE_WINDOW = timedelta(days=3)
_PULSE_RESOLVE_MISS_TTL = timedelta(hours=6)


async def _resolve_missing_event_ids(
    session: AsyncSession,
    picks: list[ParsedPick],
    providers: list[OddsProvider],
    pulsescore: Optional[PulseScoreOddsProvider] = None,
) -> int:
    """Rellena `odds_event_id` de los picks pendientes y registra el
    evento en `odds_events`.

    Un pick ya enlazado cuyo evento no esté registrado (enlaces de
    antes de existir la tabla) se re-resuelve con la misma búsqueda:
    cuesta una vez y luego el pick deja de entrar en esta consulta.
    """
    registered = set((await session.exec(select(OddsEvent.event_ext_id))).all())
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
        # The Odds API solo resuelve picks que necesitan sus mercados
        # (props/líneas alternas): un pick estándar enlazado a un id
        # `theoddsapi:` gastaría créditos de la captura en vano — su
        # listado de eventos es gratis pero la descarga de odds no.
        usable = providers
        if not pick_needs_theoddsapi(pick):
            usable = [p for p in providers if p.NAME != THEODDSAPI_NAME]
        sports_to_try = (sport, *[s for s in _RESOLVE_SPORTS if s != sport])
        ref: Optional[EventRef] = None
        found_sport = sport
        for alt in sports_to_try:
            # La pista puede depender del deporte (en tenis cede al
            # jugador): se recalcula por intento, cayendo al hint
            # original si el alterno no la produce.
            alt_hint = _lookup_hint(pick, alt) or hint
            ref = await _find_event_across_providers(
                alt, pick.fecha_evento, alt_hint, usable
            )
            if ref is not None:
                found_sport = alt
                break
        if (
            ref is None
            and pulsescore is not None
            and pick.fecha_evento - utc_now() <= _PULSE_RESOLVE_WINDOW
            and not is_rate_limited(pulsescore.NAME)
        ):
            # Última red: su board trae los mercados dentro de cada
            # página — si casa, evento y cuotas llegan en la misma
            # llamada. Marca por pick+deporte; en ventana de cierre
            # se reintenta aunque hubiera miss (`_capture_missed`).
            for alt in sports_to_try:
                if alt not in pulsescore.SUPPORTED_SPORTS:
                    continue
                miss_key = f"{pulsescore.NAME}|resmiss|{pick.id}|{alt}"
                if _capture_missed(
                    miss_key, _PULSE_RESOLVE_MISS_TTL, pick.fecha_evento
                ):
                    continue
                alt_hint = _lookup_hint(pick, alt) or hint
                try:
                    ref = await pulsescore.find_event(alt, pick.fecha_evento, alt_hint)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[ODDS] Error resolviendo pick %s en %s (%s): %s",
                        pick.id,
                        pulsescore.NAME,
                        alt,
                        exc,
                    )
                    ref = None
                if ref is not None:
                    found_sport = alt
                    break
                if not is_rate_limited(pulsescore.NAME):
                    mark_missed(miss_key)
        if ref is None:
            continue
        if found_sport != sport:
            # El evento casó en otro board: el LLM clasificó mal el
            # deporte. Se corrige `deporte` para que la verificación
            # de resultados también busque donde toca.
            logger.info(
                "[ODDS] pick id=%s reclasificado %s -> %s (%s)",
                pick.id,
                sport,
                found_sport,
                ref.event_ext_id,
            )
            pick.deporte = _CANONICAL_DEPORTE[found_sport]
            sport = found_sport
        pick.odds_event_id = ref.event_ext_id
        session.add(pick)
        # Registro del evento (home/away hacen falta para casar la
        # selección del pick con la opción del mercado al comparar).
        # Savepoint por evento: dos ciclos concurrentes (loop +
        # pasada manual) pueden resolver el mismo partido a la vez —
        # sin él un UniqueViolationError tiraba la transacción entera
        # del ciclo (resoluciones + snapshots de todos los eventos).
        if ref.event_ext_id not in registered:
            event_row = OddsEvent(
                event_ext_id=ref.event_ext_id,
                sport=sport,
                home_team=ref.home_team,
                away_team=ref.away_team,
                start=ref.start,
            )
            try:
                async with session.begin_nested():
                    session.add(event_row)
            except IntegrityError:
                session.expunge(event_row)  # el otro ciclo ganó la carrera
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


# Props en The Odds API: si el evento no está en su feed (liga menor)
# se reintenta en ~12 h — los mercados prop se publican cerca del
# partido y un miss permanente dejaría picks futuros sin auditar.
_PROP_MISS_TTL = timedelta(hours=12)


async def _capture_theoddsapi_props(
    session: AsyncSession,
    event_ext_id: str,
    info: dict,
    providers: list[OddsProvider],
    stats: dict[str, int],
) -> None:
    """Captura props/líneas alternas de The Odds API para un evento
    ya enlazado por la cadena gratuita (espn:/sofascore:).

    El evento se re-localiza en su feed por nombre+fecha (listado
    gratis) y las filas se escriben bajo el `event_ext_id` original
    con provider "theoddsapi" — mismo convenio que el enriquecimiento
    cruzado Sofascore: el id agrupa el partido real.
    """
    provider = next((p for p in providers if p.NAME == THEODDSAPI_NAME), None)
    if provider is None or is_rate_limited(provider.NAME):
        return
    has_rows = (
        await session.exec(
            select(func.count(OddsSnapshot.id))
            .where(OddsSnapshot.event_ext_id == event_ext_id)
            .where(OddsSnapshot.provider == THEODDSAPI_NAME)
        )
    ).one()
    if has_rows:
        return
    miss_key = f"{THEODDSAPI_NAME}|nomatch|{event_ext_id}"
    if _capture_missed(miss_key, _PROP_MISS_TTL, info["start"]):
        return
    ev = (
        await session.exec(
            select(OddsEvent).where(OddsEvent.event_ext_id == event_ext_id)
        )
    ).first()
    if ev is None or not ev.home_team or not ev.away_team:
        return
    ref = await provider.find_event(
        info["sport"], info["start"], f"{ev.home_team} vs {ev.away_team}"
    )
    if ref is None:
        mark_missed(miss_key)
        return
    choices = await provider.fetch_odds(ref.event_ext_id, info["sport"])
    if choices is None:
        return  # error/cuota: reintenta en el próximo ciclo
    if not choices:
        mark_missed(miss_key)
        return
    for choice in choices:
        session.add(
            OddsSnapshot(
                provider=THEODDSAPI_NAME,
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
    logger.info(
        "[ODDS] %s: +%d props/alternas vía %s",
        event_ext_id,
        len(choices),
        provider.NAME,
    )


# APIHub (1xBet/FlashScore): cuota mensual compartida — los mercados
# del tipster (3-way córners/tarjetas, props) solo existen aquí.
_APIHUB_MISS_TTL = timedelta(hours=12)


def pick_needs_apihub(pick: ParsedPick, sport: Optional[str]) -> bool:
    """True si el pick necesita los mercados profundos de APIHub.

    Fútbol: mismos picks que The Odds API (córners/tarjetas/props/
    líneas alternas) — 1xBet es el ÚNICO con el 3-way exacto del
    tipster. Tenis/basket: cualquier pick — mercados de juegos/sets
    que la cadena gratuita no publica.
    """
    if sport == "futbol":
        return pick_needs_theoddsapi(pick)
    return sport in ("tenis", "baloncesto")


async def _capture_apihub_odds(
    session: AsyncSession,
    event_ext_id: str,
    info: dict,
    providers: list[OddsProvider],
    stats: dict[str, int],
) -> None:
    """Enriquecimiento con mercados profundos de APIHub para un
    evento ya enlazado por la cadena gratuita.

    Prueba 1xBet primero (mercados etiquetados profundos: 3Way
    corners/cards, totales de equipo, props) y FlashScore como
    respaldo vivo (multi-casa, ~7 días). Las filas se escriben bajo
    el `event_ext_id` original con `provider` del servicio que las
    trajo — mismo convenio que `_capture_theoddsapi_props`.
    """
    for provider_name in (APIHUB_XBET_NAME, APIHUB_FS_NAME):
        provider = next((p for p in providers if p.NAME == provider_name), None)
        if provider is None or is_rate_limited(provider.NAME):
            continue
        has_rows = (
            await session.exec(
                select(func.count(OddsSnapshot.id))
                .where(OddsSnapshot.event_ext_id == event_ext_id)
                .where(OddsSnapshot.provider == provider_name)
            )
        ).one()
        if has_rows:
            return  # ya enriquecido por este servicio
        miss_key = f"{provider_name}|nomatch|{event_ext_id}"
        if _capture_missed(miss_key, _APIHUB_MISS_TTL, info["start"]):
            continue
        ev = (
            await session.exec(
                select(OddsEvent).where(OddsEvent.event_ext_id == event_ext_id)
            )
        ).first()
        if ev is None or not ev.home_team or not ev.away_team:
            return
        try:
            ref = await provider.find_event(
                info["sport"], info["start"], f"{ev.home_team} vs {ev.away_team}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ODDS] Error resolviendo %s en %s: %s",
                event_ext_id,
                provider.NAME,
                exc,
            )
            return
        if ref is None:
            mark_missed(miss_key)
            continue
        try:
            choices = await provider.fetch_odds(ref.event_ext_id, info["sport"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ODDS] Error pidiendo odds %s en %s: %s",
                event_ext_id,
                provider.NAME,
                exc,
            )
            return
        if choices is None:
            return  # error/cuota: reintenta en el próximo ciclo
        if not choices:
            mark_missed(miss_key)
            continue
        for choice in choices:
            session.add(
                OddsSnapshot(
                    provider=provider_name,
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
        logger.info(
            "[ODDS] %s: +%d mercados profundos vía %s",
            event_ext_id,
            len(choices),
            provider.NAME,
        )
        return


# BetsAPI RapidAPI (plan gratis ~24 req/día): el prematch muere al
# pitido inicial, así que el congelado corre para TODO evento futuro
# enlazado — no solo los de mercados profundos. Doble captura por
# evento: una al enlazarse (≈ cuota al publicar) y otra dentro de la
# ventana de cierre (las tarjetas y parte de los props abren tarde).
_BETSAPI_MISS_TTL = timedelta(hours=12)
# Capturas por ciclo: freno anti-ráfaga — el 429 aparca el provider
# igualmente, pero sin tope un solo ciclo grande vaciaría el día.
_BETSAPI_MAX_CAPTURES_PER_CYCLE = 6


async def _capture_betsapi_odds(
    session: AsyncSession,
    event_ext_id: str,
    info: dict,
    providers: list[OddsProvider],
    stats: dict[str, int],
) -> None:
    """Congela el prematch bet365 del evento vía el wrapper gratis.

    Las filas se escriben bajo el `event_ext_id` del pick (espn:/
    sofascore:) con provider "betsapi_rapid" — mismo convenio que el
    enriquecimiento APIHub: el id agrupa el partido real.
    """
    provider = next((p for p in providers if p.NAME == BETSAPI_RAPID_NAME), None)
    if provider is None or is_rate_limited(provider.NAME):
        return
    if event_ext_id.startswith(BetsApiOddsProvider.ID_PREFIX):
        return  # evento ya enlazado a su espacio: la captura normal lo cubre
    start: Optional[datetime] = info.get("start")
    now = utc_now()
    # El plan gratis solo sirve prematch de eventos SIN empezar: un
    # partido ya empezado devuelve el stub vacío — ni se intenta.
    if start is None or start <= now:
        return
    if stats.get("betsapi_capturas", 0) >= _BETSAPI_MAX_CAPTURES_PER_CYCLE:
        return
    row = (
        await session.exec(
            select(func.count(OddsSnapshot.id), func.max(OddsSnapshot.captured_at))
            .where(OddsSnapshot.event_ext_id == event_ext_id)
            .where(OddsSnapshot.provider == BETSAPI_RAPID_NAME)
        )
    ).first()
    n_rows, last_cap = (row[0], row[1]) if row else (0, None)
    need = n_rows == 0 or (
        now >= start - _CLOSING_WINDOW
        and (last_cap is None or last_cap < start - _CLOSING_WINDOW)
    )
    if not need:
        return
    miss_key = f"{BETSAPI_RAPID_NAME}|nomatch|{event_ext_id}"
    if _capture_missed(miss_key, _BETSAPI_MISS_TTL, start):
        return
    ev = (
        await session.exec(
            select(OddsEvent).where(OddsEvent.event_ext_id == event_ext_id)
        )
    ).first()
    if ev is None or not ev.home_team or not ev.away_team:
        return
    try:
        ref = await provider.find_event(
            info["sport"], start, f"{ev.home_team} vs {ev.away_team}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ODDS] Error resolviendo %s en %s: %s",
            event_ext_id,
            provider.NAME,
            exc,
        )
        return
    if ref is None:
        # Solo se marca nomatch si la búsqueda pudo correr entera:
        # un find_event abortado por 429/cuota a mitad de páginas
        # no es "no encontrado" — registrarlo así lo escondería 12h.
        if not is_rate_limited(provider.NAME):
            mark_missed(miss_key)
        return
    try:
        choices = await provider.fetch_odds(ref.event_ext_id, info["sport"])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ODDS] Error pidiendo odds %s en %s: %s",
            event_ext_id,
            provider.NAME,
            exc,
        )
        return
    if choices is None:
        return  # error/cuota: reintenta en el próximo ciclo
    if not choices:
        # Evento futuro sin mercados aún: miss provisional — las
        # cards/props abren tarde y el reintento las caza.
        mark_missed(miss_key)
        return
    stats["betsapi_capturas"] = stats.get("betsapi_capturas", 0) + 1
    for choice in choices:
        session.add(
            OddsSnapshot(
                provider=BETSAPI_RAPID_NAME,
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
    logger.info(
        "[ODDS] %s: +%d mercados bet365 congelados vía %s",
        event_ext_id,
        len(choices),
        provider.NAME,
    )


# PulseScore (plan BASIC 500 req/mes): mismo feed bet365 pero
# normalizado y con mercados que el wrapper no siempre devuelve
# (las tarjetas del sample venían llenas aquí y vacías allí). El
# board trae los mercados dentro, así que una página resuelve el
# evento Y sus cuotas; el límite real lo pone su presupuesto de
# páginas por ciclo, no este tope.
_PULSE_MISS_TTL = timedelta(hours=12)
_PULSE_MAX_CAPTURES_PER_CYCLE = 6


async def _capture_pulsescore_odds(
    session: AsyncSession,
    event_ext_id: str,
    info: dict,
    provider: Optional[PulseScoreOddsProvider],
    stats: dict[str, int],
) -> None:
    """Congela el prematch bet365 del evento vía PulseScore.

    Mismo convenio que `_capture_betsapi_odds`: las filas se
    escriben bajo el `event_ext_id` del pick con provider
    "pulsescore"."""
    if provider is None or is_rate_limited(provider.NAME):
        return
    if event_ext_id.startswith(PulseScoreOddsProvider.ID_PREFIX):
        return
    start: Optional[datetime] = info.get("start")
    now = utc_now()
    # El board solo sirve eventos SIN empezar: un partido ya
    # empezado devuelve data:null — ni se intenta.
    if start is None or start <= now:
        return
    if stats.get("pulsescore_capturas", 0) >= _PULSE_MAX_CAPTURES_PER_CYCLE:
        return
    row = (
        await session.exec(
            select(func.count(OddsSnapshot.id), func.max(OddsSnapshot.captured_at))
            .where(OddsSnapshot.event_ext_id == event_ext_id)
            .where(OddsSnapshot.provider == provider.NAME)
        )
    ).first()
    n_rows, last_cap = (row[0], row[1]) if row else (0, None)
    need = n_rows == 0 or (
        now >= start - _CLOSING_WINDOW
        and (last_cap is None or last_cap < start - _CLOSING_WINDOW)
    )
    if not need:
        return
    miss_key = f"{provider.NAME}|nomatch|{event_ext_id}"
    if _capture_missed(miss_key, _PULSE_MISS_TTL, start):
        return
    ev = (
        await session.exec(
            select(OddsEvent).where(OddsEvent.event_ext_id == event_ext_id)
        )
    ).first()
    if ev is None or not ev.home_team or not ev.away_team:
        return
    try:
        ref = await provider.find_event(
            info["sport"], start, f"{ev.home_team} vs {ev.away_team}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ODDS] Error resolviendo %s en %s: %s",
            event_ext_id,
            provider.NAME,
            exc,
        )
        return
    if ref is None:
        if not is_rate_limited(provider.NAME):
            mark_missed(miss_key)
        return
    try:
        choices = await provider.fetch_odds(ref.event_ext_id, info["sport"])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ODDS] Error pidiendo odds %s en %s: %s",
            event_ext_id,
            provider.NAME,
            exc,
        )
        return
    if choices is None:
        return  # error/cuota: reintenta en el próximo ciclo
    if not choices:
        mark_missed(miss_key)
        return
    stats["pulsescore_capturas"] = stats.get("pulsescore_capturas", 0) + 1
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
    logger.info(
        "[ODDS] %s: +%d mercados bet365 congelados vía %s",
        event_ext_id,
        len(choices),
        provider.NAME,
    )


async def run_odds_snapshot_cycle() -> dict[str, int]:
    """Un ciclo del snapshotter. Devuelve contadores para logs/tests."""
    providers = await _get_odds_providers()
    pulsescore = _build_pulsescore()
    stats = {"candidatos": 0, "resueltos": 0, "capturas": 0, "filas": 0}
    if not providers:
        return stats

    now = utc_now()
    async with AsyncSessionLocal() as session:
        result = await session.exec(
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
            for p in result.all()
            if _canonical_sport(p.deporte) in ("tenis", "futbol", "baloncesto")
        ]
        stats["candidatos"] = len(picks)

        stats["resueltos"] = await _resolve_missing_event_ids(
            session, picks, providers, pulsescore
        )

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
                    "needs_props": False,
                    "needs_apihub": False,
                },
            )
            slot["start"] = min(slot["start"], pick.fecha_evento)
            slot["needs_props"] = slot["needs_props"] or pick_needs_theoddsapi(pick)
            slot["needs_apihub"] = slot["needs_apihub"] or pick_needs_apihub(
                pick, slot["sport"]
            )

        # Eventos más próximos a empezar primero: la cuota diaria del
        # wrapper gratis es escasa y su prematch muere al pitido.
        for event_ext_id, info in sorted(
            events.items(),
            key=lambda kv: kv[1]["start"] or now,
        ):
            # Enriquecimiento de props: corre independiente del ritmo
            # de captura principal — The Odds API publica mercados que
            # nadie más tiene, y sus créditos son mensuales.
            if info["needs_props"] and info["sport"] == "futbol":
                await _capture_theoddsapi_props(
                    session, event_ext_id, info, providers, stats
                )
            # Mercados profundos APIHub (3-way córners/tarjetas del
            # tipster, props, sets/juegos): una sola descarga por
            # evento — sus filas conviven con las de la cadena gratis.
            if info["needs_apihub"]:
                await _capture_apihub_odds(
                    session, event_ext_id, info, providers, stats
                )
            # Congelado bet365 vía wrapper gratis: corre para TODO
            # evento futuro enlazado (su prematch muere al pitido).
            await _capture_betsapi_odds(session, event_ext_id, info, providers, stats)
            # Segundo congelado bet365 vía PulseScore (500/mes): su
            # esquema normalizado expone mercados que el wrapper a
            # veces devuelve vacíos (cards del sample).
            await _capture_pulsescore_odds(
                session, event_ext_id, info, pulsescore, stats
            )
            last = await session.exec(
                select(func.max(OddsSnapshot.captured_at)).where(
                    OddsSnapshot.event_ext_id == event_ext_id
                )
            )
            last_capture = last.first()
            if not _needs_snapshot(last_capture, info["start"], now):
                continue
            miss_key = f"odds|empty|{event_ext_id}"
            if _capture_missed(miss_key, _NO_ODDS_TTL, info["start"]):
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

            # Enriquecimiento cruzado: un `espn:*` solo trajo
            # ganador/total/hándicap — se busca el mismo partido en la
            # familia Sofascore y sus mercados ricos se guardan bajo
            # el mismo evento (provider = quien los trajo).
            x_provider, x_choices = await _fetch_cross_family_odds(
                session,
                info["sport"],
                event_ext_id,
                info["start"],
                providers,
            )
            if x_provider is not None and x_choices:
                for choice in x_choices:
                    session.add(
                        OddsSnapshot(
                            provider=x_provider.NAME,
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
                logger.info(
                    "[ODDS] %s: +%d opciones cruzadas vía %s",
                    event_ext_id,
                    len(x_choices),
                    x_provider.NAME,
                )

        await session.commit()
        count_odds_rows_written(stats["filas"])

    logger.info(
        "[ODDS] ciclo: %d candidatos, %d eventos resueltos, %d capturas, %d filas",
        stats["candidatos"],
        stats["resueltos"],
        stats["capturas"],
        stats["filas"],
    )
    return stats
