"""Configuración central de la aplicación.

Lee las variables de entorno de forma tipada usando pydantic-settings.
Puedes elegir el fichero de entorno con la variable `ENV_FILE`:

    ENV_FILE=.env.development uvicorn app.main:app --reload

Esto permite tener `.env.development`, `.env.production`, etc., sin
mezclar secretos reales en el repositorio.
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = os.getenv("ENV_FILE", ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # Servidor
    node_env: str = "development"
    server_url: str = "http://localhost:3000"
    # Orígenes permitidos para CORS. En desarrollo suele ser "*"; en
    # producción, una lista separada por comas, p. ej.:
    # CORS_ORIGINS=https://app.tudominio.com,https://admin.tudominio.com
    cors_origins: str = "*"

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
    # Puedes poner varios separados por comas.
    telegram_target_channel: str | None = None
    # Carpeta donde se descargan imágenes de Telegram.
    telegram_media_path: str = "media/telegram"

    # OpenAI (opcional, para OCR de imágenes de Telegram).
    openai_api_key: str | None = None

    # Verificación automática de resultados (opcional).
    # football-data.org: ligas top (La Liga, Champions, Premier...), gratis.
    football_data_api_key: str | None = None
    # API-Football vía RapidAPI: más cobertura (ligas menores), límite más bajo.
    api_football_key: str | None = None
    api_football_host: str = "api-football-v1.p.rapidapi.com"
    # Cada cuántas horas se revisan picks pendientes de verificar en segundo plano.
    results_verification_interval_hours: float = 3.0


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de Settings (patrón singleton)."""
    return Settings()
