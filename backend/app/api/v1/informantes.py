"""Endpoints de informantes (tipsters).

Migrado de `backend/DbMongo/routes.js`:
- GET /informante/:informante (líneas 185-235)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.postgres import get_session
from app.models.informante import Informante
from app.models.parsed_pick import ParsedPick
from app.models.pick import Acierto, Pick, PickSource
from app.schemas.informante import InformanteStats
from app.schemas.pick import PickRead
from app.services.pick_service import (
    calcular_stats,
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
        ganancia=ganancias.get(pick.id),
    )


def _parsed_to_read(
    parsed: ParsedPick, informante: Informante, ganancias: dict[int, float]
) -> PickRead:
    return PickRead(
        id=parsed.id,
        apuesta=parsed.seleccion or "",
        informante=informante.nombre,
        tipo_de_apuesta=parsed.mercado or "",
        acierto=_parsed_to_acierto(parsed),
        casa=parsed.casa or "",
        cantidad_apostada=parsed.stake or 1.0,
        cuota=parsed.cuota or 1.0,
        fecha=parsed.fecha_evento or parsed.created_at,
        source=PickSource.TELEGRAM,
        ganancia=ganancias.get(parsed.id),
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

    apuestas_read = [
        _pick_to_read(pick, informante, manual_stats.ganancias_por_pick)
        for pick in picks
    ]
    parsed_picks_read = [
        _parsed_to_read(parsed, informante, parsed_stats.ganancias_por_pick)
        for parsed in parsed_picks
    ]

    return InformanteStats(
        informante=informante.nombre,
        total_apuestas=manual_stats.total_apuestas,
        total_aciertos=manual_stats.total_aciertos,
        ganancias=manual_stats.ganancias,
        porcentaje_aciertos=manual_stats.porcentaje_aciertos,
        yield_pct=manual_stats.yield_pct,
        apuestas=apuestas_read,
        parsed_picks=parsed_picks_read,
        parsed_total_apuestas=parsed_stats.total_apuestas,
        parsed_total_aciertos=parsed_stats.total_aciertos,
        parsed_ganancias=parsed_stats.ganancias,
        parsed_porcentaje_aciertos=parsed_stats.porcentaje_aciertos,
        parsed_yield_pct=parsed_stats.yield_pct,
    )
