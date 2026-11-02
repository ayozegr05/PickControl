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

    # Extractores texto→JSON gratis (API OpenAI-compatible, sin
    # tarjeta): cascada Groq -> Cerebras -> OpenRouter antes de gastar
    # la cuota mínima de Gemini. Cada key activa su endpoint.
    groq_api_key: str | None = None
    cerebras_api_key: str | None = None
    openrouter_api_key: str | None = None

    # Verificación automática de resultados (opcional).
    # football-data.org: ligas top (La Liga, Champions, Premier...), gratis.
    football_data_api_key: str | None = None
    # API-Football: más cobertura (ligas menores), límite más bajo (100/día).
    # Por defecto, acceso directo en api-football.com (api-sports.io). Si te
    # registraste vía RapidAPI en su lugar, usa
    # "api-football-v1.p.rapidapi.com".
    api_football_key: str | None = None
    api_football_host: str = "v3.football.api-sports.io"
    # Baloncesto vía API-Basketball (misma familia api-sports, pero
    # producto y cuota propios — 100 req/día en el plan free, que cubre
    # la temporada actual: ACB, Supercopa, Euroliga, NBA...). Una llamada
    # games?date= trae TODOS los partidos del día. Registro gratuito en
    # dashboard.api-football.com eligiendo el producto Basketball.
    api_basketball_key: str | None = None
    api_basketball_host: str = "v1.basketball.api-sports.io"
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
    # Reutiliza RAPIDAPI_TENNIS_KEY; plan BASIC de 200 req/MES — cuando
    # se agota, el reset real viene en x-ratelimit-requests-reset.
    rapidapi_oddspapi_host: str = "bet36528.p.rapidapi.com"
    # API directa de OddsPapi (api.oddspapi.io): misma data, pero
    # /v4/historical-odds NO consume cuota (gratis por diseño); solo
    # /v4/fixtures y /v4/markets cuentan contra las ~250 req/mes del
    # plan free. Si hay key directa se prefiere sobre el canal RapidAPI.
    oddspapi_direct_key: str | None = None
    # Segunda fuente de histórico: "OddsFeed" (odds-feed). Reutiliza la
    # misma key de cuenta (cuota propia: 500 req/mes en BASIC). Cubre
    # solo mercados principales (1X2, O/U, hándicap, BTTS) — sin props.
    rapidapi_oddsfeed_host: str = "odds-feed.p.rapidapi.com"
    # Espejo RapidAPI con rutas nativas de Sofascore (/api/v1/...):
    # "SportAPI" (sportapi7). Reutiliza RAPIDAPI_TENNIS_KEY y aporta
    # otra cuota diaria propia — pero hay que suscribirlo a mano en la
    # cuenta RapidAPI. Apagado por defecto: activar con
    # RAPIDAPI_SPORTAPI7_ENABLED=true tras suscribir el plan BASIC.
    rapidapi_sportapi7_enabled: bool = False
    rapidapi_sportapi7_host: str = "sportapi7.p.rapidapi.com"
    # Espejo "SofaScore" (sofascore6): mismos ids pero rutas propias
    # (/api/sofascore/v1/...) — el transporte las traduce. También
    # reutiliza RAPIDAPI_TENNIS_KEY con cuota diaria propia tras
    # suscribir el plan BASIC y activar el flag.
    rapidapi_sofascore6_enabled: bool = False
    rapidapi_sofascore6_host: str = "sofascore6.p.rapidapi.com"
    # Kill-switch del acceso DIRECTO a la API nativa de Sofascore
    # (curl_cffi, sin cuota pero tras Cloudflare). True por defecto;
    # poner false apaga esa vía por completo sin tocar código — útil
    # si algún día aparecen bloqueos 403 en los logs.
    sofascore_direct_enabled: bool = True
    # The Odds API (the-odds-api.com): la ÚNICA fuente que publica
    # props de jugador (goleador, asistencias, marca-o-asiste) y
    # escaleras alternas de córners/tarjetas fuera de bet365. Solo
    # cuotas en vivo/futuras — el histórico pide plan de pago.
    # Free tier: 500 créditos/MES; cada mercado×región cuesta 1
    # crédito (el listado de eventos es gratis). El provider solo
    # se activa para picks de prop/línea alterna — un pick normal
    # nunca quema créditos aquí.
    odds_api_key: str | None = None
    # BetsAPI (api.b365api.com): el feed bet365 real — listado por
    # día + prematch completo (todos los mercados que bet365 ofreció,
    # congelados al entrar en vivo). Reemplazo directo de OddsPapi:
    # cubre Champions, ligas grandes, Challenger/ITF y basket global.
    # Trial "Bet365 API": 1.800 req/hora. Sin token no se registra.
    betsapi_token: str | None = None
    # Wrapper RapidAPI "BetsAPI" (betsapi2): el MISMO feed bet365 pero
    # bajo /v3 y plan gratis (~24 req/día). El prematch solo vive
    # mientras el partido no ha empezado — sirve para congelar cuotas
    # de picks nuevos en el snapshotter, NO para el backlog histórico
    # (los terminados devuelven el stub sin mercados). Reutiliza
    # RAPIDAPI_TENNIS_KEY (la key es por cuenta); sin suscripción al
    # endpoint el primer 403 lo aparca solo.
    rapidapi_betsapi_host: str = "betsapi2.p.rapidapi.com"
    # PulseScore (api.pulsescore.net): espejo NORMALIZADO del feed
    # bet365 — mismos FI que BetsAPI, esquema canonicalMarket +
    # selections. Plan BASIC gratis: 500 req/mes, 1 req/segundo,
    # headers X-RateLimit-*. Solo prematch en vivo (el evento
    # desaparece del board al empezar) — igual que el wrapper
    # RapidAPI sirve para congelar futuros, nunca para backlog.
    # Su listado /{sport}/events ya trae los mercados dentro: una
    # llamada resuelve evento + cuotas. Auth: header X-Secret.
    pulsescore_key: str | None = None
    pulsescore_host: str = "api.pulsescore.net"
    pulsescore_bookmaker: str = "v3/bet365"
    odds_api_regions: str = "uk,eu"
    odds_api_markets: str = (
        "player_goal_scorer_anytime,player_to_score_or_assist,"
        "player_assists,player_first_goal_scorer,player_last_goal_scorer,"
        "alternate_totals_corners,alternate_totals_cards,correct_score"
    )
    # APIHub (proxy.apihub.io): wrapper normalizado de 1xBet (mercados
    # etiquetados: "Corners" -> "3Way Total" — el mercado exacto de
    # las líneas enteras del tipster) y FlashScore (respaldo vivo
    # multi-casa, ventana ~7 días). 100 llamadas gratis/mes por
    # servicio; un 402/403 lo aparca hasta el día 1.
    apihub_key: str | None = None
    # 5DollarFootballAPI: apertura + cierre REALES de bet365 en las 5
    # grandes ligas (Free: 60 req/h, ~3 meses de histórico). Fuente
    # histórica del backfill y evidencia documentada de nomarket.
    fivedollar_key: str | None = None
    # Gemini API (proyecto propio en GCP): investigador de último
    # recurso vía url_context (DDG -> webs de resultados). Detecta
    # fixtures cancelados/aplazados/walkover citando fuentes fiables
    # cuando la cascada entera falla. Sin key no se registra.
    google_api_key: str | None = None
    # FlareSolverr (contenedor propio en la VM): Chromium headless que
    # resuelve challenges de Cloudflare y devuelve el HTML renderizado.
    # Fallback de los fetches del investigador cuando el fetch directo
    # recibe 403/challenge (totalcorner, sofascore...). Vacío = off.
    flaresolverr_url: str | None = None
    # Notificaciones push vía Expo Push API (gratis, sin Firebase para
    # el caso básico). `push_notifications_enabled` apaga el envío sin
    # tocar código (dev/tests); `expo_access_token` solo hace falta para
    # volúmenes altos — el envío normal no requiere token.
    push_notifications_enabled: bool = True
    expo_access_token: str | None = None
    # Alertas por email (canal de emergencia cuando el admin no tiene
    # la app instalada): solo las alertas críticas de operación
    # (ingesta caída), no el flujo cotidiano de picks. SMTP estándar —
    # para Gmail: smtp.gmail.com:587 con contraseña de aplicación.
    # `alert_email_to` vacío = canal apagado.
    alert_email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_from: str | None = None  # default: smtp_user
    # Cada cuántos minutos corre el snapshotter de odds en segundo
    # plano. Cadencia corta para capturar bien el cierre de cuotas.
    odds_snapshot_interval_minutes: float = 30.0
    # Cada cuántas horas se revisan picks pendientes de verificar en segundo plano.
    results_verification_interval_hours: float = 3.0
    # Cada cuántas horas corre el rescate (OCR pendiente + reproceso de
    # raws `processed=False`). Consume OpenAI solo si hay pendientes.
    rescue_interval_hours: float = 6.0
    # Días máximos que un raw puede estar pendiente de rescate; pasado
    # ese plazo se da por imposible (la fila queda en BD para auditoría).
    rescue_max_age_days: float = 30.0
    # Cada cuántas horas corre el backfill de cuotas históricas
    # (OddsPapi). Early-exit sin llamadas si no hay backlog pendiente.
    odds_backfill_interval_hours: float = 24.0
    # Secreto fijo del userscript de cosecha de cuotas (Tampermonkey/
    # WebView): cabecera `X-Harvest-Key` en GET /odds-harvest/queue y
    # POST /odds-harvest/event. Vacío = endpoints deshabilitados (503).
    odds_harvest_token: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia cacheada de Settings (patrón singleton)."""
    return Settings()
