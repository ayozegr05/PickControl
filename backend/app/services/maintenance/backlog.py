"""Barrido de picks pendientes FUERA de la ventana de verificación.

El ciclo normal (`verify_pending_picks`, cada ~3 h) solo reintenta
eventos de los últimos `_MAX_VERIFICATION_AGE` días — el resto quedaba
pendiente para siempre (patas de combinadas viejas sobre todo, que
además bloquean al padre). Este barrido los revisa una vez al día
desde `lifecycle.py`; `scripts/verify_backlog.py` es el wrapper CLI.

Los resultados históricos son estáticos: un pick resoluble solo se
pregunta una vez al día y los `missed` definitivos de cada provider
impiden re-quemar llamadas. El ritmo `delay` protege los límites por
minuto de las APIs gratuitas (TheSportsDB ~30 req/min).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlmodel import select

from app.core.dates import utc_now
from app.db.postgres import AsyncSessionLocal
from app.models.informante import Informante  # noqa: F401  (FK de ParsedPick)
from app.models.parsed_pick import ParsedPick
from app.models.pick import Pick  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401  (FKs de TelegramRawMessage)
from app.services.results.base import record_loop_summary
from app.services.results.verifier import (
    _MAX_VERIFICATION_AGE,
    _get_providers,
    _settle_combinadas,
    probe_event_date,
    verify_pick,
)


async def run_backlog_verification(
    apply: bool,
    delay: float,
    older_than: timedelta | None = _MAX_VERIFICATION_AGE,
    log=print,
) -> dict:
    """Intenta liquidar los pendientes más viejos que `older_than`
    (None = todos los pendientes, útil para un barrido manual total).

    Devuelve el resumen {intentados, resueltos, padres_liquidados}."""
    providers = await _get_providers()
    if not providers:
        return {
            "intentados": 0,
            "resueltos": 0,
            "errores": 0,
            "fechas_corregidas": 0,
            "padres_liquidados": 0,
        }

    async with AsyncSessionLocal() as session:
        query = (
            select(ParsedPick)
            .where(ParsedPick.es_apuesta == True)  # noqa: E712
            .where(ParsedPick.acierto == None)  # noqa: E711
            .where(ParsedPick.anulada == False)  # noqa: E712
            .where(ParsedPick.es_combinada == False)  # noqa: E712
            .where(ParsedPick.fecha_evento != None)  # noqa: E711
        )
        if older_than is not None:
            query = query.where(ParsedPick.fecha_evento < utc_now() - older_than)
        result = await session.exec(query)
        pending = list(result.all())
        # Recientes primero, igual que el ciclo normal.
        pending.sort(key=lambda p: p.fecha_evento, reverse=True)
        log(f"Pendientes a intentar: {len(pending)}")

        resolved = 0
        errores = 0
        fechas_corregidas = 0
        for i, pick in enumerate(pending):
            if i:
                await asyncio.sleep(delay)
            try:
                acierto, anulada = await verify_pick(pick, providers)
            except Exception as exc:  # un pick envenenado no aborta el barrido
                errores += 1
                log(f"  id={pick.id} error: {exc}")
                continue
            if acierto is None and not anulada:
                # La fecha guardada puede estar mal (el extractor a
                # veces toma la del mensaje, no la del partido): sondeo
                # ±7 días; si el fixture aparece con ambos lados claros
                # se corrige fecha_evento y se reintenta una vez.
                new_date = await probe_event_date(pick, providers)
                if new_date is None:
                    continue
                pick.fecha_evento = new_date
                session.add(pick)
                fechas_corregidas += 1
                log(f"  id={pick.id} fecha corregida -> {new_date.date()}")
                try:
                    acierto, anulada = await verify_pick(pick, providers)
                except Exception as exc:  # noqa: BLE001
                    errores += 1
                    log(f"  id={pick.id} error tras corregir fecha: {exc}")
                    continue
                if acierto is None and not anulada:
                    continue
            resolved += 1
            tag = "ANULADA" if anulada else ("ACIERTO" if acierto else "FALLO")
            log(f"  id={pick.id} '{pick.seleccion}' -> {tag}")
            if apply:
                pick.acierto = acierto
                pick.anulada = anulada
                pick.verificado_por = "auto"
                pick.verificado_at = utc_now()
                session.add(pick)

        settled: list = []
        if apply:
            settled = await _settle_combinadas(session)
            await session.commit()

    stats = {
        "intentados": len(pending),
        "resueltos": resolved,
        "errores": errores,
        "fechas_corregidas": fechas_corregidas,
        "padres_liquidados": len(settled),
    }
    if apply:
        record_loop_summary("verify_backlog", stats)
    return stats
