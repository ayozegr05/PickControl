"""Vista de sistema para administradores.

Expone el estado operativo de los providers de resultados/cuotas
(cuota agotada hoy, misses registrados) — información de depuración
que no debe ser visible para usuarios normales.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_current_user
from app.core.dates import utc_now
from app.db.postgres import get_session
from app.models.parsed_pick import ParsedPick
from app.models.user import User, UserRole
from app.services.results import base as results_base

router = APIRouter(prefix="/system", tags=["system"])


class ProvidersStatus(BaseModel):
    """Snapshot de `provider_state.json`: quién está sin cuota hoy y
    cuántos "no encontrado" lleva registrados cada provider."""

    rate_limited: dict[str, str]
    missed_by_provider: dict[str, int]
    calls_today: dict[str, int]
    calls_by_day: dict[str, dict[str, int]]


@router.get("/providers", response_model=ProvidersStatus)
async def providers_status(user: User = Depends(get_current_user)) -> ProvidersStatus:
    """Solo admins: el estado de cuotas es información interna."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores",
        )
    return ProvidersStatus(**results_base.providers_snapshot())


class PicksStatus(BaseModel):
    """Conteo de liquidación de picks para la pantalla de depuración.

    "Pendiente" = es_apuesta, sin acierto y no anulada. Se desglosa en
    simples (ni padre ni pata), patas (combinada_id != NULL) y padres
    combinada (es_combinada). `resueltas_hoy` usa `verificado_at`, que
    solo existe desde la migración e1f2a3b4c5d6 — los liquidados
    históricos cuentan en `resueltas_total` pero no en "hoy".
    """

    pendientes_simples: int
    pendientes_patas: int
    pendientes_combinadas: int
    resueltas_hoy: int
    resueltas_total: int


@router.get("/picks", response_model=PicksStatus)
async def picks_status(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PicksStatus:
    """Solo admins: estado de la cola de verificación."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores",
        )

    pendiente = (ParsedPick.acierto == None) & (  # noqa: E711
        ParsedPick.anulada == False  # noqa: E712
    )
    hoy = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)

    async def _count(*conds: object) -> int:
        q = select(func.count()).select_from(ParsedPick).where(*conds)
        return (await session.exec(q)).one()

    return PicksStatus(
        pendientes_simples=await _count(
            ParsedPick.es_apuesta == True,  # noqa: E712
            pendiente,
            ParsedPick.combinada_id == None,  # noqa: E711
            ParsedPick.es_combinada == False,  # noqa: E712
        ),
        pendientes_patas=await _count(
            pendiente, ParsedPick.combinada_id != None  # noqa: E711
        ),
        pendientes_combinadas=await _count(
            pendiente, ParsedPick.es_combinada == True  # noqa: E712
        ),
        resueltas_hoy=await _count(ParsedPick.verificado_at >= hoy),
        resueltas_total=await _count(
            (ParsedPick.acierto != None)  # noqa: E711
            | (ParsedPick.anulada == True)  # noqa: E712
        ),
    )
