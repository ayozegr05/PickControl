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

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.notifications.push import notify_ingest_down
from app.services.results.base import (
    is_rate_limited,
    mark_cooldown,
    rate_limited_until,
)

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
# Marca "hubo alerta sin confirmar recuperación": vive hasta que una
# pasada sana la limpia y manda el aviso de recuperado (una sola vez,
# aunque el cooldown de 6h haya expirado en medio).
_FLAG_MARK = "ingest-health-alerted"
_FLAG_TTL = timedelta(days=30)


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


@dataclass
class IngestProblem:
    """Un fallo concreto de la cadena en lenguaje claro — el correo
    debe decir QUÉ se rompió y si hay que actuar, no un genérico
    "ingesta caída" (caso real nov-2026: 25 fotos sin leer con el
    listener perfectamente vivo)."""

    kind: str  # "ocr" | "extraccion" | "listener"
    title: str
    cause: str  # causa probable
    impact: str  # qué se pierde mientras dure
    action: str  # se autorrecupera o requiere acción humana


def _build_problems(health: IngestHealth) -> list[IngestProblem]:
    """Traduce los contadores a problemas explicados. El orden es el
    del correo: primero lo que se ve (fotos), luego la cola, al final
    el fallo grave (listener)."""
    problems: list[IngestProblem] = []
    if health.fotos_sin_ocr >= _OCR_FAIL_THRESHOLD:
        problems.append(
            IngestProblem(
                kind="ocr",
                title=f"📷 {health.fotos_sin_ocr} fotos sin leer (OCR)",
                cause=(
                    "Los lectores de imágenes no responden: la cuota "
                    "diaria de Gemini está agotada y/o el respaldo de "
                    "OpenRouter está caído."
                ),
                impact=(
                    "Los picks que llegan COMO FOTO no se interpretan "
                    "ahora mismo. Los de texto sí funcionan."
                ),
                action=(
                    "Ninguna necesaria: los mensajes están guardados "
                    "y el rescate automático las reintenta cada 6h. "
                    "Si mañana sigue igual, revisar las keys de "
                    "Gemini/OpenRouter."
                ),
            )
        )
    if health.raws_sin_procesar >= _PROC_FAIL_THRESHOLD:
        problems.append(
            IngestProblem(
                kind="extraccion",
                title=f"⚙️ {health.raws_sin_procesar} mensajes sin procesar",
                cause=(
                    "Los extractores LLM (Groq/OpenRouter) no responden "
                    "o van muy lentos."
                ),
                impact=(
                    "Los mensajes entran y se guardan, pero no se "
                    "convierten en pick ni se descartan: se acumulan "
                    "pendientes."
                ),
                action=(
                    "Ninguna de momento: quedan encolados y el rescate "
                    "los reprocesa solo cuando vuelva un extractor. "
                    "Si dura más de 24h, revisar las keys."
                ),
            )
        )
    if health.ultimo_raw is not None:
        silencio = utc_now() - health.ultimo_raw
        if silencio >= _SILENCE_THRESHOLD:
            horas = int(silencio.total_seconds() // 3600)
            problems.append(
                IngestProblem(
                    kind="listener",
                    title=f"🔌 Sin mensajes nuevos en {horas}h",
                    cause=(
                        "El listener de Telegram está desconectado o "
                        "el backend está caído."
                    ),
                    impact=(
                        "Nada de lo que publiquen los canales está "
                        "entrando: esto SÍ es una parada de ingesta "
                        "real."
                    ),
                    action=(
                        "REQUIERE ACCIÓN: comprobar que el contenedor "
                        "backend está vivo y la sesión de Telegram "
                        "conectada. Al reconectar, el catch-up "
                        "recupera lo perdido (hasta 7 días)."
                    ),
                )
            )
    return problems


def _provider_status_lines(settings) -> list[str]:
    """Línea de estado por pieza de la cadena (sin key / OK /
    aparcado hasta HH:MM) — el correo debe mostrar qué respaldo
    sigue vivo, no solo qué falla."""
    from app.services.telegram import ocr

    def _estado(env_key: str, marks: list[str]) -> str:
        if not getattr(settings, env_key, None):
            return "sin key"
        aparcados = [
            u
            for u in (rate_limited_until(m) for m in marks)
            if u is not None and u > utc_now()
        ]
        if len(aparcados) == len(marks):
            return f"aparcado hasta ~{max(aparcados):%H:%M}"
        return "OK"

    lines: list[str] = []
    if not getattr(settings, "google_api_key", None):
        lines.append("• OCR Gemini: sin key")
    else:
        aparcados = [
            u
            for m in ocr._GEMINI_MODELS
            if (u := ocr._GEMINI_QUOTA_UNTIL.get(m)) is not None and u > utc_now()
        ]
        if len(aparcados) == len(ocr._GEMINI_MODELS):
            lines.append(
                f"• OCR Gemini: aparcado hasta ~{max(aparcados):%H:%M} "
                "(cuota diaria agotada)"
            )
        else:
            lines.append("• OCR Gemini: OK")
    lines.append(
        "• OCR OpenRouter: "
        + _estado(
            "openrouter_api_key",
            [ocr._vision_mark(ep[0]) for ep in ocr._VISION_ENDPOINTS],
        )
    )
    lines.append(
        "• Extracción Groq: "
        + _estado(
            "groq_api_key",
            ["llm-compat-groq-ingesta", "llm-compat-groq"],
        )
    )
    lines.append(
        "• Extracción OpenRouter: "
        + _estado("openrouter_api_key", ["llm-compat-openrouter"])
    )
    return lines


_KIND_LABEL = {"ocr": "OCR", "extraccion": "extracción", "listener": "listener"}


def _format_alert(
    health: IngestHealth,
    problems: list[IngestProblem],
    status_lines: list[str],
) -> tuple[str, str, str]:
    """(subject, push_body, email_body). El subject nombra el fallo
    real; el email explica qué sigue funcionando y qué no."""
    kinds = [p.kind for p in problems]
    if kinds == ["listener"]:
        subject = "[ControlPick] 🔌 Sin mensajes de Telegram — listener parado"
    elif kinds == ["ocr"]:
        subject = f"[ControlPick] 📷 OCR de fotos acumulado ({health.fotos_sin_ocr})"
    elif kinds == ["extraccion"]:
        subject = f"[ControlPick] ⚙️ Extracción atascada ({health.raws_sin_procesar})"
    else:
        subject = "[ControlPick] ⚠️ Ingesta degradada: " + " + ".join(
            _KIND_LABEL[k] for k in kinds
        )

    entrada = (
        f"✅ Los mensajes SIGUEN entrando y guardándose: "
        f"{health.raws_ventana} en las últimas 3h."
        if health.raws_ventana > 0
        else "No ha entrado ningún mensaje en las últimas 3h."
    )
    email = f"=== ControlPick — estado de la ingesta ===\n\n{entrada}\n\n"
    email += "❌ PROBLEMAS DETECTADOS\n"
    for i, p in enumerate(problems, 1):
        email += (
            f"{i}) {p.title}\n"
            f"   Causa probable: {p.cause}\n"
            f"   Impacto: {p.impact}\n"
            f"   Acción: {p.action}\n\n"
        )
    if status_lines:
        email += (
            "📡 ESTADO DE LOS LECTORES/EXTRACTORES\n" + "\n".join(status_lines) + "\n"
        )

    entrada_short = (
        f"mensajes entrando ({health.raws_ventana} en 3h)"
        if health.raws_ventana > 0
        else "sin mensajes nuevos"
    )
    push_body = " | ".join(p.title for p in problems) + f" — {entrada_short}"
    return subject, push_body, email


def _format_recovery(
    health: IngestHealth, status_lines: list[str]
) -> tuple[str, str, str]:
    """Aviso "ya volvió": mismo canal que la alerta, pero verde."""
    subject = "[ControlPick] ✅ Ingesta recuperada"
    push_body = (
        f"Todo vuelve a funcionar: {health.raws_ventana} mensajes en "
        "3h, sin fotos pendientes ni cola."
    )
    email = (
        "=== ControlPick — ingesta recuperada ===\n\n"
        "El problema anterior se ha resuelto solo. Estado actual:\n"
        f"• Mensajes recibidos (3h): {health.raws_ventana}\n"
        f"• Fotos sin leer: {health.fotos_sin_ocr}\n"
        f"• Pendientes de procesar: {health.raws_sin_procesar}\n"
    )
    if status_lines:
        email += (
            "\n📡 ESTADO DE LOS LECTORES/EXTRACTORES\n" + "\n".join(status_lines) + "\n"
        )
    return subject, push_body, email


async def run_ingest_healthcheck() -> None:
    """Pasada del healthcheck: si hay señal de fallo y no se avisó en
    el cooldown, email+push a admins; si la pasada sale limpia tras
    una alerta, avisa "recuperado" una vez. Nunca lanza — un fallo
    del propio healthcheck no puede tumbar el loop que lo hospeda."""
    try:
        async with AsyncSessionLocal() as session:
            health = await check_ingest_health(session)
        problems = _build_problems(health)
        if not problems:
            # Pasada limpia: si había una alerta pendiente de
            # confirmar recuperación, se avisa una vez y se limpia.
            if is_rate_limited(_FLAG_MARK):
                mark_cooldown(_FLAG_MARK, utc_now())
                subject, push_body, email_body = _format_recovery(
                    health, _provider_status_lines(get_settings())
                )
                logger.info("%s", subject)
                await notify_ingest_down(subject, push_body, email_body)
            return
        if is_rate_limited(_ALERT_MARK):
            return
        mark_cooldown(_ALERT_MARK, utc_now() + _ALERT_COOLDOWN)
        mark_cooldown(_FLAG_MARK, utc_now() + _FLAG_TTL)
        subject, push_body, email_body = _format_alert(
            health, problems, _provider_status_lines(get_settings())
        )
        logger.warning("%s — %s", subject, push_body)
        await notify_ingest_down(subject, push_body, email_body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INGEST] healthcheck falló: %s", exc)
