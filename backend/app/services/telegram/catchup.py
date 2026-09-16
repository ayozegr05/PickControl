"""Catch-up de mensajes publicados mientras el backend estaba apagado.

`events.NewMessage` solo dispara con mensajes que llegan en vivo; los
publicados durante un downtime se perderían. Al arrancar, este módulo
recorre cada canal configurado y procesa solo los mensajes posteriores
al último `message_id` ya guardado en `telegram_raw_messages` (la marca
de agua). Si el canal no tiene historial en BD, se siembra con los
últimos `_SEED_LIMIT` mensajes.
"""

from typing import Any

from sqlmodel import func, select
from telethon import TelegramClient, utils
from telethon.tl.types import Message

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram.handlers import (
    fetch_message_content,
    looks_like_id,
    parse_target_channels,
    to_telegram_chat_id,
)
from app.services.telegram.processor import process_incoming_message

logger = get_logger("app.telegram")

# Mensajes que se traen la primera vez que se ve un canal, para no
# descargar todo su historial.
_SEED_LIMIT = 50


async def _resolve_channel(
    client: TelegramClient, target: str
) -> tuple[Any | None, int | None]:
    """Resuelve el canal configurado a (entity, channel_id marcado).

    `channel_id` se devuelve en el formato con marca de Telethon
    (`-100<id>` para canales), el mismo que usa `event.chat_id` en los
    handlers, para que la marca de agua en BD sea consistente.
    """
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


async def _last_processed_message_id(channel_id: int) -> int | None:
    """Marca de agua: mayor message_id ya guardado para ese canal."""
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(func.max(TelegramRawMessage.message_id)).where(
                TelegramRawMessage.channel_id == channel_id
            )
        )
        return result.first()


async def _catchup_channel(client: TelegramClient, target: str) -> None:
    entity, channel_id = await _resolve_channel(client, target)
    if entity is None or channel_id is None:
        logger.warning(
            "[TELEGRAM_CATCHUP] No se pudo resolver el canal %s; se omite.",
            target,
        )
        return

    channel_name = (
        getattr(entity, "title", None)
        or getattr(entity, "username", None)
        or str(target)
    )

    last_id = await _last_processed_message_id(channel_id)

    if last_id is None:
        # Canal sin historial: semilla acotada, de más antiguo a más nuevo.
        seed = await client.get_messages(entity, limit=_SEED_LIMIT)
        messages = [m for m in reversed(seed) if isinstance(m, Message)]
    else:
        messages = [
            m
            async for m in client.iter_messages(entity, min_id=last_id, reverse=True)
            if isinstance(m, Message)
        ]

    if not messages:
        logger.info(
            "[TELEGRAM_CATCHUP] Canal %s al día; no hay mensajes nuevos.",
            channel_name,
        )
        return

    logger.info(
        "[TELEGRAM_CATCHUP] Canal %s: %s mensaje(s) pendientes de procesar.",
        channel_name,
        len(messages),
    )

    for message in messages:
        if message.action is not None:
            # Mensajes de servicio (canal creado, foto cambiada...).
            continue
        text, media_path, extracted_text = await fetch_message_content(client, message)
        await process_incoming_message(
            channel=channel_name,
            channel_id=channel_id,
            message_id=message.id,
            text=text,
            media_path=media_path,
            extracted_text=extracted_text,
            message_date=message.date,
        )


async def run_catchup(client: TelegramClient) -> None:
    """Sincroniza los canales configurados tras un downtime del backend.

    Un fallo en un canal nunca debe impedir que el listener en vivo
    arranque, así que cada canal se procesa de forma aislada.
    """
    settings = get_settings()
    targets = parse_target_channels(settings.telegram_target_channel or "")
    if not targets:
        return

    for target in targets:
        try:
            await _catchup_channel(client, target)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[TELEGRAM_CATCHUP] Error haciendo catch-up del canal %s",
                target,
            )
