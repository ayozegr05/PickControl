"""Tests de la auditoría semanal de consistencia (`odds/audit.py`).

La auditoría re-pasa `map_pick_choices` sobre cada pick con filas
atribuidas y verifica que la tripleta guardada sigue siendo la que
el mapeador elegiría hoy: ok / sin_map / mal / sin_evento.
"""

from __future__ import annotations

import pytest

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds.audit import audit_stored_snapshots, run_odds_audit


def _pick(pick_id: int, **kwargs) -> ParsedPick:
    base = dict(
        id=pick_id,
        raw_message_id=1,
        es_apuesta=True,
        deporte="fútbol",
        evento="Malaga - Deportivo",
    )
    base.update(kwargs)
    return ParsedPick(**base)


def _snap(
    pick_id: int | None,
    market: str,
    choice: str,
    group: str | None = None,
    cuota: float = 2.0,
    provider: str = "betexplorer",
) -> OddsSnapshot:
    return OddsSnapshot(
        provider=provider,
        event_ext_id="betexplorer:evt1",
        market_name=market,
        choice_name=choice,
        choice_group=group,
        cuota=cuota,
        parsed_pick_id=pick_id,
    )


async def _seed(session, *objects) -> None:
    for obj in objects:
        session.add(obj)
    await session.commit()


@pytest.mark.asyncio
async def test_audit_ok(session):
    """La fila atribuida es exactamente la que el mapeador elige hoy."""
    event = OddsEvent(
        event_ext_id="betexplorer:evt1",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
    )
    pick = _pick(1, mercado="ganador", seleccion="Malaga gana", cuota=2.0)
    await _seed(session, event, pick, _snap(1, "Full time", "1"))

    report = await audit_stored_snapshots(session)

    assert report.picks == 1
    assert report.ok == 1
    assert report.mal == 0
    assert report.sin_map == 0


@pytest.mark.asyncio
async def test_audit_mal(session):
    """La fila guardada no es la que el mapeador elige hoy: el pick
    O/U 2.5 posee una fila de Full time pero el mercado O/U existe."""
    event = OddsEvent(
        event_ext_id="betexplorer:evt1",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
    )
    pick = _pick(
        2,
        mercado="over/under goles",
        seleccion="más de 2.5 goles",
        linea=2.5,
        cuota=1.9,
    )
    await _seed(
        session,
        event,
        pick,
        # Fila mal atribuida al pick (bug de propiedad antiguo).
        _snap(2, "Full time", "2", cuota=2.25),
        # La opción correcta existe en el mismo evento.
        _snap(None, "Match goals", "Over", "2.5", cuota=1.95),
    )

    report = await audit_stored_snapshots(session)

    assert report.picks == 1
    assert report.mal == 1
    assert report.ok == 0
    assert report.ejemplos_mal


@pytest.mark.asyncio
async def test_audit_sin_map(session):
    """El pick posee una fila pero su mercado ya no es mapeable
    (doble oportunidad con proveedor que solo publica 1X2 -> None)."""
    event = OddsEvent(
        event_ext_id="betexplorer:evt1",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
    )
    pick = _pick(
        3,
        mercado="doble oportunidad",
        seleccion="Malaga o empate",
        cuota=1.5,
    )
    await _seed(session, event, pick, _snap(3, "Full time", "1"))

    report = await audit_stored_snapshots(session)

    assert report.picks == 1
    assert report.sin_map == 1
    assert report.mal == 0
    assert report.ejemplos_sin_map


@pytest.mark.asyncio
async def test_audit_pick_borrado_es_sin_evento(session):
    """Fila atribuida a un pick que ya no existe: ni ok ni mal —
    se cuenta aparte (huerfana), no ensucia el conteo de regresiones."""
    await _seed(session, _snap(999, "Full time", "1"))

    report = await audit_stored_snapshots(session)

    assert report.picks == 1
    assert report.sin_evento == 1
    assert report.mal == 0
    assert report.ok == 0


@pytest.mark.asyncio
async def test_audit_sin_filas_atribuidas(session):
    report = await audit_stored_snapshots(session)
    assert report.picks == 0


