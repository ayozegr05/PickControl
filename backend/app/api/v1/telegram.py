"""Endpoints de mensajes crudos y picks extraídos de Telegram."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
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
    informante_id: int | None = Query(default=None),
    per_channel: int | None = Query(default=None, ge=1, le=50),
    solo_apuestas: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[ParsedPick]:
    """Devuelve los picks extraídos de los mensajes de Telegram.

    Dos modos de uso:

    - ``per_channel=N``: vista resumen con los N picks más recientes de
      cada canal. Garantiza que todos los canales aparezcan aunque unos
      publiquen mucho más que otros (los de menor volumen no quedan
      fuera por un corte global).
    - ``informante_id=X`` + ``offset``/``limit``: lista paginada de un
      solo canal, para la vista de detalle de la pantalla de depuración.

    ``solo_apuestas`` filtra ``es_apuesta=True`` ANTES de aplicar el
    top-N o la paginación, para que el resumen no lo ocupen mensajes
    descartados recientes (resultados, promociones, etc.).
    """
    filters = []
    if solo_apuestas:
        filters.append(ParsedPick.es_apuesta.is_(True))
    if informante_id is not None:
        filters.append(ParsedPick.informante_id == informante_id)

    if per_channel is not None:
        ranked = (
            select(
                ParsedPick.id.label("pick_id"),
                func.row_number()
                .over(
                    partition_by=ParsedPick.informante_id,
                    order_by=ParsedPick.created_at.desc(),
                )
                .label("rn"),
            )
            .where(*filters)
            .subquery()
        )
        stmt = (
            select(ParsedPick)
            .join(ranked, ParsedPick.id == ranked.c.pick_id)
            .where(ranked.c.rn <= per_channel)
            .order_by(ParsedPick.created_at.desc())
        )
    else:
        stmt = (
            select(ParsedPick)
            .where(*filters)
            .order_by(ParsedPick.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

    result = await session.exec(stmt)
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
