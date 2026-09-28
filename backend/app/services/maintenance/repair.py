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

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.postgres import AsyncSessionLocal
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.results.base import fold_name
from app.services.telegram.pick_extractor import ExtractedPick, extract_pick
from app.services.telegram.processor import _persist_patas

logger = get_logger("app.maintenance.repair")

# Ventana del "par" foto-boleto + texto del tipster (la misma que usa
# processor._PAIR_WINDOW): al re-extraer un pick cuyo raw es el texto
# se reutiliza el OCR del slip de al lado si existe, y viceversa.
_PAIR_WINDOW = timedelta(minutes=10)

# Líneas de publi/afiliación del pie ("[REGÍSTRATE Y GANA 200€](t.ly/x)"):
# en la re-extracción hacen que el LLM etiquete el pick entero como
# anuncio. Si la primera pasada rechaza, se reintenta sin ellas.
_PROMO_LINE = re.compile(
    r"https?://|www\.|t\.me/|\]\(|reg[ií]strate|gratis|bono|afiliad",
    re.IGNORECASE,
)


def _strip_promo_lines(text: str) -> str:
    return "\n".join(
        ln for ln in text.splitlines() if not _PROMO_LINE.search(ln)
    ).strip()


# Último recurso determinista: el LLM rechazó incluso sin publi, pero el
# texto tiene una línea de apuesta inequívoca ("GANA DE JONG Y +7,5
# JUEGOS EN EL 1° SET"). La selección se toma verbatim del mensaje —
# nada se inventa (cuota/rival ausentes quedan a None).
_BET_LINE = re.compile(r"\bgana\w*\b", re.IGNORECASE)
_PROMO_WORD = re.compile(
    r"€|euros?|reg[ií]str|juega\s+\d|bono|click|aqu[ií]|desde", re.IGNORECASE
)


def _rule_fallback_pick(source: str, pick: ParsedPick) -> Optional[ExtractedPick]:
    """Reconstruye el pick por reglas cuando el LLM insiste en que el
    mensaje no es apuesta pero el texto guardado contiene la selección.

    Solo se usa en reparación de filas ya existentes: confianza baja y
    `metodo` propio para que quede auditable.
    """
    from app.services.telegram.pick_extractor import (  # noqa: PLC0415
        _ensure_combinada_shape,
        _looks_like_bet,
    )

    if not _looks_like_bet(source):
        return None
    candidates = []
    for ln in source.splitlines():
        if not _BET_LINE.search(ln) or _PROMO_WORD.search(ln):
            continue
        # Corta desde "gana" en adelante: lo anterior es deco markdown.
        m = _BET_LINE.search(ln)
        sel = ln[m.start() :].strip().strip("*_# ")
        if sel:
            candidates.append(sel)
    if not candidates:
        return None
    fallback = ExtractedPick(
        es_apuesta=True,
        deporte=pick.deporte,
        seleccion=max(candidates, key=len),
        stake=pick.stake,
        linea=pick.linea,
        metodo="reext-rules",
        confianza=0.4,
    )
    return _ensure_combinada_shape(fallback)


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


async def _supersede_legs(
    session, parent: ParsedPick
) -> tuple[list[ParsedPick], dict[tuple[int, str], ParsedPick]]:
    """Marca las patas viejas como excluidas: `anulada=True` las saca
    del reparto de `settle_combinada` y `es_apuesta=False` las saca del
    escaneo de pendientes. La fila se conserva para auditoría.

    Devuelve (patas, veredictos): el mapa orden+selección -> pata
    capturado ANTES de pisar `anulada`, para que las patas nuevas
    idénticas puedan heredar el veredicto de su gemela (evita patas
    huérfanas eternamente pendientes tras una re-extracción)."""
    legs = list(
        (
            await session.exec(
                select(ParsedPick).where(ParsedPick.combinada_id == parent.id)
            )
        )
        .scalars()
        .all()
    )
    verdicts: dict[tuple[int, str], ParsedPick] = {}
    for leg in legs:
        if leg.acierto is not None or leg.anulada:
            verdicts[(leg.orden or 0, fold_name(leg.seleccion or ""))] = leg
        if leg.es_apuesta or not leg.anulada:
            leg.es_apuesta = False
            leg.anulada = True
            session.add(leg)
    return legs, verdicts


