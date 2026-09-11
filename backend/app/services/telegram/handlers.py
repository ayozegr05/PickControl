"""Registro de handlers de eventos de Telegram (Telethon).

Escucha los mensajes nuevos de los canales configurados en
`TELEGRAM_TARGET_CHANNEL` (lista separada por comas) y los reenvía al
procesador de `processor.py` para guardarlos en base de datos.
"""

import os

from telethon import TelegramClient, events

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.telegram.ocr import extract_text_from_image
from app.services.telegram.processor import process_incoming_message

logger = get_logger("app.telegram")


def _parse_target_channels(targets: str) -> list[str]:
    """Convierte la variable de entorno en una lista de canales limpios."""
    return [t.strip() for t in targets.split(",") if t.strip()]


def _make_new_message_handler(target_label: str):
    """Fabrica un handler con el nombre del canal cerrado en el closure."""

    async def _on_new_message(event: events.NewMessage.Event) -> None:
        settings = get_settings()
        chat_entity = await event.get_chat()
        chat_name = (
            getattr(chat_entity, "title", None)
            or getattr(chat_entity, "username", None)
            or str(target_label)
        )
        message = event.message
        text = message.text or ""

        media_path: str | None = None
        extracted_text: str | None = None

        if message.media:
            os.makedirs(settings.telegram_media_path, exist_ok=True)
            filename = f"{message.chat_id}_{message.id}.jpg"
            media_path = os.path.join(settings.telegram_media_path, filename)
            try:
                await event.client.download_media(message.media, file=media_path)
                logger.info("Imagen descargada: %s", media_path)
                if not text:
                    extracted_text = await extract_text_from_image(
                        media_path, settings.openai_api_key
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error("Error descargando imagen: %s", exc)
                media_path = None

        logger.info(
            "[TELEGRAM_LISTENER] Mensaje recibido de @%s: %s",
            chat_name,
            text or extracted_text or "(imagen sin texto)",
        )

        await process_incoming_message(
            channel=chat_name,
            channel_id=event.chat_id,
            message_id=message.id,
            text=text,
            media_path=media_path,
            extracted_text=extracted_text,
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
