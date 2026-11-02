"""Auditoría de consistencia de los snapshots almacenados.

Re-pasa `map_pick_choices` sobre cada pick que tiene filas de
`odds_snapshots` atribuidas (`parsed_pick_id`) y comprueba que la
opción que el mapeador elegiría HOY es la misma tripleta
(market/choice/group) que quedó guardada. Sirve como red de
seguridad: un cambio futuro en el mapeador, en el extractor o en un
parser puede dejar filas antiguas apuntando a la opción equivocada
sin que nadie lo note (pasó con doble oportunidad -> victoria seca y
con "gana 2-0" -> ganador; ambos se detectaron auditando a mano).

Solo evalúa las filas escritas por FUENTES DE BACKFILL
(`_BACKFILL_PROVIDERS`): son las únicas para las que la atribución
afirma "este pick quedó resuelto". Las filas del snapshotter en vivo
(espn, sofascore...) llevan `parsed_pick_id` solo como traza de
quién disparó la captura — el pick puede poseer un bloque entero de
mercados que no cubre su mercado, y eso no es un error sino un hueco
de cobertura (se contabiliza aparte en `sin_backfill`).

Clasificación por pick con filas de backfill:
- `ok`: el mapeador casa el pick con una tripleta que el pick posee.
- `sin_map`: el mapeador devuelve None — el pick posee filas de
  backfill que ya no son compatibles con su mercado (regresión o
  atribución rota).
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

    # Picks con filas atribuidas (todas las fuentes).
    picks: int = 0
    # De los picks con filas de backfill (los que el embudo cuenta
    # "resueltos"): cuántos sigue casando el mapeador hoy.
    ok: int = 0
    sin_map: int = 0
    mal: int = 0
    # Picks sin filas de backfill (solo traza del snapshotter):
    # informativo — su mercado puede no estar capturado, pero nadie
    # afirmó que estuvieran resueltos.
    sin_backfill: int = 0
    # Fila atribuida a un pick que ya no existe.
    sin_evento: int = 0
    ejemplos_mal: list[str] = field(default_factory=list)
    ejemplos_sin_map: list[str] = field(default_factory=list)
    # --- datos internos para la sanación (run_odds_audit) ---
    # Picks por categoría y, para cada `mal`, (event_ext_id, tripleta
    # correcta) que el mapeador eligió — sirve para re-sellar la fila
    # buena si hay alguna libre en el evento.
    mal_ids: list[int] = field(default_factory=list)
    sin_map_ids: list[int] = field(default_factory=list)
    mal_targets: dict[int, tuple[str, tuple[str, str, str | None]]] = field(
        default_factory=dict
    )
    desatribuidas: int = 0
    reselladas: int = 0

    def as_dict(self) -> dict:
        """Resumen compacto para `record_loop_summary`."""
        return {
            "picks": self.picks,
            "ok": self.ok,
            "sin_map": self.sin_map,
            "mal": self.mal,
            "sin_backfill": self.sin_backfill,
            "sin_evento": self.sin_evento,
            "desatribuidas": self.desatribuidas,
            "reselladas": self.reselladas,
            "ejemplos_mal": self.ejemplos_mal,
            "ejemplos_sin_map": self.ejemplos_sin_map,
        }


async def audit_stored_snapshots(session: AsyncSession) -> AuditReport:
    """Verifica que cada fila de backfill atribuida siga casando con
    su pick.

    Una sola query de snapshots/picks/eventos — el volumen es
    administrable (cientos de filas, no millones) y se corre una vez
    por semana.
    """
    from app.services.odds.historical_backfill import _BACKFILL_PROVIDERS

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

    # Tripletas que posee cada pick, separadas por tipo de fuente, y
    # eventos donde tiene filas.
    owned_bf: dict[int, set[tuple[str, str, str | None]]] = {}
    owned_any: dict[int, set[tuple[str, str, str | None]]] = {}
    pick_events: dict[int, set[str]] = {}
    event_ids: set[str] = set()
    for row in attributed:
        assert row.parsed_pick_id is not None
        triple = (row.market_name, row.choice_name, row.choice_group)
        owned_any.setdefault(row.parsed_pick_id, set()).add(triple)
        if row.provider in _BACKFILL_PROVIDERS:
            owned_bf.setdefault(row.parsed_pick_id, set()).add(triple)
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
                select(ParsedPick).where(ParsedPick.id.in_(owned_any.keys()))
            )
        ).all()
        if p.id is not None
    }

    for pick_id, triples in owned_any.items():
        report.picks += 1
        pick = picks.get(pick_id)
        label = f"pick {pick_id} ({pick.seleccion if pick else 'borrado'})"
        if pick is None:
            # Pick borrado: la fila queda huerfana — no es mapeo
            # incorrecto, pero conviene contarlo aparte.
            report.sin_evento += 1
            continue
        bf_triples = owned_bf.get(pick_id)
        if not bf_triples:
            # Solo traza del snapshotter: el embudo no lo cuenta
            # resuelto — su comparación se evalúa con todas las
            # filas del evento al comparar, no con la propiedad.
            report.sin_backfill += 1
            continue
        matched_ok = False
        mapped_none = True
        mapped_desc = ""
        mapped_target: tuple[str, tuple[str, str, str | None]] | None = None
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
            if mapped_target is None:
                mapped_target = (
                    ext_id,
                    (matched.market_name, matched.choice_name, matched.choice_group),
                )
            if (
                matched.market_name,
                matched.choice_name,
                matched.choice_group,
            ) in bf_triples:
                matched_ok = True
                break
        if matched_ok:
            report.ok += 1
        elif mapped_none:
            report.sin_map += 1
            report.sin_map_ids.append(pick_id)
            if len(report.ejemplos_sin_map) < _SAMPLE_MAX:
                report.ejemplos_sin_map.append(f"{label}: {sorted(bf_triples)[:1]}")
        else:
            report.mal += 1
            report.mal_ids.append(pick_id)
            if mapped_target:
                report.mal_targets[pick_id] = mapped_target
            if len(report.ejemplos_mal) < _SAMPLE_MAX:
                report.ejemplos_mal.append(
                    f"{label}: guardado {sorted(bf_triples)[:1]}, "
                    f"el mapeador da {mapped_desc}"
                )
    return report


async def _heal_attributions(
    session: AsyncSession, report: AuditReport
) -> tuple[int, int]:
    """Des-atribuye las filas de backfill de picks `mal`/`sin_map` y
    re-sella la fila correcta cuando hay una libre en el evento.

    `parsed_pick_id` en una fila de backfill afirma "esta fila resuelve
    a ese pick" — si el mapeador ya no la casa, la afirmación es falsa
    y se corrige (la fila sigue existiendo como dato de mercado). El
    pick queda de nuevo "no resuelto" en el embudo... salvo que la
    tripleta correcta exista ya libre en el mismo evento, en cuyo caso
    se le re-sella y queda resuelto bien.

    Devuelve (desatribuidas, reselladas)."""
    from app.services.odds.historical_backfill import _BACKFILL_PROVIDERS

    cleared_ids = report.mal_ids + report.sin_map_ids
    if not cleared_ids:
        return 0, 0
    stale = list(
        (
            await session.exec(
                select(OddsSnapshot)
                .where(OddsSnapshot.parsed_pick_id.in_(cleared_ids))
                .where(OddsSnapshot.provider.in_(_BACKFILL_PROVIDERS))
            )
        ).all()
    )
    for row in stale:
        row.parsed_pick_id = None
        session.add(row)

    restamped = 0
    for pick_id, (ext_id, triple) in report.mal_targets.items():
        market, choice, group = triple
        # Solo filas de backfill: son las que cuentan como "resuelto"
        # en el embudo (`_resolved_pick_ids` filtra por provider).
        free = list(
            (
                await session.exec(
                    select(OddsSnapshot)
                    .where(OddsSnapshot.event_ext_id == ext_id)
                    .where(OddsSnapshot.provider.in_(_BACKFILL_PROVIDERS))
                    .where(OddsSnapshot.market_name == market)
                    .where(OddsSnapshot.choice_name == choice)
                    .where(
                        (OddsSnapshot.choice_group == group)
                        if group is not None
                        else (OddsSnapshot.choice_group == None)  # noqa: E711
                    )
                    .where(OddsSnapshot.parsed_pick_id == None)  # noqa: E711
                    .limit(1)
                )
            ).all()
        )
        if free:
            free[0].parsed_pick_id = pick_id
            session.add(free[0])
            restamped += 1
    return len(stale), restamped


async def run_odds_audit(session: AsyncSession, *, heal: bool = True) -> AuditReport:
    """Pasada completa: audita, sana las atribuciones rotas (los picks
    afectados vuelven a la cola o quedan re-sellados a su fila real),
    registra el resumen y avisa si hubo filas mal mapeadas."""
    report = await audit_stored_snapshots(session)
    if heal and (report.mal or report.sin_map):
        report.desatribuidas, report.reselladas = await _heal_attributions(
            session, report
        )
        await session.commit()
    record_loop_summary("odds_audit", report.as_dict())
    if report.mal:
        from app.services.notifications.push import notify_odds_audit_mismatch

        await notify_odds_audit_mismatch(report)
    return report