@pytest.mark.asyncio
async def test_audit_solo_snapshotter_es_informativo(session):
    """Filas del snapshotter en vivo (espn...) atribuidas a un pick
    cuyo mercado no está capturado: la traza "quién disparó" no
    afirma resolución — cuenta en `sin_backfill`, no en sin_map/mal."""
    event = OddsEvent(
        event_ext_id="espn:evt9",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
    )
    pick = _pick(
        7,
        mercado="over/under corners",
        seleccion="menos de 11 corners",
        linea=11.0,
    )
    snap = _snap(7, "1st half", "1", provider="espn")
    snap.event_ext_id = "espn:evt9"
    await _seed(session, event, pick, snap)

    report = await audit_stored_snapshots(session)

    assert report.picks == 1
    assert report.sin_backfill == 1
    assert report.mal == 0
    assert report.sin_map == 0
    assert report.ok == 0


@pytest.mark.asyncio
async def test_audit_mixta_snapshotter_y_backfill(session):
    """Pick con fila de backfill correcta + traza espn del mismo
    evento: la de backfill manda — ok aunque la traza sea de otro
    mercado."""
    event = OddsEvent(
        event_ext_id="betexplorer:evt1",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
    )
    pick = _pick(8, mercado="ganador", seleccion="Malaga gana")
    await _seed(
        session,
        event,
        pick,
        _snap(8, "Full time", "1", provider="betexplorer"),
        _snap(8, "1st half", "2", provider="espn"),
    )

    report = await audit_stored_snapshots(session)
    assert report.ok == 1
    assert report.sin_backfill == 0


@pytest.mark.asyncio
async def test_run_odds_audit_registra_y_avisa(session, monkeypatch):
    """`run_odds_audit` persiste el resumen en provider_state y, si
    hay filas mal mapeadas, llama al push de admins."""
    event = OddsEvent(
        event_ext_id="betexplorer:evt1",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
    )
    pick = _pick(
        4,
        mercado="over/under goles",
        seleccion="más de 2.5 goles",
        linea=2.5,
    )
    await _seed(
        session,
        event,
        pick,
        _snap(4, "Full time", "2"),
        _snap(None, "Match goals", "Over", "2.5"),
    )

    calls: list = []

    async def fake_notify(report) -> int:
        calls.append(report)
        return 0

    monkeypatch.setattr(
        "app.services.notifications.push.notify_odds_audit_mismatch",
        fake_notify,
    )

    report = await run_odds_audit(session)

    assert report.mal == 1
    assert len(calls) == 1
    # El resumen quedó registrado para /system/providers.
    from app.services.results.base import loop_summary

    summary = loop_summary("odds_audit")
    assert summary is not None
    assert summary["mal"] == 1
    assert summary["picks"] == 1


@pytest.mark.asyncio
async def test_run_odds_audit_limpio_no_avisa(session, monkeypatch):
    """Sin filas mal mapeadas no hay push."""
    event = OddsEvent(
        event_ext_id="betexplorer:evt1",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
    )
    pick = _pick(5, mercado="ganador", seleccion="Malaga gana")
    await _seed(session, event, pick, _snap(5, "Full time", "1"))

    monkeypatch.setattr(
        "app.services.notifications.push.notify_odds_audit_mismatch",
        lambda report: pytest.fail("no debe llamarse con mal=0"),
    )
    report = await run_odds_audit(session)
    assert report.ok == 1
    assert report.mal == 0


@pytest.mark.asyncio
async def test_audit_respeta_filas_de_otros_proveedores(session):
    """Un pick puede tener filas atribuidas en varios providers del
    mismo evento — todas las del evento alimentan el mapeo, pero la
    propiedad solo cuenta las suyas."""
    event = OddsEvent(
        event_ext_id="betexplorer:evt1",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
    )
    pick = _pick(
        6,
        mercado="over/under goles",
        seleccion="más de 2.5 goles",
        linea=2.5,
    )
    await _seed(
        session,
        event,
        pick,
        _snap(6, "Match goals", "Over", "2.5", cuota=1.95),
    )

    report = await audit_stored_snapshots(session)
    assert report.ok == 1
