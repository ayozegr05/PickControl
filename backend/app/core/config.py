"""Configuración central de la aplicación.

Lee las variables de entorno (ver `.env.example`) de forma tipada usando
pydantic-settings, evitando el acceso disperso a `os.environ` que había
en el backend Node original.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Servidor
    node_env: str = "development"
    server_url: str = "http://localhost:3000"

    # Base de datos (PostgreSQL)
    database_url: str = (
        "postgresql+asyncpg://usuario:password@localhost:5432/tipster_auditor"
    )

    # Autenticación
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 1440

    # Telegram (cliente de usuario vía Telethon, ver app/services/telegram/)
    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    telegram_phone: str | None = None
    telegram_session_name: str = "controlpick_telegram"
    # Canal a escuchar: username (sin @) o id numérico (con prefijo -100).
    telegram_target_channel: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de Settings (patrón singleton)."""
    return Settings()
