"""Healthcheck de ingesta: detecta que entran mensajes pero no sale nada.

El fallo que motiva esto (oct 2026): OpenAI sin crédito → 76 fotos sin
OCR y la extracción muerta durante ~14h sin que nadie se enterara.
Las señales son dos contadores baratos en BD sobre las últimas horas:

- fotos recientes sin `extracted_text` (la cadena OCR caída),
- raws `processed=False` (excepciones en la extracción/persistencia),
- silencio total: ningún raw en ≥18h (listener/cuenta Telegram muerta —
  el hueco nocturno habitual ronda 12h y no debe dispararlo).

Con cualquiera por encima del umbral se notifica a admins por push,
con cooldown persistido para no spamear mientras dure la caída.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.notifications.push import notify_ingest_down
from app.services.results.base import is_rate_limited, mark_cooldown

logger = get_logger("app.maintenance.ingest_health")

# Ventana de medida: lo que pasa "ahora", no el histórico.
_HEALTH_WINDOW = timedelta(hours=3)
# Gracia: un raw recién llegado puede estar aún en OCR/proceso — solo
# cuentan como fallo los que llevan >10 min sin resolverse (evita
# falsos positivos con un álbum que llega justo antes del chequeo).
_GRACE = timedelta(minutes=10)
# Umbrales: suficientemente altos para no dispararse con un mensaje
# suelto problemático, suficientemente bajos para pillar un día muerto.
_OCR_FAIL_THRESHOLD = 5
_PROC_FAIL_THRESHOLD = 8
# Silencio de entrada: si el raw más reciente de CUALQUIER canal lleva
# >=18h, el listener está caído o la cuenta desconectada (los canales
# publican a diario; la noche tranquila ronda 12h).
_SILENCE_THRESHOLD = timedelta(hours=18)
# Un aviso cada 6h mientras siga roto (persistido en provider_state).
_ALERT_COOLDOWN = timedelta(hours=6)
_ALERT_MARK = "ingest-health"


@dataclass
class IngestHealth:
    """Contadores de la ventana reciente."""

    raws_ventana: int
    fotos_sin_ocr: int
    raws_sin_procesar: int
    # Timestamp del raw más reciente (None = BD sin raws: sistema
    # recién instalado, no alerta).
    ultimo_raw: datetime | None = None


async def check_ingest_health(session) -> IngestHealth:
    """Cuenta las dos señales en la ventana reciente. Solo SELECTs —
    corre en cada ciclo del snapshotter sin coste apreciable."""
    window = utc_now() - _HEALTH_WINDOW
    settled = utc_now() - _GRACE
    raws_ventana = (
        await session.exec(
            select(func.count(TelegramRawMessage.id)).where(
                TelegramRawMessage.received_at >= window
            )
        )
    ).scalar()
    fotos_sin_ocr = (
        await session.exec(
            select(func.count(TelegramRawMessage.id)).where(
                TelegramRawMessage.received_at.between(window, settled),
                TelegramRawMessage.media_path != None,  # noqa: E711
                or_(
                    TelegramRawMessage.extracted_text.is_(None),
                    func.trim(TelegramRawMessage.extracted_text) == "",
                ),
            )
        )
    ).scalar()
    raws_sin_procesar = (
        await session.exec(
            select(func.count(TelegramRawMessage.id)).where(
                TelegramRawMessage.received_at.between(window, settled),
                TelegramRawMessage.processed == False,  # noqa: E712
            )
        )
    ).scalar()
    ultimo_raw = (
        await session.exec(select(func.max(TelegramRawMessage.received_at)))
    ).scalar()
    return IngestHealth(
        raws_ventana=raws_ventana,
        fotos_sin_ocr=fotos_sin_ocr,
        raws_sin_procesar=raws_sin_procesar,
        ultimo_raw=ultimo_raw,
    )


async def run_ingest_healthcheck() -> None:
    """Pasada del healthcheck: si hay señal de ingesta caída y no se
    avisó en el cooldown, push a admins. Nunca lanza — un fallo del
    propio healthcheck no puede tumbar el loop que lo hospeda."""
    try:
        async with AsyncSessionLocal() as session:
            health = await check_ingest_health(session)
        problems: list[str] = []
        if health.fotos_sin_ocr >= _OCR_FAIL_THRESHOLD:
            problems.append(f"{health.fotos_sin_ocr} fotos sin OCR")
        if health.raws_sin_procesar >= _PROC_FAIL_THRESHOLD:
            problems.append(f"{health.raws_sin_procesar} raws sin procesar")
        if health.ultimo_raw is not None:
            silencio = utc_now() - health.ultimo_raw
            if silencio >= _SILENCE_THRESHOLD:
                horas = int(silencio.total_seconds() // 3600)
                problems.append(f"sin mensajes nuevos en {horas}h")
        if not problems:
            return
        if is_rate_limited(_ALERT_MARK):
            return
        mark_cooldown(_ALERT_MARK, utc_now() + _ALERT_COOLDOWN)
        logger.warning(
            "[INGEST] posible ingesta caída: %s (%s raws en 3h)",
            ", ".join(problems),
            health.raws_ventana,
        )
        await notify_ingest_down(", ".join(problems), health.raws_ventana)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INGEST] healthcheck falló: %s", exc)
