# ruff: noqa: E402
"""Login de Telethon por código QR (sin SMS ni código por app).

Genera `login_qr.png` en backend/: se abre en el PC y se escanea desde
Telegram → Ajustes → Dispositivos → Vincular dispositivo, con la cuenta
que se quiera usar como sensor. El token del QR caduca cada ~30 s; el
script lo regenera automáticamente hasta que se escanee.

Uso:
    .\\.venv\\Scripts\\python.exe scripts\\telegram_qr_login.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import qrcode
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("scripts.telegram_qr_login")

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
session_name = os.getenv("TELEGRAM_SESSION_NAME", "controlpick_telegram")
QR_PATH = os.path.join(os.path.dirname(__file__), "..", "login_qr.png")


def _save_qr(url: str) -> None:
    qrcode.make(url).save(QR_PATH)
    logger.info("QR guardado en %s — escanéalo desde Telegram", QR_PATH)


async def main() -> None:
    client = TelegramClient(session_name, int(api_id), api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        logger.info("Sesión ya autorizada como %s (@%s)", me.first_name, me.username)
        await client.disconnect()
        return

    qr = await client.qr_login()
    _save_qr(qr.url)

    while True:
        try:
            await qr.wait()
            break
        except asyncio.TimeoutError:
            await qr.recreate()
            _save_qr(qr.url)
        except SessionPasswordNeededError:
            logger.error("La cuenta tiene 2FA — desactívalo o usa login por código.")
            await client.disconnect()
            return

    me = await client.get_me()
    logger.info(
        "Login QR completado como: %s (@%s) — sesión en %s.session",
        me.first_name,
        me.username,
        session_name,
    )
    try:
        os.remove(QR_PATH)
    except OSError:
        pass
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
