# ruff: noqa: E402
"""Reenvía el código de login de Telethon escalando el método de entrega.

Cuando el primer `send_code_request` entrega por app (`SentCodeTypeApp`)
y el chat "Telegram" no aparece, `auth.ResendCodeRequest` hace que
Telegram rote al siguiente canal disponible (app → SMS → llamada) sin
invalidar el `phone_code_hash` del login en curso.

Uso:
    .\\.venv\\Scripts\\python.exe scripts\\telegram_resend_code.py <PHONE_CODE_HASH>
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl import functions

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("scripts.telegram_resend_code")

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
phone = os.getenv("TELEGRAM_PHONE")
session_name = os.getenv("TELEGRAM_SESSION_NAME", "controlpick_telegram")


async def main() -> None:
    if len(sys.argv) < 2:
        logger.error("Uso: telegram_resend_code.py <PHONE_CODE_HASH>")
        sys.exit(1)

    phone_code_hash = sys.argv[1]

    client = TelegramClient(session_name, int(api_id), api_hash)
    await client.connect()

    try:
        sent = await client(
            functions.auth.ResendCodeRequest(
                phone_number=phone, phone_code_hash=phone_code_hash
            )
        )
        logger.info("Código reenviado. Detalles:")
        logger.info("  Tipo de envío: %s", type(sent.type).__name__)
        logger.info("  phone_code_hash: %s", sent.phone_code_hash)
        logger.info("  timeout: %ss", sent.timeout)
    except Exception as e:  # noqa: BLE001
        logger.error("Error al reenviar el código: %s: %s", type(e).__name__, e)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
