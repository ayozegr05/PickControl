"""Registro de handlers de eventos de Telegram (Telethon).

Se registran handlers GLOBALES (NewMessage + Album) que filtran cada
evento por el set de canales activos de la tabla `channels`
(`channels.py`). Así añadir/quitar canales desde la app no requiere
re-registrar handlers ni reiniciar el listener.
"""

import os

from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.telegram.channels import active_channel_ids, channel_name_for
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


def _make_new_message_handler():
    """Handler global de mensajes nuevos, filtrado por canales activos."""

    async def _on_new_message(event: events.NewMessage.Event) -> None:
        chat_id = event.chat_id
        if chat_id is None or chat_id not in active_channel_ids():
            return

        message = event.message
        # Los miembros de un álbum también disparan NewMessage; los
        # procesa el handler de Album, que agrupa y comparte la caption.
        if getattr(message, "grouped_id", None) is not None:
            return

        chat_name = channel_name_for(chat_id)

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
            channel_id=chat_id,
            message_id=message.id,
            text=text,
            media_path=media_path,
            extracted_text=extracted_text,
            message_date=message.date,
        )

    return _on_new_message


def _make_album_handler():
    """Handler para álbumes (varias fotos enviadas como un solo mensaje).

    Un "pack" de picks suele llegar como álbum: N boletos con un mismo
    `grouped_id` y la caption ("STAKE 4, CUOTA...") solo en uno de ellos.
    Cada foto es un pick distinto, así que cada una genera su propio raw
    y ParsedPick, pero hereda la caption compartida si no lleva texto.
    """

    async def _on_album(event: events.Album.Event) -> None:
        chat_id = event.chat_id
        if chat_id is None or chat_id not in active_channel_ids():
            return

        chat_name = channel_name_for(chat_id)

        messages = list(event.messages)
        # Caption del álbum: el texto de cualquiera de sus mensajes (Telegram
        # permite una caption por mensaje; en la práctica solo uno la lleva).
        shared_caption = next(
            (m.text for m in messages if getattr(m, "text", None)), ""
        )

        logger.info(
            "[TELEGRAM_LISTENER] Álbum recibido de @%s: %s fotos, caption=%s",
            chat_name,
            len(messages),
            (shared_caption or "")[:80],
        )

        for message in messages:
            text, media_path, extracted_text = await fetch_message_content(
                event.client, message
            )
            await process_incoming_message(
                channel=chat_name,
                channel_id=chat_id,
                message_id=message.id,
                text=text or shared_caption,
                media_path=media_path,
                extracted_text=extracted_text,
                message_date=message.date,
            )

    return _on_album


def register_handlers(client: TelegramClient) -> None:
    """Registra los handlers globales de mensajes y álbumes.

    No filtran por canal al registrarse: cada evento se comprueba contra
    `active_channel_ids()` (tabla `channels`), de modo que el CRUD de
    canales desde la app surte efecto sin reiniciar el listener.
    """
    client.on(events.NewMessage())(_make_new_message_handler())
    client.on(events.Album())(_make_album_handler())
    logger.info(
        "[TELEGRAM_LISTENER] Handlers globales registrados "
        "(filtro dinámico por tabla channels)"
    )


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
