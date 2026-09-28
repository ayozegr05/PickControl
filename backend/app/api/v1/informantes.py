"""Endpoints de informantes (tipsters).

Migrado de `backend/DbMongo/routes.js`:
- GET /informante/:informante (líneas 185-235)
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.postgres import get_session
from app.models.informante import Informante
from app.models.parsed_pick import ParsedPick
from app.models.pick import Acierto, Pick, PickSource
from app.schemas.informante import (
    InformanteOddsStats,
    InformanteStats,
    InformanteSummary,
)
from app.schemas.odds import OddsStatsBloque
from app.schemas.pick import CombinadaPata, PickRead
from app.services.odds.compare import aggregate_odds_stats, compare_picks
from app.services.pick_service import (
    _es_pick_simple,
    calcular_stats,
    calcular_stats_combinadas,
    calcular_stats_combinado,
    calcular_stats_parsed,
)

router = APIRouter(tags=["informantes"])


def _parsed_to_acierto(parsed: ParsedPick) -> Acierto:
    """Convierte el estado de un ParsedPick en el enum Acierto."""
    if parsed.anulada:
        return Acierto.PENDING
    if parsed.acierto is True:
        return Acierto.TRUE
    if parsed.acierto is False:
        return Acierto.FALSE
    return Acierto.PENDING


def _pick_to_read(
    pick: Pick, informante: Informante, ganancias: dict[int, float]
) -> PickRead:
    return PickRead(
        id=pick.id,
        apuesta=pick.apuesta,
        informante=informante.nombre,
        tipo_de_apuesta=pick.tipo_de_apuesta,
        acierto=pick.acierto,
        casa=pick.casa,
        cantidad_apostada=pick.cantidad_apostada,
        cuota=pick.cuota,
        fecha=pick.fecha,
        source=pick.source,
        parsed_pick_id=pick.parsed_pick_id,
        ganancia=ganancias.get(pick.id),
    )


def _pata_to_read(leg: ParsedPick) -> CombinadaPata:
    return CombinadaPata(
        id=leg.id,
        orden=leg.orden,
        seleccion=leg.seleccion,
        evento=leg.evento,
        mercado=leg.mercado,
        linea=leg.linea,
        cuota=leg.cuota,
        fecha_evento=leg.fecha_evento,
        acierto=leg.acierto,
        anulada=leg.anulada,
    )


def _parsed_to_read(
    parsed: ParsedPick,
    informante: Informante,
    ganancias: dict[int, float],
    patas: Optional[list[ParsedPick]] = None,
) -> PickRead:
    return PickRead(
        id=parsed.id,
        apuesta=parsed.seleccion or "",
        informante=informante.nombre,
        tipo_de_apuesta=parsed.mercado or "",
        acierto=_parsed_to_acierto(parsed),
        casa=parsed.casa or "",
        # Sin inventar: si el tipster no publicó cuota/stake quedan
        # NULL y la UI muestra "—" en vez de un 1.00 ficticio.
        cantidad_apostada=parsed.stake,
        cuota=parsed.cuota,
        fecha=parsed.fecha_evento or parsed.created_at,
        source=PickSource.TELEGRAM,
        ganancia=ganancias.get(parsed.id),
        evento=parsed.evento,
        es_reto=parsed.es_reto,
        es_combinada=parsed.es_combinada,
        cuota_efectiva=parsed.cuota_efectiva,
        patas=[_pata_to_read(p) for p in patas] if patas else None,
        anulada=parsed.anulada,
    )


@router.get("/informante/{nombre}", response_model=InformanteStats)
async def obtener_stats_informante(
    nombre: str, session: AsyncSession = Depends(get_session)
) -> InformanteStats:
    """Equivalente a `router.get("/informante/:informante", ...)`: devuelve
    las apuestas de un informante junto con sus estadísticas agregadas
    (total de apuestas, aciertos, ganancias, % de aciertos y Yield).

    Ahora también incluye los picks extraídos automáticamente de Telegram
    bajo el mismo nombre de informante/canal."""
    informante = (
        await session.exec(select(Informante).where(Informante.nombre == nombre))
    ).first()

    if informante is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró el informante.",
        )

    picks = (
        await session.exec(select(Pick).where(Pick.informante_id == informante.id))
    ).all()
    parsed_picks = (
        await session.exec(
            select(ParsedPick).where(ParsedPick.informante_id == informante.id)
        )
    ).all()

    if not picks and not parsed_picks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontraron apuestas para este informante.",
        )

    manual_stats = calcular_stats(picks)
    parsed_stats = calcular_stats_parsed(parsed_picks)
    combinadas_stats = calcular_stats_combinadas(parsed_picks)

    patas_por_padre: dict[int, list[ParsedPick]] = {}
    for parsed in parsed_picks:
        if parsed.combinada_id is not None:
            patas_por_padre.setdefault(parsed.combinada_id, []).append(parsed)
    for patas in patas_por_padre.values():
        patas.sort(key=lambda p: p.orden or 0)

    apuestas_read = [
        _pick_to_read(pick, informante, manual_stats.ganancias_por_pick)
        for pick in picks
    ]
    parsed_picks_read = [
        _parsed_to_read(
            parsed,
            informante,
            parsed_stats.ganancias_por_pick,
            patas=patas_por_padre.get(parsed.id),
        )
        for parsed in parsed_picks
        if parsed.es_apuesta and parsed.combinada_id is None
    ]

    return InformanteStats(
        informante=informante.nombre,
        total_apuestas=manual_stats.total_apuestas,
        total_aciertos=manual_stats.total_aciertos,
        ganancias=manual_stats.ganancias,
        porcentaje_aciertos=manual_stats.porcentaje_aciertos,
        yield_pct=manual_stats.yield_pct,
        total_pendientes=manual_stats.total_pendientes,
        apuestas=apuestas_read,
        parsed_picks=parsed_picks_read,
        parsed_total_apuestas=parsed_stats.total_apuestas,
        parsed_total_aciertos=parsed_stats.total_aciertos,
        parsed_ganancias=parsed_stats.ganancias,
        parsed_porcentaje_aciertos=parsed_stats.porcentaje_aciertos,
        parsed_yield_pct=parsed_stats.yield_pct,
        parsed_total_pendientes=parsed_stats.total_pendientes,
        combinadas_total=combinadas_stats.total_apuestas,
        combinadas_aciertos=combinadas_stats.total_aciertos,
        combinadas_ganancias=combinadas_stats.ganancias,
        combinadas_porcentaje=combinadas_stats.porcentaje_aciertos,
        combinadas_yield_pct=combinadas_stats.yield_pct,
        combinadas_pendientes=combinadas_stats.total_pendientes,
    )


@router.get("/informante/{nombre}/odds-stats", response_model=InformanteOddsStats)
async def odds_stats_informante(
    nombre: str, session: AsyncSession = Depends(get_session)
) -> InformanteOddsStats:
    """Agregados de la auditoría de cuotas del canal.

    % de picks con cuota inflada (la anunciada no existía en mercado al
    publicar), CLV medio y % que bate el cierre — el veredicto global
    del tipster: un pick aislado no dice nada, el patrón sí.
    """
    informante = (
        await session.exec(select(Informante).where(Informante.nombre == nombre))
    ).first()

    if informante is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró el informante.",
        )

    parsed_picks = (
        await session.exec(
            select(ParsedPick).where(ParsedPick.informante_id == informante.id)
        )
    ).all()
    # Mismo filtro que las stats de simples: fuera retos, combinadas y
    # patas — su cuota/stake atípicos distorsionarían el veredicto.
    simples = [p for p in parsed_picks if _es_pick_simple(p)]
    comparisons = await compare_picks(session, simples)
    stats = aggregate_odds_stats(simples, comparisons)
    return InformanteOddsStats(
        informante=informante.nombre, odds=OddsStatsBloque(**vars(stats))
    )


@router.get("/informantes", response_model=list[InformanteSummary])
async def listar_informantes(
    session: AsyncSession = Depends(get_session),
) -> list[InformanteSummary]:
    """Devuelve un resumen de los informantes con sus métricas
    de apuestas manuales, picks de Telegram y el total combinado.

    Solo se listan canales de Telegram reales (`es_canal_telegram`):
    el ranking y el formulario de alta manual trabajan siempre sobre
    canales monitorizados, no sobre nombres libres."""
    informantes = (
        await session.exec(
            select(Informante).where(Informante.es_canal_telegram.is_(True))
        )
    ).all()
    if not informantes:
        return []

    ids = [i.id for i in informantes]
    picks = (await session.exec(select(Pick).where(Pick.informante_id.in_(ids)))).all()
    parsed_picks = (
        await session.exec(select(ParsedPick).where(ParsedPick.informante_id.in_(ids)))
    ).all()

    picks_by: dict[int, list[Pick]] = {}
    parsed_by: dict[int, list[ParsedPick]] = {}
    for pick in picks:
        picks_by.setdefault(pick.informante_id, []).append(pick)
    for parsed in parsed_picks:
        parsed_by.setdefault(parsed.informante_id, []).append(parsed)

    resultados: list[InformanteSummary] = []
    for informante in informantes:
        manuales = picks_by.get(informante.id, [])
        telegram = parsed_by.get(informante.id, [])

        manual_stats = calcular_stats(manuales)
        parsed_stats = calcular_stats_parsed(telegram)
        combined = calcular_stats_combinado(manuales, telegram)

        resultados.append(
            InformanteSummary(
                informante=informante.nombre,
                manual_total=manual_stats.total_apuestas,
                manual_aciertos=manual_stats.total_aciertos,
                manual_ganancias=manual_stats.ganancias,
                manual_porcentaje=manual_stats.porcentaje_aciertos,
                manual_yield=manual_stats.yield_pct,
                manual_pendientes=manual_stats.total_pendientes,
                parsed_total=parsed_stats.total_apuestas,
                parsed_aciertos=parsed_stats.total_aciertos,
                parsed_ganancias=parsed_stats.ganancias,
                parsed_porcentaje=parsed_stats.porcentaje_aciertos,
                parsed_yield=parsed_stats.yield_pct,
                parsed_pendientes=parsed_stats.total_pendientes,
                total=combined.total_apuestas,
                aciertos=combined.total_aciertos,
                ganancias=combined.ganancias,
                porcentaje=combined.porcentaje_aciertos,
                yield_pct=combined.yield_pct,
                pendientes=combined.total_pendientes,
            )
        )

    # Ranking por rentabilidad: el yield (ganancia neta / total apostado)
    # mide la eficiencia del tipster; las ganancias absolutas dependerían
    # del volumen de picks, no de lo bueno que es.
    return sorted(resultados, key=lambda s: s.yield_pct, reverse=True)
