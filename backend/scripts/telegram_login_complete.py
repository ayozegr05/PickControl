"""Completa el login de Telethon iniciado por `telegram_login_diagnostic.py`.

Uso:

    .\\.venv\\Scripts\\python.exe scripts\\telegram_login_complete.py <CODIGO> <PHONE_CODE_HASH>

Guarda la sesión autenticada en `<TELEGRAM_SESSION_NAME>.session`, que es
la misma que usará luego `app/services/telegram/client.py` al arrancar
el backend. Script de diagnóstico puntual, no forma parte del arranque
normal de la app.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
phone = os.getenv("TELEGRAM_PHONE")
session_name = os.getenv("TELEGRAM_SESSION_NAME", "controlpick_telegram")


async def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: telegram_login_complete.py <CODIGO> <PHONE_CODE_HASH>")
        sys.exit(1)

    code = sys.argv[1]
    phone_code_hash = sys.argv[2]

    client = TelegramClient(session_name, int(api_id), api_hash)
    await client.connect()

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        me = await client.get_me()
        print(f"Login completado correctamente como: {me.first_name} (@{me.username})")
    except SessionPasswordNeededError:
        password = input(
            "Verificación en dos pasos activada. Introduce tu contraseña de Telegram: "
        )
        await client.sign_in(password=password)
        me = await client.get_me()
        print(f"Login completado correctamente como: {me.first_name} (@{me.username})")
    except Exception as e:  # noqa: BLE001
        print(f"Error al iniciar sesión: {type(e).__name__}: {e}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
