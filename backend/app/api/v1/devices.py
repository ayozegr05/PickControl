"""Registro de dispositivos para notificaciones push.

La app móvil (Expo) llama a `POST /devices` tras obtener su
`ExponentPushToken` — al arrancar si ya hay sesión y tras cada login.
El upsert por token hace el registro idempotente y reasigna la fila si
el móvil cambió de cuenta.
"""

from fastapi import APIRouter, Depends, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_current_user
from app.core.dates import utc_now
from app.db.postgres import get_session
from app.models.device_token import DeviceToken
from app.models.user import User
from app.schemas.device import DeviceTokenCreate, DeviceTokenRead

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "",
    response_model=DeviceTokenRead,
    status_code=status.HTTP_201_CREATED,
)
async def register_device(
    payload: DeviceTokenCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> DeviceToken:
    """Registra (o refresca) el token push del dispositivo del usuario."""
    token = payload.token.strip()
    existing = (
        await session.exec(select(DeviceToken).where(DeviceToken.token == token))
    ).first()
    if existing is not None:
        existing.user_id = user.id
        existing.platform = payload.platform
        existing.enabled = True
        existing.updated_at = utc_now()
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing
    device = DeviceToken(
        user_id=user.id,
        token=token,
        platform=payload.platform,
    )
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return device


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device(
    payload: DeviceTokenCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    """Desactiva el token del dispositivo (logout / desinstalar).

    Idempotente: un token desconocido devuelve 204 igualmente — el
    cliente no debe distinguir "no existía" de "ya desactivado".
    """
    existing = (
        await session.exec(
            select(DeviceToken).where(
                DeviceToken.token == payload.token.strip(),
                DeviceToken.user_id == user.id,
            )
        )
    ).first()
    if existing is not None:
        existing.enabled = False
        existing.updated_at = utc_now()
        session.add(existing)
        await session.commit()
