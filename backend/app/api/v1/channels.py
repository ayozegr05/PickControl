"""CRUD de canales de Telegram monitorizados.

La tabla `channels` es la fuente de verdad del listener y del catch-up.
Tras cada cambio se recarga el caché en memoria (`refresh_channel_cache`)
para que el alta/baja surta efecto sin reiniciar el backend.

Borrar un canal solo lo quita de la monitorización: los mensajes crudos
y los picks ya guardados se conservan (auditoría).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telethon import TelegramClient, utils
from telethon.tl.types import Channel as TlChannel

from app.api.deps import get_current_user
from app.db.postgres import get_session
from app.models.channel import Channel
from app.models.user import User
from app.schemas.channel import (
    AvailableChannelRead,
    ChannelCreate,
    ChannelRead,
    ChannelUpdate,
)
from app.services.telegram.catchup import run_catchup_for_target
from app.services.telegram.channels import (
    refresh_channel_cache,
    resolve_channel_target,
)
from app.services.telegram.client import get_telegram_client

router = APIRouter(prefix="/channels", tags=["channels"])


def _connected_client() -> TelegramClient:
    """Cliente Telethon conectado o 503 (listener no iniciado)."""
    client = get_telegram_client()
    if not client.is_connected():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El listener de Telegram no está conectado.",
        )
    return client


def _normalize_target(raw: str) -> str:
    """Normaliza la entrada del usuario a username o id.

    Acepta `t.me/canal`, `https://t.me/canal`, `@canal`, `canal` o un id
    numérico. Los enlaces de invitación privados (`t.me/+hash`) no se
    pueden resolver sin unirse antes al canal con la cuenta.
    """
    target = raw.strip()
    if "t.me/" in target:
        target = target.split("t.me/", 1)[1]
    target = target.split("?", 1)[0].strip("/").lstrip("@").strip()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Introduce un enlace t.me, @usuario o id de canal.",
        )
    if target.startswith(("+", "joinchat")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Enlace de invitación privado: únete al canal con tu "
                "cuenta de Telegram y luego selecciónalo en la lista."
            ),
        )
    return target


@router.get("", response_model=list[ChannelRead])
async def listar_canales(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[Channel]:
    """Canales configurados (activos e inactivos)."""
    result = await session.exec(select(Channel).order_by(Channel.id))
    return list(result.all())


@router.get("/disponibles", response_model=list[AvailableChannelRead])
async def listar_canales_disponibles(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[AvailableChannelRead]:
    """Canales visibles para la cuenta de Telegram (picker de la app).

    Recorre los diálogos de la sesión Telethon y devuelve los canales y
    supergrupos que la cuenta sigue, marcando cuáles ya se monitorizan.
    """
    client = _connected_client()

    monitored = await session.exec(select(Channel.channel_id).where(Channel.activo))
    monitored_ids = set(monitored.all())

    available: list[AvailableChannelRead] = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, TlChannel):
            continue
        marked_id = utils.get_peer_id(entity, add_mark=True)
        available.append(
            AvailableChannelRead(
                channel_id=marked_id,
                name=entity.title or str(marked_id),
                username=entity.username,
                monitorizado=marked_id in monitored_ids,
            )
        )
    return available


@router.post("", response_model=ChannelRead, status_code=status.HTTP_201_CREATED)
async def crear_canal(
    payload: ChannelCreate,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> Channel:
    """Añade un canal a la monitorización y dispara su catch-up.

    El `target` puede ser enlace t.me, @username o id numérico; se
    resuelve contra la cuenta de Telegram para obtener el id canónico.
    """
    client = _connected_client()
    target = _normalize_target(payload.target)

    try:
        entity, marked_id = await resolve_channel_target(client, target)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se pudo resolver el canal {target!r}: {exc}",
        ) from exc
    if entity is None or marked_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"El canal {target!r} no aparece en los diálogos de la "
                "cuenta; únete a él en Telegram primero."
            ),
        )
    if not isinstance(entity, TlChannel):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El destino resuelto no es un canal ni un grupo.",
        )

    name = getattr(entity, "title", None) or target
    username = getattr(entity, "username", None)

    result = await session.exec(
        select(Channel).where(
            (Channel.target == target) | (Channel.channel_id == marked_id)
        )
    )
    channel = result.first()
    if channel is None:
        channel = Channel(
            target=target, channel_id=marked_id, name=name, username=username
        )
    else:
        channel.channel_id = marked_id
        channel.name = name
        channel.username = username
        channel.activo = True
    session.add(channel)
    await session.commit()
    await session.refresh(channel)

    await refresh_channel_cache(client)
    asyncio.create_task(run_catchup_for_target(client, str(marked_id)))
    return channel


@router.patch("/{channel_pk}", response_model=ChannelRead)
async def actualizar_canal(
    channel_pk: int,
    payload: ChannelUpdate,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> Channel:
    """Activa o desactiva la monitorización de un canal."""
    channel = await session.get(Channel, channel_pk)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Canal no encontrado."
        )
    channel.activo = payload.activo
    session.add(channel)
    await session.commit()
    await session.refresh(channel)

    client = get_telegram_client()
    await refresh_channel_cache(client if client.is_connected() else None)
    if payload.activo and client.is_connected() and channel.channel_id:
        asyncio.create_task(run_catchup_for_target(client, str(channel.channel_id)))
    return channel


@router.delete("/{channel_pk}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_canal(
    channel_pk: int,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> None:
    """Quita el canal de la monitorización.

    No borra mensajes crudos ni picks históricos: siguen en la BD como
    auditoría del canal.
    """
    channel = await session.get(Channel, channel_pk)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Canal no encontrado."
        )
    await session.delete(channel)
    await session.commit()

    client = get_telegram_client()
    await refresh_channel_cache(client if client.is_connected() else None)
