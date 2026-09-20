"""Envío de notificaciones push vía Expo Push API.

Dos disparadores, ambos fire-and-forget seguros (una notificación
NUNCA debe tumbar la ingesta de Telegram ni la verificación):

- `notify_new_pick`: un `ParsedPick` nuevo con `es_apuesta` (las
  combinadas notifican solo el padre — las patas no pasan por aquí).
- `notify_settled_picks`: picks que acaban de liquidarse — solo se
  avisa al usuario que marcó "Yo también la jugué" (existe una fila
  `picks.parsed_pick_id` enlazada), no por cada pick del tipster.

Los recibos de Expo vienen alineados con los mensajes enviados; un
`DeviceNotRegistered` desactiva el token (`enabled=False`) para no
reintentar envíos imposibles.
"""

from __future__ import annotations

from typing import Optional

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.device_token import DeviceToken
from app.models.parsed_pick import ParsedPick
from app.models.pick import Pick

logger = get_logger("app.notifications.push")

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
# Expo recomienda lotes de <=100 mensajes por petición.
_CHUNK = 100


def _enabled(settings) -> bool:
    """Push activado y configurado (sin kill-switch accidental)."""
    return bool(settings.push_notifications_enabled)


async def _send_chunk(messages: list[dict], access_token: Optional[str]) -> list[dict]:
    """Un lote a la Expo Push API; devuelve los recibos por mensaje."""
    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(_EXPO_PUSH_URL, json=messages, headers=headers)
        response.raise_for_status()
        data = response.json()
    receipts = data.get("data") or []
    return receipts if isinstance(receipts, list) else []


async def _send(session: AsyncSession, rows: list[DeviceToken], build_message) -> int:
    """Envía `build_message(token)` a cada fila; desactiva tokens muertos.

    Devuelve cuántos mensajes se intentaron. Los recibos se emparejan
    por posición con el envío (la API responde en el mismo orden).
    """
    if not rows:
        return 0
    settings = get_settings()
    sent = 0
    for start in range(0, len(rows), _CHUNK):
        chunk = rows[start : start + _CHUNK]
        messages = [build_message(row.token) for row in chunk]
        try:
            receipts = await _send_chunk(messages, settings.expo_access_token)
        except httpx.HTTPError as exc:
            logger.warning("[PUSH] Error enviando lote a Expo: %s", exc)
            continue
        sent += len(messages)
        for row, receipt in zip(chunk, receipts):
            if (
                isinstance(receipt, dict)
                and receipt.get("status") == "error"
                and (receipt.get("details") or {}).get("error") == "DeviceNotRegistered"
            ):
                row.enabled = False
                row.updated_at = utc_now()
                session.add(row)
    # Los tokens muertos quedan desactivados en la misma transacción de
    # la notificación (sesión propia del servicio — sin commit se
    # perdería el disable y se reintentaría el envío imposible).
    await session.commit()
    return sent


async def _tokens_of(session: AsyncSession, user_ids: set[int]) -> list[DeviceToken]:
    return list(
        (
            await session.exec(
                select(DeviceToken)
                .where(DeviceToken.enabled == True)  # noqa: E712
                .where(DeviceToken.user_id.in_(user_ids))
            )
        ).all()
    )


def _pick_summary(pick: ParsedPick) -> str:
    """Texto corto del pick para el cuerpo de la notificación."""
    cuota = f" @ {pick.cuota:g}" if pick.cuota else ""
    return f"{pick.seleccion or pick.apuesta or 'Nuevo pick'}{cuota}"


async def notify_new_pick(pick_id: int) -> None:
    """Push "pick nuevo" a TODOS los dispositivos activos.

    Las combinadas llegan como el padre (las patas no disparan), y los
    duplicados/enriquecidos no pasan por aquí (ya notificaron al crearse).
    """
    try:
        if not _enabled(get_settings()):
            return
        async with AsyncSessionLocal() as session:
            pick = await session.get(ParsedPick, pick_id)
            if pick is None or not pick.es_apuesta:
                return
            rows = list(
                (
                    await session.exec(
                        select(DeviceToken).where(
                            DeviceToken.enabled == True  # noqa: E712
                        )
                    )
                ).all()
            )
            if pick.es_combinada:
                patas = (
                    await session.exec(
                        select(ParsedPick).where(ParsedPick.combinada_id == pick.id)
                    )
                ).all()
                summary = (
                    f"Combinada x{len(patas) or '?'} @ {pick.cuota:g}"
                    if pick.cuota
                    else "Combinada nueva"
                )
            else:
                summary = _pick_summary(pick)

            def message(token: str) -> dict:
                return {
                    "to": token,
                    "title": f"Nuevo pick — {pick.informante or 'tipster'}",
                    "body": summary,
                    "data": {"type": "new_pick", "pick_id": pick.id},
                }

            sent = await _send(session, rows, message)
            if sent:
                logger.info(
                    "[PUSH] Pick nuevo id=%s notificado a %d dispositivos.",
                    pick_id,
                    sent,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PUSH] notify_new_pick(%s) falló: %s", pick_id, exc)


async def notify_settled_picks(pick_ids: list[int]) -> None:
    """Push "apuesta liquidada" solo a usuarios que la jugaron.

    `picks.parsed_pick_id` enlaza la apuesta del usuario con el pick del
    tipster — sin esa fila no hay notificación (el tipster liquida
    cientos de picks; solo importan las tuyas).
    """
    try:
        if not pick_ids or not _enabled(get_settings()):
            return
        async with AsyncSessionLocal() as session:
            for pick_id in pick_ids:
                pick = await session.get(ParsedPick, pick_id)
                if pick is None:
                    continue
                user_bets = list(
                    (
                        await session.exec(
                            select(Pick).where(Pick.parsed_pick_id == pick_id)
                        )
                    ).all()
                )
                user_ids = {bet.usuario_id for bet in user_bets}
                if not user_ids:
                    continue
                rows = await _tokens_of(session, user_ids)
                estado = (
                    "anulada"
                    if pick.anulada
                    else ("acertada" if pick.acierto else "fallada")
                )

                def message(token: str) -> dict:
                    return {
                        "to": token,
                        "title": f"Apuesta {estado}",
                        "body": _pick_summary(pick),
                        "data": {
                            "type": "settled",
                            "pick_id": pick.id,
                            "acierto": pick.acierto,
                            "anulada": pick.anulada,
                        },
                    }

                sent = await _send(session, rows, message)
                if sent:
                    logger.info(
                        "[PUSH] Pick id=%s liquidado (%s): notificado a %d.",
                        pick_id,
                        estado,
                        sent,
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PUSH] notify_settled_picks(%s) falló: %s", pick_ids, exc)
