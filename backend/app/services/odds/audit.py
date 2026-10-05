"""Auditoría de consistencia de los snapshots almacenados.

Re-pasa `map_pick_choices` sobre cada pick que tiene filas de
`odds_snapshots` atribuidas (`parsed_pick_id`) y comprueba que la
opción que el mapeador elegiría HOY es la misma tripleta
(market/choice/group) que quedó guardada. Sirve como red de
seguridad: un cambio futuro en el mapeador, en el extractor o en un
parser puede dejar filas antiguas apuntando a la opción equivocada
sin que nadie lo note (pasó con doble oportunidad -> victoria seca y
con "gana 2-0" -> ganador; ambos se detectaron auditando a mano).

Clasificación por pick:
- `ok`: el mapeador casa el pick con una tripleta que el pick posee.
- `sin_map`: el mapeador devuelve None — el pick posee filas que ya
  no son compatibles con su mercado (regresión o atribución rota).
- `mal`: el mapeador casa con una tripleta DISTINTA a la guardada —
  la fila almacenada no es la que corresponde al pick.

Se corre como job semanal desde `lifecycle.py` y su resumen queda en
`record_loop_summary("odds_audit")`; `mal > 0` dispara push a admins.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds.compare import map_pick_choices
from app.services.results.base import record_loop_summary

# Máximo de casos de muestra en el resumen/log (el conteo es completo).
_SAMPLE_MAX = 5


@dataclass
class AuditReport:
    """Resultado de una pasada de auditoría sobre las filas atribuidas."""

    picks: int = 0
    ok: int = 0
    sin_map: int = 0
    mal: int = 0
    sin_evento: int = 0
    ejemplos_mal: list[str] = field(default_factory=list)
    ejemplos_sin_map: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Resumen compacto para `record_loop_summary`."""
        return {
            "picks": self.picks,
            "ok": self.ok,
            "sin_map": self.sin_map,
            "mal": self.mal,
            "sin_evento": self.sin_evento,
            "ejemplos_mal": self.ejemplos_mal,
            "ejemplos_sin_map": self.ejemplos_sin_map,
        }


async def audit_stored_snapshots(session: AsyncSession) -> AuditReport:
    """Verifica que cada fila atribuida siga casando con su pick.

    Una sola query de snapshots/picks/eventos — el volumen es
    administrable (cientos de filas, no millones) y se corre una vez
    por semana.
    """
    report = AuditReport()
    attributed = list(
        (
            await session.exec(
                select(OddsSnapshot).where(
                    OddsSnapshot.parsed_pick_id != None  # noqa: E711
                )
            )
        ).all()
    )
    if not attributed:
        return report

    # Tripleta que posee cada pick y eventos donde tiene filas.
    owned: dict[int, set[tuple[str, str, str | None]]] = {}
    pick_events: dict[int, set[str]] = {}
    event_ids: set[str] = set()
    for row in attributed:
        assert row.parsed_pick_id is not None
        owned.setdefault(row.parsed_pick_id, set()).add(
            (row.market_name, row.choice_name, row.choice_group)
        )
        pick_events.setdefault(row.parsed_pick_id, set()).add(row.event_ext_id)
        event_ids.add(row.event_ext_id)

    # Todas las filas de esos eventos (las no atribuidas también
    # cuentan: el mapeador ve el mercado completo del evento).
    rows_by_event: dict[str, list[OddsSnapshot]] = {}
    for row in (
        await session.exec(
            select(OddsSnapshot)
            .where(OddsSnapshot.event_ext_id.in_(event_ids))
            .order_by(OddsSnapshot.captured_at)
        )
    ).all():
        rows_by_event.setdefault(row.event_ext_id, []).append(row)

    events = {
        e.event_ext_id: e
        for e in (
            await session.exec(
                select(OddsEvent).where(OddsEvent.event_ext_id.in_(event_ids))
            )
        ).all()
    }
    picks = {
        p.id: p
        for p in (
            await session.exec(
                select(ParsedPick).where(ParsedPick.id.in_(owned.keys()))
            )
        ).all()
        if p.id is not None
    }

    for pick_id, triples in owned.items():
        report.picks += 1
        pick = picks.get(pick_id)
        label = f"pick {pick_id} ({pick.seleccion if pick else 'borrado'})"
        if pick is None:
            # Pick borrado: la fila queda huerfana — no es mapeo
            # incorrecto, pero conviene contarlo aparte.
            report.sin_evento += 1
            continue
        matched_ok = False
        mapped_none = True
        mapped_desc = ""
        for ext_id in pick_events.get(pick_id, ()):
            event = events.get(ext_id)
            rows = rows_by_event.get(ext_id, [])
            if event is None or not rows:
                continue
            matched = map_pick_choices(pick, event, rows)
            if matched is None:
                continue
            mapped_none = False
            mapped_desc = f"{matched.market_name}/{matched.choice_name}"
            if (
                matched.market_name,
                matched.choice_name,
                matched.choice_group,
            ) in triples:
                matched_ok = True
                break
        if matched_ok:
            report.ok += 1
        elif mapped_none:
            report.sin_map += 1
            if len(report.ejemplos_sin_map) < _SAMPLE_MAX:
                report.ejemplos_sin_map.append(f"{label}: {sorted(triples)[:1]}")
        else:
            report.mal += 1
            if len(report.ejemplos_mal) < _SAMPLE_MAX:
                report.ejemplos_mal.append(
                    f"{label}: guardado {sorted(triples)[:1]}, "
                    f"el mapeador da {mapped_desc}"
                )
    return report


async def run_odds_audit(session: AsyncSession) -> AuditReport:
    """Pasada completa: audita, registra el resumen y avisa si hay
    filas mal mapeadas. Pensada para el loop semanal de lifecycle."""
    report = await audit_stored_snapshots(session)
    record_loop_summary("odds_audit", report.as_dict())
    if report.mal:
        from app.services.notifications.push import notify_odds_audit_mismatch

        await notify_odds_audit_mismatch(report)
    return report
