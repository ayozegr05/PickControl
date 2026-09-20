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
    # API-Football: más cobertura (ligas menores), límite más bajo (100/día).
    # Por defecto, acceso directo en api-football.com (api-sports.io). Si te
    # registraste vía RapidAPI en su lugar, usa
    # "api-football-v1.p.rapidapi.com".
    api_football_key: str | None = None
    api_football_host: str = "v3.football.api-sports.io"
    # Tenis vía TheSportsDB (gratis; la key pública "3" vale para uso
    # personal — con Patreon de $2 dan una propia). Cubre ATP/WTA Tour
    # y Grand Slams; Challengers/ITF quedan para el fallback.
    api_tennis_key: str | None = "3"
    # Fallback de tenis vía RapidAPI ("Tennis API - ATP WTA ITF":
    # cubre Challenger/ITF). Plan gratuito con cuota diaria limitada —
    # solo se consume cuando TheSportsDB no encuentra el partido.
    rapidapi_tennis_key: str | None = None
    rapidapi_tennis_host: str = "tennis-api-atp-wta-itf.p.rapidapi.com"
    # Tercer nivel de tenis: "TennisApi" (tennisapi1, datos de Sofascore:
    # ATP/WTA/Challenger/ITF). Reutiliza RAPIDAPI_TENNIS_KEY — la key es
    # por cuenta RapidAPI, no por producto; cada suscripción lleva su
    # propia cuota (~50 req/día cada una).
    rapidapi_tennisapi1_host: str = "tennisapi1.p.rapidapi.com"
    # Proveedor de cuotas de mercado (auditoría "cuota tipster vs
    # real"): "AllSportsApi" en RapidAPI, mismo backend Sofascore que
    # tennisapi1 y misma key de cuenta — pero cuota diaria propia, así
    # los snapshots no compiten con la verificación de resultados.
    rapidapi_allsports_host: str = "allsportsapi2.p.rapidapi.com"
    # Stats/marcadores de fútbol sin ventana de fechas: "FootApi"
    # (footapi7, mismo backend Sofascore). Reutiliza RAPIDAPI_TENNIS_KEY
    # (la key es por cuenta) y tiene cuota diaria propia — rescata los
    # mercados de estadísticas que API-Football ya no puede consultar
    # fuera de su ventana ±1 día del plan gratis.
    rapidapi_footapi_host: str = "footapi7.p.rapidapi.com"
    # Cuotas históricas (backfill de auditoría): "OddsPapi" (bet36528).
    # Reutiliza RAPIDAPI_TENNIS_KEY; el histórico es ilimitado en el
    # plan gratis — solo lo consume scripts/backfill_historical_odds.py.
    rapidapi_oddspapi_host: str = "bet36528.p.rapidapi.com"
    # Cada cuántos minutos corre el snapshotter de odds en segundo
    # plano. Cadencia corta para capturar bien el cierre de cuotas.
    odds_snapshot_interval_minutes: float = 30.0
    # Cada cuántas horas se revisan picks pendientes de verificar en segundo plano.
    results_verification_interval_hours: float = 3.0


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de Settings (patrón singleton)."""
    return Settings()
