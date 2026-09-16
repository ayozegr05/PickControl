"""Registro de handlers de eventos de Telegram (Telethon).

Escucha los mensajes nuevos de los canales configurados en
`TELEGRAM_TARGET_CHANNEL` (lista separada por comas) y los reenvía al
procesador de `processor.py` para guardarlos en base de datos.
"""

import os

from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.telegram.ocr import extract_text_from_image
from app.services.telegram.processor import process_incoming_message

logger = get_logger("app.telegram")


def parse_target_channels(targets: str) -> list[str]:
    """Convierte la variable de entorno en una lista de canales limpios."""
    return [t.strip() for t in targets.split(",") if t.strip()]


async def fetch_message_content(
    client: TelegramClient, message
) -> tuple[str, str | None, str | None]:
    """Extrae el contenido procesable de un mensaje de Telegram.

    Devuelve (texto, media_path, extracted_text). Si el mensaje lleva
    foto, la descarga al directorio configurado; si además no tiene
    texto propio, le aplica OCR con OpenAI.
    """
    settings = get_settings()
    text = message.text or ""

    media_path: str | None = None
    extracted_text: str | None = None

    if message.media:
        if isinstance(message.media, MessageMediaPhoto):
            os.makedirs(settings.telegram_media_path, exist_ok=True)
            filename = f"{message.chat_id}_{message.id}.jpg"
            media_path = os.path.join(settings.telegram_media_path, filename)
            try:
                await client.download_media(message.media, file=media_path)
                logger.info("Imagen descargada: %s", media_path)
                if not text:
                    extracted_text = await extract_text_from_image(
                        media_path, settings.openai_api_key
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error("Error descargando imagen: %s", exc)
                media_path = None
        else:
            logger.info("Media no soportada para OCR: %s", type(message.media).__name__)

    return text, media_path, extracted_text


def _make_new_message_handler(target_label: str):
    """Fabrica un handler con el nombre del canal cerrado en el closure."""

    async def _on_new_message(event: events.NewMessage.Event) -> None:
        chat_entity = await event.get_chat()
        chat_name = (
            getattr(chat_entity, "title", None)
            or getattr(chat_entity, "username", None)
            or str(target_label)
        )
        message = event.message

        text, media_path, extracted_text = await fetch_message_content(
            event.client, message
        )

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
            message_date=message.date,
        )

    return _on_new_message


def register_handlers(client: TelegramClient) -> None:
    """Registra un listener de mensajes nuevos por cada canal configurado.

    Si `TELEGRAM_TARGET_CHANNEL` no está configurado, no registra nada
    para no lanzar el cliente a escuchar "todo".
    """
    settings = get_settings()
    targets = parse_target_channels(settings.telegram_target_channel or "")

    if not targets:
        logger.warning(
            "[TELEGRAM_LISTENER] TELEGRAM_TARGET_CHANNEL no está configurado; "
            "no se registrará ningún listener."
        )
        return

    for target in targets:
        # Telethon acepta usernames ("mi_canal") o ids numéricos.
        # Normalizamos posibles formatos: 123, -123, -100123...
        chat = to_telegram_chat_id(target) if looks_like_id(target) else target

        client.on(events.NewMessage(chats=[chat]))(_make_new_message_handler(target))
        logger.info("[TELEGRAM_LISTENER] Escuchando canal objetivo: %s", target)


def looks_like_id(value: str) -> bool:
    return value.lstrip("-").isdigit()


def to_telegram_chat_id(value: str) -> int:
    """Convierte un string de id de Telegram en el entero que espera Telethon.

    Telegram representa los canales como `-100<id_canal>`. Aceptamos:
    - `1914772235` (id positivo)
    - `-1914772235` (con signo negativo, lo convertimos a positivo)
    - `-1001914772235` (formato completo, lo dejamos tal cual)
    """
    cleaned = value.lstrip("-")
    if cleaned.startswith("100") and len(cleaned) >= 13:
        # Formato completo -1001914772235 -> lo dejamos como entero negativo.
        return int(value)
    # -1914772235 o 1914772235 -> usamos el id positivo del canal.
    return int(cleaned)
