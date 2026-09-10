"""Cliente de Telegram (Telethon) para escuchar canales como cuenta de usuario.

Sustituye a la integración basada en `node-telegram-bot-api`
(`backend/telegramBot.js`, retirado). Telethon actúa como una cuenta de
usuario de Telegram, lo que permite leer el historial y los mensajes
nuevos de un canal sin necesidad de que un bot sea administrador de él.

Nota: la primera vez que se ejecuta, Telethon necesita autenticar la
cuenta de forma interactiva (código enviado por Telegram al
`TELEGRAM_PHONE` configurado, ver `.env.example`). Tras ese primer
login, la sesión queda guardada en un archivo
`<TELEGRAM_SESSION_NAME>.session` y los siguientes arranques son
automáticos.
"""

from telethon import TelegramClient

from app.core.config import get_settings

_client: TelegramClient | None = None


def get_telegram_client() -> TelegramClient:
    """Devuelve la instancia (singleton) del cliente de Telethon.

    Lanza `RuntimeError` si `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` no
    están configurados, para fallar rápido en vez de arrancar un
    cliente inválido.
    """
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise RuntimeError(
                "TELEGRAM_API_ID y TELEGRAM_API_HASH son obligatorios para "
                "iniciar el cliente de Telegram (ver .env.example)."
            )
        _client = TelegramClient(
            settings.telegram_session_name,
            int(settings.telegram_api_id),
            settings.telegram_api_hash,
        )
    return _client


def reset_telegram_client() -> None:
    """Descarta la instancia cacheada del cliente (usado al hacer shutdown)."""
    global _client
    _client = None
