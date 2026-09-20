"""Catch-up de mensajes publicados mientras el backend estaba apagado.

`events.NewMessage` solo dispara con mensajes que llegan en vivo; los
publicados durante un downtime se perderían. Al arrancar, este módulo
recorre cada canal configurado y hace dos cosas:

1. **Marca de agua**: procesa los mensajes posteriores al último
   `message_id` guardado en `telegram_raw_messages`. Si el canal no
   tiene historial en BD, se siembra con los últimos `_SEED_LIMIT`
   mensajes.
2. **Detección de huecos**: la marca de agua sola no basta — si el
   backend estuvo caído un rato y luego llegaron mensajes en vivo, los
   ids intermedios quedan por debajo de la marca y nunca se revisarían.
   Por eso también se escanean los últimos `_GAP_SCAN_LIMIT` mensajes
   del canal y se procesa cualquiera que exista en Telegram pero no en
   la BD, o cuyo raw se guardó vacío (descarga de media fallida).

Ambas fases procesan de más nuevo a más viejo y se detienen al llegar a
mensajes más antiguos que `_MAX_MESSAGE_AGE`: así los picks de hoy se
importan primero y un canal poco activo no arrastra semanas de historia
en cada arranque.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlmodel import func, select
from telethon import TelegramClient
from telethon.tl.types import Message

from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.channel import Channel
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram.channels import resolve_channel_target
from app.services.telegram.handlers import fetch_message_content
from app.services.telegram.processor import process_incoming_message

logger = get_logger("app.telegram")

# Mensajes que se traen la primera vez que se ve un canal, para no
# descargar todo su historial.
_SEED_LIMIT = 50

# Cuántos mensajes recientes se escanean buscando huecos por debajo de
# la marca de agua en cada arranque.
_GAP_SCAN_LIMIT = 300

# Mensajes más antiguos que esto no se recuperan en ninguna fase. Sin el
# tope, un canal poco activo haría que el escaneo de 300 mensajes (o la
# marca de agua) se fuera semanas atrás drenando historia irrelevante.
_MAX_MESSAGE_AGE = timedelta(days=7)


async def _saved_message_ids(channel_id: int) -> set[int]:
    """Ids de mensajes ya guardados en `telegram_raw_messages`."""
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(TelegramRawMessage.message_id).where(
                TelegramRawMessage.channel_id == channel_id
            )
        )
        return set(result.all())


async def _empty_raw_ids(channel_id: int) -> set[int]:
    """Ids de raws guardados sin contenido (texto ni OCR ni media).

    Un raw vacío significa que la descarga falló al llegar el mensaje;
    si el mensaje sigue existiendo en Telegram hay que reintentarlo.
    """
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(TelegramRawMessage.message_id).where(
                TelegramRawMessage.channel_id == channel_id,
                TelegramRawMessage.text == "",
                TelegramRawMessage.extracted_text.is_(None),
                TelegramRawMessage.media_path.is_(None),
            )
        )
        return set(result.all())


async def _incomplete_raw_ids(channel_id: int) -> set[int]:
    """Ids de raws guardados a medias que conviene reintentar.

    - `processed=False`: la extracción del pick falló con excepción (p.
      ej. un 429 de OpenAI). El raw conserva el texto; basta reintentar.
    - foto sin OCR (`media_path` presente pero `extracted_text` NULL y
      sin texto propio): la descarga funcionó pero el OCR devolvió None
      (rate limit); sin reintento ese pick se pierde para siempre.
    """
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(TelegramRawMessage.message_id).where(
                TelegramRawMessage.channel_id == channel_id,
                or_(
                    TelegramRawMessage.processed == False,  # noqa: E712
                    and_(
                        TelegramRawMessage.text == "",
                        TelegramRawMessage.media_path.isnot(None),
                        TelegramRawMessage.extracted_text.is_(None),
                    ),
                ),
            )
        )
        return set(result.all())


async def _drop_incomplete_raw(channel_id: int, message_id: int) -> bool:
    """Elimina el raw incompleto antes de reprocesar el mensaje.

    Un raw vacío o fallido no aporta nada a la auditoría;
    `process_incoming_message` creará uno nuevo completo con el mismo
    `channel_id`/`message_id` y la fecha real del mensaje.

    Devuelve False si el raw tiene un ParsedPick vinculado (p. ej. creado
    a mano o fusionado como duplicado): en ese caso no se borra y el
    mensaje se omite.
    """
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(TelegramRawMessage).where(
                TelegramRawMessage.channel_id == channel_id,
                TelegramRawMessage.message_id == message_id,
            )
        )
        stale = result.first()
        if stale is None:
            return True

        linked = await session.exec(
            select(func.count())
            .select_from(ParsedPick)
            .where(ParsedPick.raw_message_id == stale.id)
        )
        if linked.one() > 0:
            return False

        await session.delete(stale)
        await session.commit()
        return True


async def _process_message(
    client: TelegramClient, message: Message, channel_name: str, channel_id: int
) -> None:
    """Descarga el contenido del mensaje y lo pasa al procesador."""
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


async def _catchup_channel(client: TelegramClient, target: str) -> None:
    entity, channel_id = await resolve_channel_target(client, target)
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

    saved_ids = await _saved_message_ids(channel_id)
    empty_ids = await _empty_raw_ids(channel_id)
    incomplete_ids = await _incomplete_raw_ids(channel_id)
    last_id = max(saved_ids) if saved_ids else None
    cutoff = datetime.now(timezone.utc) - _MAX_MESSAGE_AGE

    # --- 1) Huecos en el historial reciente -----------------------------
    # Mensajes que existen en Telegram pero no en la BD (downtime con
    # llegada posterior de mensajes en vivo), raws sin contenido y raws
    # incompletos (extracción fallida u OCR perdido por rate limit).
    # Solo aplica si el canal ya tiene historial; en uno nuevo la semilla
    # de la marca de agua basta y evita un barrido masivo de 300 mensajes.
    recent: list[Message] = []
    if last_id is not None:
        recent = [
            m
            async for m in client.iter_messages(entity, limit=_GAP_SCAN_LIMIT)
            if isinstance(m, Message)
        ]
    gap_found = 0
    for message in recent:  # de más nuevo a más viejo: hoy primero
        if message.date < cutoff:
            break
        if message.action is not None:
            continue
        missing = message.id not in saved_ids
        incomplete = message.id in empty_ids or message.id in incomplete_ids
        if not missing and not incomplete:
            continue
        if incomplete and not await _drop_incomplete_raw(channel_id, message.id):
            # Raw incompleto con pick vinculado: ya está representado en BD.
            continue
        try:
            await _process_message(client, message, channel_name, channel_id)
        except Exception:  # noqa: BLE001
            # Un mensaje problemático no debe abortar el canal: se omite y
            # quedará pendiente para el próximo arranque.
            logger.exception(
                "[TELEGRAM_CATCHUP] Error procesando mensaje %s del canal %s.",
                message.id,
                channel_name,
            )
            continue
        saved_ids.add(message.id)
        gap_found += 1

    if gap_found:
        logger.info(
            "[TELEGRAM_CATCHUP] Canal %s: %s mensaje(s) recuperados de huecos.",
            channel_name,
            gap_found,
        )

    # --- 2) Marca de agua ------------------------------------------------
    # Mensajes posteriores al último id guardado (puede haber más allá de
    # la ventana del escaneo de huecos).
    if last_id is None:
        # Canal sin historial: semilla acotada.
        seed = await client.get_messages(entity, limit=_SEED_LIMIT)
        messages = [m for m in seed if isinstance(m, Message)]
    else:
        messages = [
            m
            async for m in client.iter_messages(entity, min_id=last_id)
            if isinstance(m, Message)
        ]

    new_found = 0
    for message in messages:  # de más nuevo a más viejo
        if message.date < cutoff:
            break
        if message.action is not None or message.id in saved_ids:
            continue
        try:
            await _process_message(client, message, channel_name, channel_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[TELEGRAM_CATCHUP] Error procesando mensaje %s del canal %s.",
                message.id,
                channel_name,
            )
            continue
        new_found += 1

    if not gap_found and not new_found:
        logger.info(
            "[TELEGRAM_CATCHUP] Canal %s al día; no hay mensajes nuevos.",
            channel_name,
        )
    elif new_found:
        logger.info(
            "[TELEGRAM_CATCHUP] Canal %s: %s mensaje(s) nuevos procesados.",
            channel_name,
            new_found,
        )


async def run_catchup_for_target(client: TelegramClient, target: str) -> None:
    """Catch-up de un solo canal (p. ej. al activarlo desde la app)."""
    try:
        await _catchup_channel(client, target)
    except Exception:  # noqa: BLE001
        logger.exception(
            "[TELEGRAM_CATCHUP] Error haciendo catch-up del canal %s",
            target,
        )


async def run_catchup(client: TelegramClient) -> None:
    """Sincroniza los canales activos tras un downtime del backend.

    La fuente de verdad es la tabla `channels`; un fallo en un canal
    nunca debe impedir que el listener en vivo arranque, así que cada
    canal se procesa de forma aislada.
    """
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(Channel).where(
                Channel.activo,
                Channel.eliminado == False,  # noqa: E712
            )
        )
        channels = list(result.all())
    if not channels:
        return

    for channel in channels:
        target = str(channel.channel_id) if channel.channel_id else channel.target
        try:
            await _catchup_channel(client, target)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[TELEGRAM_CATCHUP] Error haciendo catch-up del canal %s",
                target,
            )
