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
from app.services.odds.oddspapi import HistoricalChoice, MarketDef, PricePoint
from app.services.results.base import is_missed

pytestmark = pytest.mark.asyncio


class _Fixture:
    home_name = "A"
    away_name = "B"
    start = datetime(2026, 9, 20)


class _FakeSource:
    """Fuente histórica mínima (duck-type de _HistorySource)."""

    name = "fakefeed"
    sport_ids = {"futbol": 1}

    def __init__(self, fixtures_return=None, choices_status="ok", choices=None):
        self.days_requested = []
        self._fixtures_return = fixtures_return
        self._choices_status = choices_status
        self._choices = choices or []

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
        return self._choices_status, list(self._choices)


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


class _NoPropSource(_FakeSource):
    """Fuente que no soporta props de jugador (caso oddspapi: su
    traducción solo lee líneas de equipo, players["0"])."""

    def pick_supported(self, pick):
        return "marca gol" not in (pick.seleccion or "").lower()


async def test_pick_no_soportado_marca_nomarket_sin_llamar(session):
    """Un pick que la fuente declara no-traducible se marca
    `nomarket` SIN gastar ni la llamada del fixture ni la de la
    curva — la cuota mensual no se quema en buscar lo imposible."""
    src = _NoPropSource(
        fixtures_return=[_Fixture()],
        choices=[
            HistoricalChoice(
                market_name="Full time",
                choice_name="1",
                choice_group=None,
                points=[PricePoint(captured_at=datetime(2026, 9, 19), cuota=1.5)],
            )
        ],
    )
    prop = _pick(1, 20)
    prop.seleccion = "Aubameyang marca gol"
    prop.mercado = "jugador"
    normal = _pick(2, 20)
    report = BackfillReport()

    await _run_source(src, [prop, normal], set(), set(), session, None, True, report)

    assert is_missed("fakefeed|nomarket|1") is True
    assert is_missed("fakefeed|nomarket|2") is False
    assert report.sin_opcion == 1
    # Solo el pick normal llegó a la fase de fixture/curva.
    assert report.con_fixture == 1


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


def _choice() -> HistoricalChoice:
    return HistoricalChoice(
        market_name="Full time",
        choice_name="1",
        choice_group=None,
        points=[PricePoint(captured_at=datetime(2026, 9, 20), cuota=2.0)],
    )


async def test_sin_opcion_marca_nomarket_en_apply(session):
    """El fixture existe y sus mercados ya se vieron pero nada casa
    con el pick (prop/línea que la fuente no publica): en apply se
    marca `<src>|nomarket|<pick>` para no re-descargar el fixture en
    cada pasada quemando cuota."""
    src = _FakeSource(fixtures_return=[_Fixture()], choices=[_choice()])
    pick = _pick(1, 20)
    pick.mercado = "jugador"
    pick.seleccion = "Isak marca gol en cualquier momento"

    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, True, report)

    assert report.sin_opcion == 1
    assert is_missed(f"fakefeed|nomarket|{pick.id}")

    # Segunda pasada: el pick se salta antes de pedir ni un día.
    src.days_requested.clear()
    report2 = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, True, report2)
    assert src.days_requested == []
    assert report2.sin_opcion == 0


async def test_sin_opcion_sin_apply_no_marca(session):
    """En dry-run el sin-opción solo informa — nada queda marcado y
    la siguiente pasada reintenta como siempre."""
    src = _FakeSource(fixtures_return=[_Fixture()], choices=[_choice()])
    pick = _pick(1, 20)
    pick.mercado = "jugador"
    pick.seleccion = "Isak marca gol en cualquier momento"

    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert report.sin_opcion == 1
    assert not is_missed(f"fakefeed|nomarket|{pick.id}")


async def test_opcion_casada_no_marca_nomarket(session):
    """Un pick que sí casa con una opción no lleva marca — solo los
    `sin opción` se descartan definitivamente por fuente."""
    src = _FakeSource(fixtures_return=[_Fixture()], choices=[_choice()])
    pick = _pick(1, 20)
    pick.mercado = "ganador"
    pick.seleccion = "A"

    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, True, report)

    assert report.sin_opcion == 0
    assert not is_missed(f"fakefeed|nomarket|{pick.id}")


class _WideDeltaSource(_FakeSource):
    """Fuente con ventana de búsqueda propia (caso oddspapi ±4)."""

    search_deltas = (0, -1, 1, -2, 2)


async def test_search_deltas_configurables(session):
    """El loop pide los deltas que declara la fuente, no siempre ±1:
    oddspapi cubre ±4 días porque su /fixtures es una llamada de rango
    que cuesta lo mismo pida 3 o 9 días."""
    src = _WideDeltaSource(fixtures_return=[])
    report = BackfillReport()

    await _run_source(src, [_pick(1, 20)], set(), set(), session, None, False, report)

    assert [d.day for d in src.days_requested] == [20, 19, 21, 18, 22]


