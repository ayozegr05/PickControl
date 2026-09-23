"""Vista de sistema para administradores.

Expone el estado operativo de los providers de resultados/cuotas
(cuota agotada hoy, misses registrados) — información de depuración
que no debe ser visible para usuarios normales.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_current_user
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
