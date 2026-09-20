"""Registro dinámico de canales monitorizados.

La tabla `channels` es la fuente de verdad: los handlers de Telethon y
el catch-up consultan el set de `channel_id` activos en memoria, que se
recarga con `refresh_channel_cache` al arrancar y tras cada cambio por
API. Así añadir/quitar canales no requiere reiniciar el listener.
"""

from typing import Any

from sqlalchemy import func
from sqlmodel import select
from telethon import TelegramClient, utils

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.channel import Channel

logger = get_logger("app.telegram")

_active_channel_ids: set[int] = set()
_channel_names: dict[int, str] = {}


def active_channel_ids() -> set[int]:
    """Ids marcados de Telethon (`-100...`) de los canales activos."""
    return _active_channel_ids


def channel_name_for(chat_id: int) -> str:
    """Nombre de display del canal, o el id si no está resuelto."""
    return _channel_names.get(chat_id, str(chat_id))


async def seed_channels_from_env() -> None:
    """Siembra `channels` desde TELEGRAM_TARGET_CHANNEL si está vacía.

    Bootstrap de la migración .env -> BD. Solo corre cuando la tabla no
    tiene ninguna fila, así no re-siembra canales borrados a propósito.
    """
    settings = get_settings()
    raw_targets = settings.telegram_target_channel or ""
    targets = [t.strip() for t in raw_targets.split(",") if t.strip()]
    if not targets:
        return
    async with AsyncSessionLocal() as session:
        count = (await session.exec(select(func.count()).select_from(Channel))).one()
        if count:
            return
        for target in targets:
            session.add(Channel(target=target, activo=True))
        await session.commit()
        logger.info("channels: sembrados %d canales desde .env", len(targets))


async def resolve_channel_target(
    client: TelegramClient, target: str
) -> tuple[Any | None, int | None]:
    """Resuelve un target (username/id/nombre) a (entity, channel_id).

    `channel_id` se devuelve en el formato con marca de Telethon
    (`-100<id>` para canales), el mismo que usa `event.chat_id` en los
    handlers y `channel_id` en los raws.
    """
    from app.services.telegram.handlers import looks_like_id, to_telegram_chat_id

    if looks_like_id(target):
        numeric = to_telegram_chat_id(target)
        # Un entero sin contexto no siempre se puede resolver con
        # get_entity; lo buscamos entre los diálogos del usuario.
        async for dialog in client.iter_dialogs():
            peer_id = utils.get_peer_id(dialog.entity, add_mark=True)
            if peer_id == numeric or dialog.entity.id == abs(numeric):
                return dialog.entity, peer_id
        return None, None

    entity = await client.get_entity(target)
    return entity, utils.get_peer_id(entity, add_mark=True)


async def refresh_channel_cache(client: TelegramClient | None = None) -> set[int]:
    """Recarga el caché de canales activos desde la tabla `channels`.

    Con `client` resuelve y persiste `channel_id`/`name`/`username` de
    los canales que aún no lo tienen. Sin `client` solo recarga los ya
    resueltos (los pendientes se ignoran hasta poder resolverlos).
    """
    global _active_channel_ids, _channel_names
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Channel).where(
                Channel.activo,
                Channel.eliminado == False,  # noqa: E712
            )
        )
        channels = list(result.all())
        ids: set[int] = set()
        names: dict[int, str] = {}
        for ch in channels:
            if ch.channel_id is None and client is not None:
                try:
                    entity, marked = await resolve_channel_target(client, ch.target)
                except Exception:
                    logger.exception("channels: no se pudo resolver %r", ch.target)
                    continue
                if marked is None:
                    logger.warning(
                        "channels: %r no encontrado en los diálogos", ch.target
                    )
                    continue
                ch.channel_id = marked
                ch.name = getattr(entity, "title", None) or ch.target
                ch.username = getattr(entity, "username", None)
                session.add(ch)
            if ch.channel_id is None:
                continue
            ids.add(ch.channel_id)
            names[ch.channel_id] = ch.name or ch.target
        await session.commit()
    _active_channel_ids = ids
    _channel_names = names
    logger.info("channels: %d canales activos %s", len(ids), sorted(ids))
    return ids