async def test_pick_enlaza_evento_aunque_no_case_opcion(session):
    """El fixture casado rellena `odds_event_id` aunque el pick no
    encuentre mercado: el evento ES ese — el id permite reintentos
    offline desde el payload guardado."""
    src = _FakeSource(fixtures_return=[_Fixture()], choices=[])
    pick = _pick(1, 20)
    pick.mercado = "jugador"
    pick.seleccion = "Isak marca gol"

    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert report.sin_opcion == 1
    assert pick.odds_event_id == "fake:1"


class _FakePapiClient:
    """Cliente bet365 de mentira: cuenta llamadas y sirve fixtures."""

    def __init__(self, fixtures=None, payload=None):
        self.calls = 0
        self._fixtures = fixtures or []
        self._payload = payload
        self.fixture_ranges = []

    async def fixtures(self, http, sport_id, date_from, date_to):
        self.calls += 1
        self.fixture_ranges.append((date_from, date_to))
        return list(self._fixtures)

    async def markets(self, http):
        self.calls += 1
        return _PAPI_MARKETS

    async def historical_odds(self, http, fixture_id):
        self.calls += 1
        return (200, self._payload) if self._payload else (404, None)


_PAPI_MARKETS = {
    101: MarketDef(
        market_name="Full Time Result",
        market_type="1x2",
        period="fulltime",
        handicap=0.0,
        outcomes={101: "1", 102: "X", 103: "2"},
    )
}


class TestOddsPapiSource:
    """La fuente real de bet36528: ventana ancha + payload a disco."""

    def _source(self, client, tmp_path):
        from app.services.odds.historical_backfill import _OddsPapiSource

        src = _OddsPapiSource(api_key="k", api_host="h", data_dir=tmp_path)
        src._client = client
        return src

    async def test_una_llamada_cubre_nueve_dias(self, tmp_path):
        """/fixtures es una llamada de RANGO: pedir ±4 cuesta lo mismo
        que ±1 — los cubos vecinos salen de caché, no de cuota."""
        from datetime import date

        from app.services.odds.oddspapi import OddsPapiFixture

        fixture = OddsPapiFixture(
            "f1", None, "A", "B", datetime(2026, 9, 16, 18, 0), 10
        )
        client = _FakePapiClient(fixtures=[fixture])
        src = self._source(client, tmp_path)

        day = date(2026, 9, 20)
        assert await src.fixtures(None, 10, day) == []
        # El rango pedido es ±4 y los 9 días quedan cubiertos.
        assert client.fixture_ranges == [(date(2026, 9, 16), date(2026, 9, 24))]
        assert await src.fixtures(None, 10, date(2026, 9, 16)) == [fixture]
        assert await src.fixtures(None, 10, date(2026, 9, 24)) == []
        assert client.calls == 1

    async def test_choices_desde_disco_sin_llamada(self, tmp_path):
        """Payload guardado → la curva se re-traduce sin pagar otra
        llamada de /historical-odds."""
        from app.services.odds.oddspapi import (
            OddsPapiFixture,
            save_markets,
            save_payload,
        )

        fixture = OddsPapiFixture(
            "f1", None, "A", "B", datetime(2026, 9, 16, 18, 0), 10
        )
        save_markets(_PAPI_MARKETS, base=tmp_path)
        payload = {
            "bookmakers": {
                "bet365": {
                    "markets": {
                        "101": {
                            "outcomes": {
                                "101": {
                                    "players": {
                                        "0": [
                                            {
                                                "createdAt": "2026-09-15T10:00:00.000Z",
                                                "price": 2.1,
                                                "active": True,
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        save_payload(fixture, "oddspapi:f1", payload, base=tmp_path)
        client = _FakePapiClient()
        src = self._source(client, tmp_path)

        status, choices = await src.choices(None, fixture, "futbol", [])
        assert status == "ok"
        assert [c.choice_name for c in choices] == ["1"]
        assert client.calls == 0

    async def test_choices_descarga_y_guarda_payload(self, tmp_path):
        """Sin payload en disco: baja de la API y lo persiste."""
        from app.services.odds.oddspapi import (
            OddsPapiFixture,
            load_payload,
        )

        payload = {
            "bookmakers": {
                "bet365": {
                    "markets": {
                        "101": {
                            "outcomes": {
                                "101": {
                                    "players": {
                                        "0": [
                                            {
                                                "createdAt": "2026-09-15T10:00:00.000Z",
                                                "price": 2.1,
                                                "active": True,
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        fixture = OddsPapiFixture(
            "f1", None, "A", "B", datetime(2026, 9, 16, 18, 0), 10
        )
        client = _FakePapiClient(payload=payload)
        src = self._source(client, tmp_path)

        status, choices = await src.choices(None, fixture, "futbol", [])
        assert status == "ok"
        assert [c.choice_name for c in choices] == ["1"]
        # markets + historical-odds = 2 llamadas; el payload quedó.
        assert client.calls == 2
        assert load_payload("oddspapi:f1", base=tmp_path) is not None
