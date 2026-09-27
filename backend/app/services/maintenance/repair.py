"""Reparación de picks mal extraídos re-procesando el material guardado.

El raw nunca se borra (auditoría), así que el texto/OCR original sigue
disponible: para picks cuya extracción quedó rota (padres de combinada
con patas de ruido, selecciones sin equipo, "1X" mal clasificado,
patas con `evento` = torneo) se vuelve a correr `extract_pick` sobre el
mismo texto y se corrige la fila en sitio.

Reglas:

- Padre con >=2 patas nuevas -> sigue combinada: las patas viejas
  quedan `es_apuesta=False` + `anulada=True` (fila conservada para
  auditoría, excluida del reparto en `settle_combinada`) y las nuevas
  se insertan como filas propias.
- Padre con 0-1 patas -> degrada a pick simple (los "combinada" de una
  sola selección eran picks sencillos troceados: encabezado de torneo +
  selección + eslogan).
- Re-extracción "no es apuesta" -> `es_apuesta=False` (descarte por
  análisis, no por patrón — auditable y reversible).
- Las filas de patas no se reparan por separado: su parche llega con el
  padre (`combinada_id`).

`repair_picks` es la pieza reusable; `scripts/repair_extraction.py` es
el wrapper manual (dry-run por defecto, `--apply` para escribir).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram.pick_extractor import ExtractedPick, extract_pick
from app.services.telegram.processor import _persist_patas

logger = get_logger("app.maintenance.repair")

# Ventana del "par" foto-boleto + texto del tipster (la misma que usa
# processor._PAIR_WINDOW): al re-extraer un pick cuyo raw es el texto
# se reutiliza el OCR del slip de al lado si existe, y viceversa.
_PAIR_WINDOW = timedelta(minutes=10)


@dataclass
class RepairReport:
    """Resultado de la reparación de una fila (aplicada o dry-run)."""

    pick_id: int
    action: str  # 'repaired' | 'rejected' | 'unchanged' | 'skipped' | 'error'
    detail: str = ""
    old: dict = field(default_factory=dict)
    new: dict = field(default_factory=dict)


def _pick_snapshot(pick: ParsedPick) -> dict:
    return {
        "es_apuesta": pick.es_apuesta,
        "deporte": pick.deporte,
        "evento": pick.evento,
        "mercado": pick.mercado,
        "seleccion": pick.seleccion,
        "cuota": pick.cuota,
        "stake": pick.stake,
        "linea": pick.linea,
        "fecha_evento": pick.fecha_evento.isoformat() if pick.fecha_evento else None,
        "es_combinada": pick.es_combinada,
    }


def _new_snapshot(new: ExtractedPick) -> dict:
    return {
        "es_apuesta": new.es_apuesta,
        "deporte": new.deporte,
        "evento": new.evento,
        "mercado": new.mercado,
        "seleccion": new.seleccion,
        "cuota": new.cuota,
        "stake": new.stake,
        "linea": new.linea,
        "fecha_evento": new.fecha_evento.isoformat() if new.fecha_evento else None,
        "patas": [
            {"seleccion": p.seleccion, "evento": p.evento, "mercado": p.mercado}
            for p in new.patas
        ],
    }


async def _source_text(
    session, pick: ParsedPick
) -> tuple[str, Optional[TelegramRawMessage]]:
    """Texto del mensaje del pick (+ el OCR del slip emparejado si el
    raw propio no lo trae — la pareja foto+texto vive en otro raw)."""
    raw = await session.get(TelegramRawMessage, pick.raw_message_id)
    if raw is None:
        return "", None
    parts = [t.strip() for t in (raw.text or "", raw.extracted_text or "") if t]
    if not raw.extracted_text:
        pair = (
            (
                await session.exec(
                    select(TelegramRawMessage)
                    .where(TelegramRawMessage.channel_id == raw.channel_id)
                    .where(TelegramRawMessage.id != raw.id)
                    .where(TelegramRawMessage.extracted_text.is_not(None))
                    .where(
                        TelegramRawMessage.received_at.between(
                            raw.received_at - _PAIR_WINDOW,
                            raw.received_at + _PAIR_WINDOW,
                        )
                    )
                    .order_by(TelegramRawMessage.received_at)
                )
            )
            .scalars()
            .first()
        )
        if pair and pair.extracted_text:
            parts.append(pair.extracted_text.strip())
    return "\n\n".join(p for p in parts if p), raw


async def _supersede_legs(session, parent: ParsedPick) -> int:
    """Marca las patas viejas como excluidas: `anulada=True` las saca
    del reparto de `settle_combinada` y `es_apuesta=False` las saca del
    escaneo de pendientes. La fila se conserva para auditoría."""
    legs = (
        await session.exec(
            select(ParsedPick).where(ParsedPick.combinada_id == parent.id)
        )
    ).all()
    for leg in legs:
        if leg.es_apuesta or not leg.anulada:
            leg.es_apuesta = False
            leg.anulada = True
            session.add(leg)
    return len(legs)


def _apply_fields(pick: ParsedPick, new: ExtractedPick) -> None:
    """La re-extracción manda en los campos estructurados; cuota/stake/
    casa/explicacion/fecha solo se pisan cuando trae valor (no se borra
    un dato válido porque el segundo parseo no lo repita)."""
    pick.es_apuesta = new.es_apuesta
    if new.seleccion:
        pick.seleccion = new.seleccion
        pick.apuesta = new.seleccion
    # Solo se pisa con dato nuevo: que la re-extracción no encuentre el
    # cruce/mercado/deporte no borra el que ya había.
    if new.evento:
        pick.evento = new.evento
    if new.deporte:
        pick.deporte = new.deporte
    if new.mercado:
        pick.mercado = new.mercado
    if new.linea is not None:
        pick.linea = new.linea
    if new.cuota is not None:
        pick.cuota = new.cuota
    if new.stake is not None:
        pick.stake = new.stake
    if new.casa:
        pick.casa = new.casa
    if new.explicacion:
        pick.explicacion = new.explicacion
    if new.fecha_evento is not None:
        pick.fecha_evento = new.fecha_evento
    pick.metodo = f"{new.metodo}+reext"[:20]
    pick.confianza = new.confianza


async def repair_pick(session, pick: ParsedPick, *, apply: bool) -> RepairReport:
    """Re-extrae UNA fila (padre o simple) desde su raw guardado.

    Las patas no se pasan por aquí: su corrección llega al reparar el
    padre, que regenera el boleto completo.
    """
    report = RepairReport(pick_id=pick.id or 0, action="skipped")
    if pick.combinada_id is not None:
        report.action = "skipped"
        report.detail = "pata de combinada: se repara vía el padre"
        return report

    report.old = _pick_snapshot(pick)
    source, raw = await _source_text(session, pick)
    if not source:
        report.action = "skipped"
        report.detail = "raw sin texto ni OCR"
        return report

    settings = get_settings()
    try:
        new = await extract_pick(
            source,
            settings.openai_api_key,
            informante=raw.channel_name if raw else None,
            fecha_referencia=raw.received_at if raw else None,
        )
    except Exception as exc:  # noqa: BLE001
        report.action = "error"
        report.detail = f"extract_pick falló: {exc!r}"
        return report

    if new is None:
        report.action = "error"
        report.detail = "extract_pick devolvió None"
        return report

    report.new = _new_snapshot(new)
    if not new.es_apuesta:
        report.action = "rejected"
        report.detail = "la re-extracción resolvió que no es apuesta"
        if apply:
            pick.es_apuesta = False
            pick.metodo = "rejected+reext"
            await _supersede_legs(session, pick)
            session.add(pick)
        return report

    n_patas = len(new.patas)
    was_combinada = pick.es_combinada
    if apply:
        _apply_fields(pick, new)
        superseded = await _supersede_legs(session, pick)
        if n_patas >= 2:
            pick.es_combinada = True
            pick.mercado = "combinada"
            session.add(pick)
            await session.flush()
            await _persist_patas(
                session,
                pick,
                new,
                pick.informante_id or 0,
                pick.informante or (raw.channel_name if raw else ""),
                pick.es_reto,
            )
        else:
            pick.es_combinada = False
            session.add(pick)
        report.action = "repaired"
        report.detail = (
            f"combinada {was_combinada}->{n_patas >= 2}, "
            f"{superseded} patas viejas excluidas, {n_patas} nuevas"
        )
    else:
        report.action = "repaired"
        report.detail = (
            f"combinada {was_combinada}->{n_patas >= 2}, {n_patas} patas nuevas"
        )
    return report


async def repair_picks(
    pick_ids: list[int], *, apply: bool = False
) -> list[RepairReport]:
    """Repara las filas indicadas (padres/simples). Dry-run salvo
    `apply=True`; con apply se commitea al final."""
    reports: list[RepairReport] = []
    async with AsyncSessionLocal() as session:
        for pid in pick_ids:
            pick = await session.get(ParsedPick, pid)
            if pick is None:
                reports.append(RepairReport(pid, "skipped", "id inexistente"))
                continue
            reports.append(await repair_pick(session, pick, apply=apply))
        if apply:
            await session.commit()
            logger.info("[REPAIR] %s picks reprocesados y aplicados", len(pick_ids))
    return reports
