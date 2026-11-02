"""Auditoría diaria de falsos rechazos de la extracción.

El pipeline descarta como `metodo='rejected'` tanto mensajes que el
pre-filtro no reconoció como apuesta como textos que el extractor
juzgó no-apuesta. En volumen es correcto (~180/día, marketing y
charla), pero nunca se mide la tasa de falso rechazo — este ciclo la
audita y además RECUPERA los picks reales que se colaron:

- muestrea los rechazos más recientes (~15/día — las llamadas LLM van
  por la cascada gratuita, sin coste);
- re-extrae cada uno con `extract_pick` (reglas + LLM);
- si resulta apuesta: la promociona en el ParsedPick existente, con
  dedup por canal/ventana (`_find_duplicate_pick`, mismo que el
  ingest normal) — los duplicados se fusionan, no duplican;
- marca cada muestra (`reject-audit|{id}` en provider_state) para no
  re-muestrear el mismo rechazo en pasadas siguientes;
- si recupera algo, push a admins (`notify_rejects_recovered`) — un
  falso rechazo recuperado es un pick real que se habría perdido.

Corre una vez al día dentro del loop de rescate (`lifecycle.py`).
Fail-open: un fallo de la auditoría se loguea y nunca tumba el loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.notifications.push import notify_rejects_recovered
from app.services.results.base import (
    is_missed,
    mark_missed,
    record_loop_summary,
)
from app.services.telegram.pick_extractor import extract_pick
from app.services.telegram.processor import (
    _find_duplicate_pick,
    _merge_pick_data,
)

logger = get_logger("app.maintenance.reject_audit")

# Rechazos re-extraídos por pasada diaria (cascada gratuita: coste 0,
# el techo real es no saturar a Groq/OpenRouter con trabajo muerto).
_AUDIT_SAMPLE = 15
# Solo rechazos recientes: los antiguos ya pasaron por revisiones
# manuales (reclassify_rejected.py) o prescribieron para la auditoría.
_AUDIT_WINDOW = timedelta(days=2)
_AUDIT_MARK = "reject-audit|{id}"


@dataclass
class RejectAuditReport:
    """Resumen de una pasada de auditoría."""

    muestreados: int = 0
    recuperados: int = 0
    duplicados: int = 0
    sigue_rechazado: int = 0
    sin_texto: int = 0
    errores: int = 0
    recuperados_detalle: list[str] = field(default_factory=list)


async def audit_rejected_picks(
    session, sample: int = _AUDIT_SAMPLE
) -> RejectAuditReport:
    """Re-extrae una muestra de rechazos recientes. Marca cada muestra
    auditada para no repetirla; promociona los que sí eran apuesta."""
    report = RejectAuditReport()
    result = await session.exec(
        select(ParsedPick, TelegramRawMessage)
        .join(
            TelegramRawMessage,
            ParsedPick.raw_message_id == TelegramRawMessage.id,
        )
        .where(ParsedPick.es_apuesta == False)  # noqa: E712
        .where(ParsedPick.metodo == "rejected")
        .where(TelegramRawMessage.received_at >= utc_now() - _AUDIT_WINDOW)
        .order_by(TelegramRawMessage.received_at.desc())
        .limit(sample * 3)  # margen: algunos se saltarán (ya auditados/sin texto)
    )
    settings = get_settings()
    for parsed, raw in result.all():
        if report.muestreados >= sample:
            break
        if is_missed(_AUDIT_MARK.format(id=parsed.id)):
            continue
        mark_missed(_AUDIT_MARK.format(id=parsed.id))
        report.muestreados += 1

        source_text = (raw.extracted_text or raw.text or "").strip()
        if not source_text:
            report.sin_texto += 1
            continue
        try:
            pick = await extract_pick(
                source_text,
                settings.openai_api_key,
                informante=raw.channel_name,
                fecha_referencia=raw.received_at,
            )
        except Exception as exc:  # noqa: BLE001 — cascada agotada, etc.
            report.errores += 1
            logger.warning(
                "[REJECT_AUDIT] pick %s re-extracción falló: %s",
                parsed.id,
                exc,
            )
            continue
        if not pick or not pick.es_apuesta:
            report.sigue_rechazado += 1
            continue

        if pick.fecha_evento is None and raw.received_at is not None:
            pick.fecha_evento = raw.received_at
        duplicate = await _find_duplicate_pick(
            session, raw.channel_name, pick, raw.received_at
        )
        if duplicate:
            report.duplicados += 1
            if _merge_pick_data(duplicate, pick):
                session.add(duplicate)
            continue

        # Falso rechazo confirmado: promocionar en la fila existente
        # (mismo que reclassify_rejected.py --apply).
        parsed.es_apuesta = True
        parsed.apuesta = pick.seleccion
        parsed.deporte = pick.deporte
        parsed.evento = pick.evento
        parsed.mercado = pick.mercado
        parsed.seleccion = pick.seleccion
        parsed.cuota = pick.cuota
        parsed.stake = pick.stake
        parsed.casa = pick.casa
        parsed.fecha_evento = pick.fecha_evento
        parsed.linea = pick.linea
        # metodo es varchar(20): "+audit" desbordaría los motores largos
        # ("rule+llm-openrouter"=19). La marca de recuperación va en
        # explicacion (sin límite) y metodo queda fiel al extractor.
        parsed.metodo = pick.metodo
        parsed.explicacion = f"{pick.explicacion or ''} [re-audit]".strip()
        parsed.confianza = pick.confianza
        session.add(parsed)
        report.recuperados += 1
        report.recuperados_detalle.append(
            f"#{parsed.id} '{pick.seleccion}' @{pick.cuota}"
        )
        logger.warning(
            "[REJECT_AUDIT] falso rechazo recuperado: pick %s '%s' @%s",
            parsed.id,
            pick.seleccion,
            pick.cuota,
        )
    return report


async def run_reject_audit() -> None:
    """Pasada diaria: audita una muestra, commitea las recuperaciones
    y avisa a admins si había picks reales entre los rechazos."""
    try:
        async with AsyncSessionLocal() as session:
            report = await audit_rejected_picks(session)
            await session.commit()
        record_loop_summary(
            "reject_audit",
            {
                "muestreados": report.muestreados,
                "recuperados": report.recuperados,
                "duplicados": report.duplicados,
                "sigue_rechazado": report.sigue_rechazado,
                "errores": report.errores,
            },
        )
        if report.muestreados:
            logger.info(
                "[REJECT_AUDIT] %s muestreados: %s recuperados, %s dups, "
                "%s ok rechazados, %s errores",
                report.muestreados,
                report.recuperados,
                report.duplicados,
                report.sigue_rechazado,
                report.errores,
            )
        if report.recuperados:
            await notify_rejects_recovered(
                report.recuperados, "; ".join(report.recuperados_detalle[:5])
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[REJECT_AUDIT] la auditoría falló: %s", exc)
