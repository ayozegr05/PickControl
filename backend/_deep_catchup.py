"""Catch-up profundo puntual: recupera el hueco 23-oct → 26-oct 2026.

El catch-up rutinario corta a `_MAX_MESSAGE_AGE` (7d); este script
barre la ventana exacta del apagón de ingesta (sesión Telethon rota
23-oct 09:12 → restaurada 2-nov). Se ejecuta DENTRO del contenedor
con una COPIA del fichero de sesión para no pisar el sqlite que el
listener tiene abierto:

    docker cp _deep_catchup.py controlpick-backend:/tmp/
    docker exec controlpick-backend python /tmp/_deep_catchup.py

Dedup: solo procesa ids que no están en telegram_raw_messages.
"""

import asyncio
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from sqlmodel import select
from telethon import TelegramClient
from telethon.tl.types import Message

from app.core.config import get_settings
from app.db.postgres import AsyncSessionLocal
from app.models.channel import Channel
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram.catchup import _process_message
from app.services.telegram.channels import resolve_channel_target

DEEP_FROM = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
DEEP_TO = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)


async def _saved_ids(channel_id: int) -> set[int]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.exec(
                select(TelegramRawMessage.message_id).where(
                    TelegramRawMessage.channel_id == channel_id
                )
            )
        ).all()
    return set(rows)


async def main() -> None:
    settings = get_settings()
    shutil.copy(
        f"/app/{settings.telegram_session_name}.session",
        "/tmp/deepcatch.session",
    )
    client = TelegramClient(
        "/tmp/deepcatch", int(settings.telegram_api_id), settings.telegram_api_hash
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("ERROR: sesión copia no autorizada")
        return

    async with AsyncSessionLocal() as session:
        channels = list(
            (
                await session.exec(
                    select(Channel).where(
                        Channel.activo, Channel.eliminado == False  # noqa: E712
                    )
                )
            ).all()
        )

    for channel in channels:
        target = str(channel.channel_id) if channel.channel_id else channel.target
        entity, channel_id = await resolve_channel_target(client, target)
        if entity is None or channel_id is None:
            print(f"canal {target}: no se pudo resolver")
            continue
        name = getattr(entity, "title", None) or str(target)
        saved = await _saved_ids(channel_id)
        recovered = 0
        async for m in client.iter_messages(entity, offset_date=DEEP_TO):
            if not isinstance(m, Message):
                continue
            if m.date < DEEP_FROM:
                break
            if m.action is not None or m.id in saved:
                continue
            try:
                await _process_message(client, m, name, channel_id)
                saved.add(m.id)
                recovered += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  msg {m.id} de {name}: error {exc}")
        print(f"canal {name}: {recovered} mensajes recuperados del hueco")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
