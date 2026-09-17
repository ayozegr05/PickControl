# ruff: noqa: E402
"""Recupera de Telegram mensajes de un canal que quedaron mal guardados.

Cubre dos casos que ni el listener ni el catch-up reparan:

- Mensajes guardados como TelegramRawMessage pero sin contenido
  (descarga de media fallida, backend reiniciándose, etc.): se vuelve a
  descargar el contenido, se actualiza el raw y queda processed=False
  para que `reprocess_raw.py` extraiga el pick.
- Mensajes que nunca llegaron a la BD (huecos en message_id por debajo
  de la marca de agua del catch-up): se procesan como nuevos.

Uso:
    .venv\\Scripts\\python.exe scripts\\refetch_messages.py <canal> <id_inicio> <id_fin>

    <canal> puede ser el id numérico (-100...) o el username.

El backend debe estar parado: la sesión de Telethon es un SQLite que no
admite dos clientes a la vez.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from telethon.tl.types import Message

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401
from app.models.parsed_pick import ParsedPick  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User  # noqa: F401
from app.services.telegram.catchup import _resolve_channel
from app.services.telegram.client import get_telegram_client, reset_telegram_client
from app.services.telegram.handlers import fetch_message_content
from app.services.telegram.processor import process_incoming_message


async def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__)
        return

    target, id_start, id_end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

    settings = get_settings()
    client = get_telegram_client()
    await client.start(phone=settings.telegram_phone)

    try:
        entity, channel_id = await _resolve_channel(client, target)
        if entity is None or channel_id is None:
            print(f"No se pudo resolver el canal {target!r}.")
            return

        channel_name = (
            getattr(entity, "title", None)
            or getattr(entity, "username", None)
            or str(target)
        )

        wanted = list(range(id_start, id_end + 1))
        messages = await client.get_messages(entity, ids=wanted)

        async with AsyncSessionLocal() as session:
            for message in messages:
                if message is None:
                    continue  # id no existe en el canal (borrado)
                if not isinstance(message, Message) or message.action is not None:
                    continue

                result = await session.exec(
                    select(TelegramRawMessage).where(
                        TelegramRawMessage.channel_id == channel_id,
                        TelegramRawMessage.message_id == message.id,
                    )
                )
                raw = result.scalars().first()

                if raw is not None and (raw.text or raw.extracted_text):
                    print(f"msg {message.id}: ya tiene contenido, salto.")
                    continue

                text, media_path, extracted_text = await fetch_message_content(
                    client, message
                )

                if raw is not None:
                    raw.text = text or ""
                    raw.media_path = media_path
                    raw.extracted_text = extracted_text
                    raw.processed = False
                    session.add(raw)
                    print(
                        f"msg {message.id}: raw {raw.id} actualizado "
                        f"(texto={len(text)} chars, ocr={len(extracted_text or '')} chars)."
                    )
                else:
                    await process_incoming_message(
                        channel=channel_name,
                        channel_id=channel_id,
                        message_id=message.id,
                        text=text,
                        media_path=media_path,
                        extracted_text=extracted_text,
                        message_date=message.date,
                        session=session,
                    )
                    print(f"msg {message.id}: procesado como nuevo.")

            await session.commit()
    finally:
        await client.disconnect()
        reset_telegram_client()

    print(
        "Listo. Ejecuta reprocess_raw.py para extraer picks de los raws actualizados."
    )


if __name__ == "__main__":
    asyncio.run(main())
