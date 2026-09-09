"""Endpoints de informantes (tipsters).

Migrado de `backend/DbMongo/routes.js`:
- GET /informante/:informante (líneas 185-235)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.postgres import get_session
from app.models.informante import Informante
from app.models.pick import Pick
from app.schemas.informante import InformanteStats
from app.schemas.pick import PickRead
from app.services.pick_service import calcular_stats

router = APIRouter(tags=["informantes"])


@router.get("/informante/{nombre}", response_model=InformanteStats)
async def obtener_stats_informante(
    nombre: str, session: AsyncSession = Depends(get_session)
) -> InformanteStats:
    """Equivalente a `router.get("/informante/:informante", ...)`: devuelve
    las apuestas de un informante junto con sus estadísticas agregadas
    (total de apuestas, aciertos, ganancias, % de aciertos y Yield)."""
    informante = (
        await session.exec(select(Informante).where(Informante.nombre == nombre))
    ).first()

    if informante is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontraron apuestas para este informante.",
        )

    picks = (
        await session.exec(select(Pick).where(Pick.informante_id == informante.id))
    ).all()

    if not picks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontraron apuestas para este informante.",
        )

    stats = calcular_stats(picks)

    apuestas_read = [
        PickRead(
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
            ganancia=stats.ganancias_por_pick.get(pick.id),
        )
        for pick in picks
    ]

    return InformanteStats(
        informante=informante.nombre,
        total_apuestas=stats.total_apuestas,
        total_aciertos=stats.total_aciertos,
        ganancias=stats.ganancias,
        porcentaje_aciertos=stats.porcentaje_aciertos,
        yield_pct=stats.yield_pct,
        apuestas=apuestas_read,
    )
