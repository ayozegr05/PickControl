"""Registro de handlers de eventos de Telegram (Telethon).

Por ahora solo escucha los mensajes nuevos del canal objetivo
configurado (`TELEGRAM_TARGET_CHANNEL`) y los reenvía al stub de
procesamiento de `processor.py`. La lógica de interpretación (LLM) se
añadirá en una fase posterior.
"""
import logging

from telethon import TelegramClient, events

from app.core.config import get_settings
from app.services.telegram.processor import process_incoming_message

logger = logging.getLogger("app.telegram")


def register_handlers(client: TelegramClient) -> None:
    """Registra el listener de mensajes nuevos sobre `client`.

    Si `TELEGRAM_TARGET_CHANNEL` no está configurado, no registra nada
    (y lo avisa por log) para no lanzar el cliente a escuchar "todo".
    """
    settings = get_settings()
    target_channel = settings.telegram_target_channel

    if not target_channel:
        logger.warning(
            "[TELEGRAM_LISTENER] TELEGRAM_TARGET_CHANNEL no está configurado; "
            "no se registrará ningún listener."
        )
        return

    # Telethon acepta tanto usernames ("mi_canal") como ids numéricos
    # ("-1001125596067"); si parece un entero, lo convertimos.
    chat = int(target_channel) if _looks_like_id(target_channel) else target_channel

    @client.on(events.NewMessage(chats=[chat]))
    async def _on_new_message(event: events.NewMessage.Event) -> None:
        chat_entity = await event.get_chat()
        chat_name = (
            getattr(chat_entity, "title", None)
            or getattr(chat_entity, "username", None)
            or str(target_channel)
        )
        text = event.message.text or ""

        logger.info("[TELEGRAM_LISTENER] Mensaje recibido de @%s: %s", chat_name, text)

        await process_incoming_message(
            channel=chat_name,
            channel_id=event.chat_id,
            message_id=event.message.id,
            text=text,
        )

    logger.info("[TELEGRAM_LISTENER] Escuchando canal objetivo: %s", target_channel)


def _looks_like_id(value: str) -> bool:
    return value.lstrip("-").isdigit()
