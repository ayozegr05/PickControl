"""Tests de `_run_source` en `odds/historical_backfill.py`.

Cubre el orden de buckets (días recientes primero) y el contador
`sin_revisar`: picks a los que la pasada no llega porque la fuente se
quedó sin presupuesto de llamadas o la API falló — quedan pendientes
sin marca, a diferencia de `sin_fixture`/`sin_historial` (intentados).
"""

from datetime import datetime

import pytest

from app.models.parsed_pick import ParsedPick
from app.services.odds.historical_backfill import (
    BackfillReport,
    _run_source,
)

pytestmark = pytest.mark.asyncio


class _Fixture:
    home_name = "A"
    away_name = "B"
    start = datetime(2026, 9, 20)


class _FakeSource:
    """Fuente histórica mínima (duck-type de _HistorySource)."""

    name = "fakefeed"
    sport_ids = {"futbol": 1}

    def __init__(self, fixtures_return=None, choices_status="ok"):
        self.days_requested = []
        self._fixtures_return = fixtures_return
        self._choices_status = choices_status

    async def fixtures(self, http, sport_id, day):
        self.days_requested.append(day)
        if self._fixtures_return == "error":
            return None
        return list(self._fixtures_return or [])

    def match(self, fixtures, hint, dt, sofascore_id):
        return fixtures[0] if fixtures else None

    def ext_id(self, fixture):
        return "fake:1"

    def fixture_key(self, fixture):
        return "fk1"

    async def choices(self, http, fixture, sport, picks):
        return self._choices_status, []


def _pick(pid: int, day: int) -> ParsedPick:
    return ParsedPick(
        id=pid,
        es_apuesta=True,
        deporte="fútbol",
        evento="A vs B",
        seleccion="A gana",
        fecha_evento=datetime(2026, 9, day),
    )


async def test_dias_recientes_primero(session):
    """Los buckets se recorren de día más nuevo a más viejo — los
    picks frescos se resuelven antes de agotar la paga diaria."""
    src = _FakeSource(fixtures_return=[])
    picks = [_pick(1, 1), _pick(2, 20)]
    report = BackfillReport()

    await _run_source(src, picks, set(), set(), session, None, False, report)

    # Primeras llamadas: el bucket de sept-20 pide 19/20/21 antes de
    # que el de sept-01 pida nada.
    assert src.days_requested[0].day == 19
    assert src.days_requested[1].day == 20
    assert src.days_requested[2].day == 21
    assert report.sin_fixture == 2
    assert report.sin_revisar == 0


async def test_error_api_cuenta_sin_revisar(session):
    """Si la API falla al listar el día, esos picks cuentan como
    `sin_revisar` (no `sin_fixture`) y no llevan marca."""
    src = _FakeSource(fixtures_return="error")
    picks = [_pick(1, 20), _pick(2, 20)]
    report = BackfillReport()

    await _run_source(src, picks, set(), set(), session, None, False, report)

    assert report.sin_revisar == 2
    assert report.sin_fixture == 0


async def test_presupuesto_en_curvas_cuenta_sin_revisar(session):
    """Presupuesto agotado en la fase de curvas: los picks casados
    que no llegaron a descargar cuentan como `sin_revisar`."""
    src = _FakeSource(fixtures_return=[_Fixture()], choices_status="budget")
    picks = [_pick(1, 20), _pick(2, 20)]
    report = BackfillReport()

    await _run_source(src, picks, set(), set(), session, None, False, report)

    assert report.con_fixture == 2
    assert report.sin_revisar == 2
    assert report.filas == 0


class _EarlyFixture(_Fixture):
    """El partido real se jugó el 4/sep, no el 20 que guarda el pick
    (la fecha guardada era la del repost del boleto)."""

    start = datetime(2026, 9, 4)


async def test_fixture_en_otra_fecha_corrige_fecha_evento(session):
    """El fixture casado por una fuente con sondeo de fecha (el CSV
    de temporada entera) corrige `fecha_evento` del pick con la fecha
    real del partido — la guardada era la del repost."""
    src = _FakeSource(fixtures_return=[_EarlyFixture()])
    pick = _pick(1, 20)  # guardada: 20/sep; partido real: 4/sep
    assert pick.fecha_evento != _EarlyFixture.start

    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert report.con_fixture == 1
    assert report.fechas_corregidas == 1
    assert pick.fecha_evento == _EarlyFixture.start


async def test_fixture_misma_fecha_no_corrige(session):
    src = _FakeSource(fixtures_return=[_Fixture()])
    pick = _pick(1, 20)
    pick.fecha_evento = _Fixture.start  # ya correcta

    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert report.fechas_corregidas == 0
    assert pick.fecha_evento == _Fixture.start
