"""Diagnóstico aislado del login de Telethon (sin FastAPI/uvicorn de por medio).

Uso (una sola vez, para depurar por qué no llega el código de login):

    .\\.venv\\Scripts\\python.exe scripts\\telegram_login_diagnostic.py

Se conecta, pide el código UNA sola vez y muestra qué tipo de envío dice
Telegram que ha usado (app, SMS, llamada...) o el error exacto (p. ej.
`FloodWaitError` si Telegram está bloqueando temporalmente el envío por
haber pedido demasiados códigos seguidos).

Este script es solo una herramienta de diagnóstico puntual; no forma
parte del arranque normal de la app (ver `app/services/telegram/`).
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
phone = os.getenv("TELEGRAM_PHONE")
session_name = os.getenv("TELEGRAM_SESSION_NAME", "controlpick_telegram")


async def main() -> None:
    if not api_id or not api_hash or not phone:
        print("Faltan TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE en .env")
        sys.exit(1)

    client = TelegramClient(session_name, int(api_id), api_hash)
    await client.connect()

    if await client.is_user_authorized():
        print("La sesión YA está autorizada (no hace falta pedir código).")
        await client.disconnect()
        return

    force_sms = "--force-sms" in sys.argv

    try:
        sent = await client.send_code_request(phone, force_sms=force_sms)
        print("Código solicitado correctamente. Detalles:")
        print(f"  Tipo de envío: {type(sent.type).__name__}")
        print(f"  phone_code_hash: {sent.phone_code_hash}")
        print(f"  timeout: {sent.timeout}s")
        print("\nRevisa el chat 'Telegram' en tu app/Telegram Web (o el SMS) "
              "y entra el código en los próximos segundos si quieres "
              "completar el login manualmente con otro script.")
    except FloodWaitError as e:
        print(f"FLOOD WAIT: Telegram está bloqueando el envío de códigos "
              f"durante {e.seconds} segundos (~{e.seconds / 60:.1f} min) "
              f"por exceso de solicitudes recientes. Espera ese tiempo antes "
              f"de reintentar.")
    except Exception as e:  # noqa: BLE001
        print(f"Error inesperado al pedir el código: {type(e).__name__}: {e}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
