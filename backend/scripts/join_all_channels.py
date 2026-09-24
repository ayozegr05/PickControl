# ruff: noqa: E402
"""Une la cuenta Telethon a todos los canales activos de la tabla `channels`.

Pensado para migrar a la cuenta dedicada: la tabla `channels` es la fuente
de verdad, así que basta autenticar la sesión nueva y ejecutar este script
para que la cuenta se una a cada canal monitorizado.

Los joins masivos son de lo más vigilado por Telegram, así que el script
duerme ~JOIN_DELAY s entre uniones y, ante FloodWaitError, duerme lo que
pida Telegram + margen antes de reintentar.

Uso:
    .\\.venv\\Scripts\\python.exe scripts\\join_all_channels.py [--dry-run]

- Públicos (@user / t.me/user): JoinChannelRequest.
- Invitación (t.me/+hash, t.me/joinchat/hash): ImportChatInviteRequest.
- Ids numéricos: solo resolubles si ya están en los diálogos de la cuenta
  (Telegram no permite unirse por id sin hash); si no, se reportan.
"""

import argparse
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import logging

from dotenv import load_dotenv
from sqlmodel import select
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from app.db.postgres import AsyncSessionLocal
from app.models.channel import Channel
from app.services.telegram.channels import resolve_channel_target
from app.services.telegram.client import get_telegram_client

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("scripts.join_all_channels")

JOIN_DELAY = float(os.getenv("JOIN_CHANNELS_DELAY", "20"))
FLOOD_MARGIN = 10
MAX_FLOOD_RETRIES = 3

_INVITE_RE = re.compile(r"t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)")


def _invite_hash(target: str) -> str | None:
    """Extrae el hash si el target es un enlace de invitación privada."""
    m = _INVITE_RE.search(target)
    return m.group(1) if m else None


async def _join_with_flood_wait(client, request, label: str) -> bool:
    """Ejecuta un request de join respetando FloodWait hasta 3 veces."""
    for attempt in range(1, MAX_FLOOD_RETRIES + 1):
        try:
            await client(request)
            return True
        except UserAlreadyParticipantError:
            logger.info("  %s: ya era participante", label)
            return True
        except FloodWaitError as e:
            wait = e.seconds + FLOOD_MARGIN
            logger.warning(
                "  %s: FloodWait %ds (intento %d/%d) — durmiendo %ds",
                label,
                e.seconds,
                attempt,
                MAX_FLOOD_RETRIES,
                wait,
            )
            if attempt == MAX_FLOOD_RETRIES:
                return False
            await asyncio.sleep(wait)
        except Exception as e:  # noqa: BLE001
            logger.error("  %s: fallo al unirse: %s: %s", label, type(e).__name__, e)
            return False
    return False


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo resuelve y reporta; no ejecuta ningún join.",
    )
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Channel).where(
                Channel.activo,
                Channel.eliminado == False,  # noqa: E712
            )
        )
        channels = list(result.all())

    if not channels:
        logger.info("No hay canales activos en la tabla.")
        return
    logger.info("%d canales activos a procesar", len(channels))

    client = get_telegram_client()
    await client.connect()
    if not await client.is_user_authorized():
        logger.error("Sesión no autorizada — completa el login primero.")
        return
    me = await client.get_me()
    logger.info("Sesión: %s (@%s)", me.first_name, me.username)

    ok, already, failed = 0, 0, 0
    for i, ch in enumerate(channels, 1):
        label = ch.name or ch.target
        invite = _invite_hash(ch.target)
        entity = None
        marked = ch.channel_id

        if invite:
            if not args.dry_run:
                joined = await _join_with_flood_wait(
                    client, ImportChatInviteRequest(invite), label
                )
                if not joined:
                    failed += 1
                    continue
                try:
                    entity, marked = await resolve_channel_target(client, ch.target)
                except Exception:
                    logger.warning("  %s: unido pero no se pudo resolver", label)
            else:
                logger.info("[%d/%d] %s → invitación privada", i, len(channels), label)
                ok += 1
                continue
        else:
            try:
                entity, marked = await resolve_channel_target(client, ch.target)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[%d/%d] %s: no resoluble (%s: %s)",
                    i,
                    len(channels),
                    label,
                    type(e).__name__,
                    e,
                )
                failed += 1
                continue
            if entity is None:
                logger.warning(
                    "[%d/%d] %s: id numérico sin diálogo — necesita invite link",
                    i,
                    len(channels),
                    label,
                )
                failed += 1
                continue

            if getattr(entity, "left", False) is False and marked is not None:
                already += 1
                logger.info("[%d/%d] %s: ya unido", i, len(channels), label)
            elif not args.dry_run:
                if not await _join_with_flood_wait(
                    client, JoinChannelRequest(entity), label
                ):
                    failed += 1
                    continue
                ok += 1
                logger.info("[%d/%d] %s: unido", i, len(channels), label)
            else:
                logger.info("[%d/%d] %s: pendiente de join", i, len(channels), label)
                ok += 1
                continue

        if entity is not None and not args.dry_run:
            async with AsyncSessionLocal() as session:
                db_ch = await session.get(Channel, ch.id)
                if db_ch is not None:
                    db_ch.channel_id = marked
                    db_ch.name = getattr(entity, "title", None) or db_ch.target
                    db_ch.username = getattr(entity, "username", None)
                    session.add(db_ch)
                    await session.commit()

        if i < len(channels) and not args.dry_run:
            await asyncio.sleep(JOIN_DELAY)

    logger.info("Fin: %d unidos/ok, %d ya estaban, %d fallidos", ok, already, failed)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
