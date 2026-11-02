"""Alertas críticas por email (SMTP estándar, stdlib — sin deps nuevas).

Canal de emergencia para avisos de operación que deben llegar aunque
el admin no tenga la app instalada (los push Expo mueren con el token
del dispositivo). Se usa SOLO para alertas raras y graves — hoy la
ingesta caída; el flujo cotidiano de picks sigue yendo por push.

Config en `.env`: ALERT_EMAIL_TO + SMTP_HOST/PORT/USER/PASS[/FROM].
Sin `alert_email_to` el canal no existe y `send_alert` es un no-op —
así dev/tests no intentan abrir sockets.

`smtplib` es síncrono: el envío corre en `asyncio.to_thread` para no
bloquear el loop del backend (es un aviso cada horas, no alta
frecuencia). El fallo NUNCA propaga: una alerta rota no puede tumbar
al vigilante que la dispara.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("app.notifications.email")


def _send_sync(subject: str, body: str) -> None:
    settings = get_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = settings.alert_email_to
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_pass or "")
        s.send_message(msg)


async def send_alert(subject: str, body: str) -> bool:
    """Envía una alerta crítica por email. Best-effort total: devuelve
    False en cualquier fallo (sin config, SMTP caído, auth mala) y solo
    lo registra — el caller nunca debería preocuparse."""
    settings = get_settings()
    if not settings.alert_email_to or not settings.smtp_host:
        return False
    try:
        await asyncio.to_thread(_send_sync, subject, body)
        logger.info("[EMAIL] Alerta enviada a %s", settings.alert_email_to)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EMAIL] Fallo enviando alerta: %s", exc)
        return False
