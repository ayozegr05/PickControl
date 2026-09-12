"""Endpoints de mensajes crudos y picks extraídos de Telegram."""

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_current_user
from app.db.postgres import get_session
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User

router = APIRouter(tags=["telegram"])


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
