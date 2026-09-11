"""Registro de handlers de eventos de Telegram (Telethon).

Escucha los mensajes nuevos de los canales configurados en
`TELEGRAM_TARGET_CHANNEL` (lista separada por comas) y los reenvía al
procesador de `processor.py` para guardarlos en base de datos.
"""

from telethon import TelegramClient, events

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.telegram.processor import process_incoming_message

logger = get_logger("app.telegram")


def _parse_target_channels(targets: str) -> list[str]:
    """Convierte la variable de entorno en una lista de canales limpios."""
    return [t.strip() for t in targets.split(",") if t.strip()]


def _make_new_message_handler(target_label: str):
    """Fabrica un handler con el nombre del canal cerrado en el closure."""

    async def _on_new_message(event: events.NewMessage.Event) -> None:
        chat_entity = await event.get_chat()
        chat_name = (
            getattr(chat_entity, "title", None)
            or getattr(chat_entity, "username", None)
            or str(target_label)
        )
        text = event.message.text or ""

        logger.info(
            "[TELEGRAM_LISTENER] Mensaje recibido de @%s: %s",
            chat_name,
            text,
        )

        await process_incoming_message(
            channel=chat_name,
            channel_id=event.chat_id,
            message_id=event.message.id,
            text=text,
        )

    return _on_new_message


def register_handlers(client: TelegramClient) -> None:
    """Registra un listener de mensajes nuevos por cada canal configurado.

    Si `TELEGRAM_TARGET_CHANNEL` no está configurado, no registra nada
    para no lanzar el cliente a escuchar "todo".
    """
    settings = get_settings()
    targets = _parse_target_channels(settings.telegram_target_channel or "")

    if not targets:
        logger.warning(
            "[TELEGRAM_LISTENER] TELEGRAM_TARGET_CHANNEL no está configurado; "
            "no se registrará ningún listener."
        )
        return

    for target in targets:
        # Telethon acepta tanto usernames ("mi_canal") como ids numéricos
        # ("-1001125596067"); si parece un entero, lo convertimos.
        chat = int(target) if _looks_like_id(target) else target

        client.on(events.NewMessage(chats=[chat]))(_make_new_message_handler(target))
        logger.info("[TELEGRAM_LISTENER] Escuchando canal objetivo: %s", target)


def _looks_like_id(value: str) -> bool:
    return value.lstrip("-").isdigit()
