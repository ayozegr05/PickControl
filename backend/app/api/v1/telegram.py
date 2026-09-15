"""Endpoints de mensajes crudos y picks extraídos de Telegram."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_current_user
from app.db.postgres import get_session
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User

router = APIRouter(tags=["telegram"])


class ParsedPickAciertoUpdate(BaseModel):
    """Payload para corregir manualmente el resultado de un pick extraído."""

    acierto: bool | None = None
    # Apuesta anulada/devuelta (p. ej. "push" en hándicap/over-under).
    # Si es True, `acierto` se ignora y se guarda como None.
    anulada: bool = False


@router.get("/telegram/raw-messages", response_model=list[TelegramRawMessage])
async def listar_mensajes_crudos(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[TelegramRawMessage]:
    """Devuelve los últimos mensajes crudos recibidos de Telegram."""
    result = await session.exec(
        select(TelegramRawMessage)
        .order_by(TelegramRawMessage.received_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.all())


@router.get("/telegram/parsed-picks", response_model=list[ParsedPick])
async def listar_picks_extraidos(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[ParsedPick]:
    """Devuelve los picks extraídos de los mensajes de Telegram."""
    result = await session.exec(
        select(ParsedPick)
        .order_by(ParsedPick.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.all())


@router.patch("/telegram/parsed-picks/{pick_id}", response_model=ParsedPick)
async def corregir_acierto_pick(
    pick_id: int,
    payload: ParsedPickAciertoUpdate,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> ParsedPick:
    """Corrige manualmente el resultado de un pick (Acertó/Falló/Anulada/Pendiente).

    Útil cuando la verificación automática no puede resolverlo (deporte
    o mercado no soportado) o si se ha equivocado.
    """
    pick = await session.get(ParsedPick, pick_id)
    if pick is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pick no encontrado"
        )

    pick.acierto = None if payload.anulada else payload.acierto
    pick.anulada = payload.anulada
    pick.verificado_por = (
        "manual" if (payload.anulada or payload.acierto is not None) else None
    )

    session.add(pick)
    await session.commit()
    await session.refresh(pick)
    return pick
