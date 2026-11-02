"""Cosecha manual de cuotas históricas (userscript OddsPortal).

Endpoints que consume el userscript de Tampermonkey (y mañana el
WebView de la app):

- `GET  /odds-harvest/queue` — eventos con picks pendientes que el
  script debe localizar y cosechar.
- `POST /odds-harvest/event` — ingesta de una página de partido.

Auth: token dedicado `ODDS_HARVEST_TOKEN` en cabecera
`X-Harvest-Key` — un JWT caducaría y exigiría login interactivo en
el script; un secreto fijo es el contrato correcto para una
herramienta de mantenimiento que solo corre en el navegador del
dueño. Sin token configurado el endpoint responde 503 (feature off).
"""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.db.postgres import get_session
from app.schemas.odds_harvest import (
    HarvestEventPayload,
    HarvestIngestResult,
    HarvestQueueRead,
)
from app.services.odds import harvest

router = APIRouter(prefix="/odds-harvest", tags=["odds-harvest"])


def _check_harvest_key(x_harvest_key: str = Header(default="")) -> None:
    expected = get_settings().odds_harvest_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cosecha deshabilitada: falta ODDS_HARVEST_TOKEN",
        )
    if not secrets.compare_digest(x_harvest_key, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Harvest-Key inválida",
        )


@router.get("/queue", response_model=HarvestQueueRead)
async def get_queue(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_check_harvest_key),
) -> HarvestQueueRead:
    """Cola de eventos pendientes de cosecha para el userscript."""
    return HarvestQueueRead(events=await harvest.harvest_queue(session))


@router.post("/event", response_model=HarvestIngestResult)
async def post_event(
    payload: HarvestEventPayload,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_check_harvest_key),
) -> HarvestIngestResult:
    """Ingesta una página de partido cosechada por el userscript."""
    return await harvest.ingest_harvest(session, payload)