async def _inherit_leg_verdicts(
    session,
    parent: ParsedPick,
    verdicts: dict[tuple[int, str], ParsedPick],
) -> int:
    """Patas nuevas idénticas a una vieja ya cerrada heredan su veredicto.

    La re-extracción regenera el boleto: si la pata nueva coincide con
    una anterior por `orden` + selección (plegada) y la anterior ya
    estaba cerrada (acierto o anulada), es literalmente la misma
    apuesta — sin la herencia quedaría pendiente para siempre cuando
    su extracción es peor (sin evento)."""
    if not verdicts:
        return 0
    await session.flush()  # las patas nuevas aún están pendientes de flush
    new_legs = list(
        (
            await session.exec(
                select(ParsedPick)
                .where(ParsedPick.combinada_id == parent.id)
                .where(ParsedPick.es_apuesta == True)  # noqa: E712
            )
        )
        .scalars()
        .all()
    )
    inherited = 0
    for leg in new_legs:
        twin = verdicts.get((leg.orden or 0, fold_name(leg.seleccion or "")))
        if twin is None:
            continue
        leg.acierto = twin.acierto
        leg.anulada = twin.anulada
        leg.motivo_anulada = twin.motivo_anulada
        leg.verificado_por = twin.verificado_por
        leg.verificado_at = twin.verificado_at
        leg.verificado_provider = twin.verificado_provider
        session.add(leg)
        inherited += 1
    return inherited


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

    async def _extract(src: str) -> Optional[ExtractedPick]:
        return await extract_pick(
            src,
            settings.openai_api_key,
            informante=raw.channel_name if raw else None,
            fecha_referencia=raw.received_at if raw else None,
        )

    rules_fallback = False
    try:
        new = await _extract(source)
        if new is not None and not new.es_apuesta:
            # Rechazo con publi presente: el pie de afiliado puede haber
            # convencido al LLM de que es un anuncio — reintento limpio.
            stripped = _strip_promo_lines(source)
            if stripped != source:
                retry = await _extract(stripped)
                if retry is not None and retry.es_apuesta:
                    new = retry
            else:
                # Sin publi que quitar solo hubo UNA lectura: el LLM es
                # no determinista (mismo texto, veredictos distintos), así
                # que el rechazo exige una segunda confirmación.
                retry = await _extract(source)
                if retry is not None and retry.es_apuesta:
                    new = retry
            if not new.es_apuesta:
                # Ni limpio lo reconoce: si el texto tiene una línea de
                # apuesta inequívoca se reconstruye por reglas (verbatim,
                # sin inventar cuota ni rival).
                fallback = _rule_fallback_pick(stripped, pick)
                if fallback is not None and fallback.es_apuesta:
                    new = fallback
                    rules_fallback = True
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
            await _supersede_legs(session, pick)  # patas excluidas, sin herencia
            session.add(pick)
        return report

    n_patas = len(new.patas)
    was_combinada = pick.es_combinada
    if apply:
        _apply_fields(pick, new)
        superseded_legs, leg_verdicts = await _supersede_legs(session, pick)
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
        inherited = await _inherit_leg_verdicts(session, pick, leg_verdicts)
        report.action = "repaired"
        report.detail = (
            f"combinada {was_combinada}->{n_patas >= 2}, "
            f"{len(superseded_legs)} patas viejas excluidas, {n_patas} nuevas, "
            f"{inherited} veredictos heredados"
            + (" [vía reglas: LLM rechazó]" if rules_fallback else "")
        )
    else:
        report.action = "repaired"
        report.detail = (
            f"combinada {was_combinada}->{n_patas >= 2}, {n_patas} patas nuevas"
            + (" [vía reglas: LLM rechazó]" if rules_fallback else "")
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


# --- Ciclo periódico de autorregulación -------------------------------------
#
# Detecta filas pendientes con el mismo diagnóstico que el "cubo 1"
# original y las re-extrae solas: combinadas con patas de ruido, eventos
# que son el torneo en vez del cruce, "1X" clasificado como hándicap.

# Palabra de competición sin cruce ("CHALLENGER BIELLA", "WTA
# GUADALAJARA"): el extractor guardó el torneo como si fuera el evento.
_COMPETITION_WORD = re.compile(
    r"\b(?:chall(?:enger)?|atp|wta|itf|copa|liga|league|champions|"
    r"euroleague|nba|torneo|open|masters|premier|bundesliga|"
    r"eredivisie|serie\s+a|ligue|mls|europa\s+league)\b",
    re.IGNORECASE,
)
_CROSS = re.compile(r"\bvs\.?\b|\s-\s", re.IGNORECASE)
# Selección "1X"/"X2" guardada como hándicap: es doble oportunidad.
_DC_IN_HANDICAP = re.compile(r"\b(?:1x|x2)\b", re.IGNORECASE)


def _tournament_only(evento: Optional[str]) -> bool:
    return bool(
        evento and _COMPETITION_WORD.search(evento) and not _CROSS.search(evento)
    )


def _pick_needs_repair(pick: ParsedPick, active_legs: list[ParsedPick]) -> bool:
    """Señales conservadoras de extracción rota en una fila pendiente.

    Solo disparan cuando el dato guardado es claramente defectuoso —
    una pata con `evento` a NULL no dispara (puede heredar del padre);
    el objetivo es NO re-extraer picks sanos (cada re-extracción cuesta
    una llamada LLM y puede degradar un dato bueno).
    """
    if pick.es_combinada:
        # Combinada degenerada: menos de 2 patas activas (ruido o
        # extracción a medias del boleto).
        if len(active_legs) < 2:
            return True
        if _tournament_only(pick.evento):
            return True
        # Pata cuyo "evento" es el torneo — el cruce se perdió.
        if any(_tournament_only(leg.evento) for leg in active_legs):
            return True
    else:
        if _tournament_only(pick.evento):
            return True
        if (
            pick.mercado
            and "hándicap" in pick.mercado.lower()
            and _DC_IN_HANDICAP.search(pick.seleccion or "")
        ):
            return True
    return False


async def find_repair_candidates(session, limit: int = 10) -> list[int]:
    """Ids de picks pendientes (padres o simples, nunca patas) que
    pintan a extracción rota. Acotado por `limit`: el LLM no es gratis
    y una pasada no tiene por qué drenar toda la cola."""
    pending = (
        (
            await session.exec(
                select(ParsedPick)
                .where(ParsedPick.es_apuesta == True)  # noqa: E712
                .where(ParsedPick.acierto.is_(None))  # noqa: E711
                .where(ParsedPick.anulada == False)  # noqa: E712
            )
        )
        .scalars()
        .all()
    )
    # Padres pendientes (las patas nunca se reparan sueltas).
    parents = [p for p in pending if p.combinada_id is None]
    # Las patas se consultan TODAS, no solo pendientes: si el padre está
    # pendiente pero sus patas ya resueltas, no es una combinada
    # degenerada — solo le falta el settle.
    combinada_parents = [p.id for p in parents if p.es_combinada]
    legs_by_parent: dict[int, list[ParsedPick]] = {}
    if combinada_parents:
        all_legs = (
            (
                await session.exec(
                    select(ParsedPick).where(
                        ParsedPick.combinada_id.in_(combinada_parents)
                    )
                )
            )
            .scalars()
            .all()
        )
        for leg in all_legs:
            if leg.es_apuesta and not leg.anulada and leg.combinada_id:
                legs_by_parent.setdefault(leg.combinada_id, []).append(leg)
    candidates = [
        p.id
        for p in parents
        if p.id is not None and _pick_needs_repair(p, legs_by_parent.get(p.id, []))
    ]
    return candidates[:limit]


async def run_repair_cycle(limit: int = 10) -> list[RepairReport]:
    """Pasada periódica de autorregulación: busca filas sospechosas y
    las re-extrae desde su raw guardado. Corre desde el loop de rescue
    (mismo intervalo); sin candidatos no gasta ni una llamada LLM."""
    async with AsyncSessionLocal() as session:
        ids = await find_repair_candidates(session, limit)
    if not ids:
        return []
    logger.info("[REPAIR] Ciclo: %s picks sospechosos — re-extrayendo", len(ids))
    reports = await repair_picks(ids, apply=True)
    for r in reports:
        logger.info("[REPAIR] #%s [%s] %s", r.pick_id, r.action, r.detail)
    return reports
