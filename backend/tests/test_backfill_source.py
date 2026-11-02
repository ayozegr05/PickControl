"""Tests de `_run_source` en `odds/historical_backfill.py`.

Cubre el orden de buckets (días recientes primero) y el contador
`sin_revisar`: picks a los que la pasada no llega porque la fuente se
quedó sin presupuesto de llamadas o la API falló — quedan pendientes
sin marca, a diferencia de `sin_fixture`/`sin_historial` (intentados).
"""

from datetime import date, datetime, timedelta

import pytest

from app.models.parsed_pick import ParsedPick
from app.services.odds.historical_backfill import (
    BackfillReport,
    _participant_ids_for_hint,
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


class _DirectedSource(_FakeSource):
    """Fuente con descubrimiento dirigido (duck-type de
    `fixtures_for_picks` — caso oddspapi por participante)."""

    def __init__(self, mode, directed=None, **kw):
        super().__init__(**kw)
        self._mode = mode
        self._directed = directed or []
        self.discover_calls = []

    async def fixtures_for_picks(self, http, sport_id, day, picks):
        self.discover_calls.append(day)
        return self._mode, list(self._directed)


async def test_descubrimiento_dirigido_evita_el_barrido(session):
    """Cuando la vía dirigida resuelve, el barrido por día NO se
    llama — eso es lo que ahorra las billables de /fixtures."""
    src = _DirectedSource("ok", directed=[_Fixture()], fixtures_return=[])
    picks = [_pick(1, 20)]
    report = BackfillReport()

    await _run_source(src, picks, set(), set(), session, None, False, report)

    assert src.days_requested == []
    assert report.con_fixture == 1


async def test_descubrimiento_fallback_vuelve_al_barrido(session):
    """Un pick sin equipo resoluble en el catálogo devuelve el cubo
    al barrido por día — la red de seguridad no se pierde."""
    src = _DirectedSource("fallback", fixtures_return=[_Fixture()])
    picks = [_pick(1, 20)]
    report = BackfillReport()

    await _run_source(src, picks, set(), set(), session, None, False, report)

    assert src.days_requested != []
    assert report.con_fixture == 1


async def test_descubrimiento_error_cuenta_sin_revisar(session):
    src = _DirectedSource("error", fixtures_return=[_Fixture()])
    picks = [_pick(1, 20), _pick(2, 20)]
    report = BackfillReport()

    await _run_source(src, picks, set(), set(), session, None, False, report)

    assert src.days_requested == []
    assert report.sin_revisar == 2


def test_participant_ids_por_hint():
    catalog = {34: "Real Betis", 35: "Sevilla FC", 99: "Machac, Tomas"}
    assert _participant_ids_for_hint(catalog, "Sevilla vs Real Betis") == {34, 35}
    # Tenis: el catálogo guarda "Apellido, Nombre".
    assert _participant_ids_for_hint(catalog, "Tomas Machac vs Otro") == {99}
    assert _participant_ids_for_hint(catalog, "Zzz Qqq Fcfc") == set()


def test_participant_ids_prefiere_senior_y_descarta_srl():
    catalog = {
        1: "Elche CF",
        2: "Elche Ilicitano CF",
        3: "Malaga CF",
        4: "Malaga CF Srl",
        5: "Sporting CP",
        6: "Sporting Clube Farense",
    }
    # El filial queda penalizado bajo el umbral: solo el senior.
    assert _participant_ids_for_hint(catalog, "elche - otro") == {1}
    # La variante SRL queda por debajo del umbral.
    assert _participant_ids_for_hint(catalog, "malaga - otro") == {3}
    # Multi-ficha: "sporting" casa con Sporting CP y Farense — se
    # devuelven ambas para unir sus fixtures.
    assert _participant_ids_for_hint(catalog, "sporting - otro") == {5, 6}


def test_participant_ids_fichas_duplicadas():
    # El caso Liverpool real: la ficha exacta es solo-Champions y las
    # "FC" domésticas comparten el club — hay que llevarse varias.
    catalog = {
        90134: "Liverpool",
        44: "Liverpool FC",
        36556: "Liverpool FC",
        32: "Ipswich Town",
        397536: "Ipswich Town",
        1098127: "Ipswich FC",
        694839: "Liverpool FC SRL",
        203621: "Liverpool Montevideo",
    }
    ids = _participant_ids_for_hint(catalog, "ipswich - liverpool")
    # Incluye la ficha Champions + las dos domésticas (top-3 por
    # score) y el Ipswich Town canónico — no la Ipswich australiana.
    assert 44 in ids and 90134 in ids
    assert 32 in ids
    assert 694839 not in ids  # SRL penalizada
    assert len(ids) <= 6  # 2 equipos x 3 fichas máx.


def test_participant_ids_selecciones_en_espanol():
    catalog = {7: "Belgium", 8: "Norway", 9: "Spain", 10: "Real CD Espana"}
    assert _participant_ids_for_hint(catalog, "francia - bélgica") == {7}
    assert _participant_ids_for_hint(catalog, "portugal - noruega") == {8}
    # "españa" casa con Spain, no con el club hondureño "España".
    assert _participant_ids_for_hint(catalog, "inglaterra - españa") == {9}


def test_participant_ids_femenino():
    catalog = {11: "Chelsea (W)", 12: "Chelsea", 13: "1. FC Koln (W)"}
    # Pick masculino: la variante (W) queda penalizada.
    assert _participant_ids_for_hint(catalog, "chelsea - otro") == {12}
    # Pick femenino: se permite la variante (W).
    assert _participant_ids_for_hint(catalog, "lyon femenino vs chelsea femenino") == {
        11
    }
    assert _participant_ids_for_hint(catalog, "fc koln (f) vs otro") == {13}


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


# --- _FiveDollarSource: filtro de ventana/props + caché de día -----


async def test_fivedollar_pick_supported():
    """La fuente descarta sin llamada los picks fuera de la ventana
    Free (~3 meses) y los props de jugador — ambos son miss
    garantizado en su catálogo."""
    from app.core.dates import utc_now
    from app.services.odds.historical_backfill import _FiveDollarSource

    src = _FiveDollarSource(api_key="x")

    reciente = _pick(1, 20)
    reciente.fecha_evento = utc_now() - timedelta(days=30)
    assert src.pick_supported(reciente) is True

    viejo = _pick(2, 20)
    viejo.fecha_evento = utc_now() - timedelta(days=200)
    assert src.pick_supported(viejo) is False

    prop = _pick(3, 20)
    prop.fecha_evento = utc_now() - timedelta(days=10)
    prop.mercado = "jugador"
    prop.seleccion = "Aubameyang marca gol"
    assert src.pick_supported(prop) is False


async def test_fivedollar_day_cache_roundtrip(tmp_path):
    """El listado de un día pasado se guarda a disco (es inmutable):
    una pasada nueva lo lee sin repetir la llamada a la API — incluso
    los días sin fixtures, que también se pagan una sola vez."""
    from app.services.odds import fivedollar

    day = date(2026, 9, 20)
    fixtures = [
        fivedollar.FiveFixture(
            fixture_id=7,
            home_name="A",
            away_name="B",
            start=datetime(2026, 9, 20, 18),
            league_name="LaLiga",
        )
    ]
    fivedollar.save_day(day, fixtures, tmp_path)
    loaded = fivedollar.load_day(day, tmp_path)
    assert loaded is not None
    assert loaded[0].fixture_id == 7
    assert loaded[0].home_name == "A"
    assert loaded[0].start == datetime(2026, 9, 20, 18)
    assert loaded[0].league_name == "LaLiga"
    assert fivedollar.load_day(date(2026, 9, 21), tmp_path) is None
    fivedollar.save_day(date(2026, 9, 22), [], tmp_path)
    assert fivedollar.load_day(date(2026, 9, 22), tmp_path) == []


def _event(ext_id: str, sport: str, home: str, away: str, start):
    from app.models.odds_snapshot import OddsEvent

    return OddsEvent(
        event_ext_id=ext_id,
        sport=sport,
        home_team=home,
        away_team=away,
        start=start,
    )


def test_link_consistent_ambos_lados():
    """El enlace solo es creíble si AMBOS lados del pick aparecen en
    los equipos del OddsEvent (caso base: traducción de países)."""
    from app.services.odds.historical_backfill import _link_consistent

    pick = _pick(1, 19)
    pick.evento = "Albania - San Marino"
    ev = _event("e:1", "futbol", "Albania", "San Marino", datetime(2026, 10, 6))
    assert _link_consistent(pick, ev, "futbol") is True


def test_link_consistent_rechaza_lado_ausente():
    """Grupo D: 'Djere vs Glinka' enlazado a 'Martinez vs Djere' —
    un solo apellido compartido no basta."""
    from app.services.odds.historical_backfill import _link_consistent

    pick = _pick(1, 11)
    pick.deporte = "tenis"
    pick.evento = "Laslo Djere vs Daniil Glinka"
    pick.seleccion = "Laslo Djere gana"
    ev = _event(
        "sofascore:1",
        "tenis",
        "Pedro Martinez",
        "Laslo Djere",
        datetime(2026, 10, 10),
    )
    assert _link_consistent(pick, ev, "tenis") is False


def test_link_consistent_alias_ciudad():
    """'Betis - Oporto' vs 'Real Betis-FC Porto': el alias de ciudad
    no rompe la consistencia."""
    from app.services.odds.historical_backfill import _link_consistent

    pick = _pick(1, 18)
    pick.evento = "Betis - Oporto"
    ev = _event(
        "espn:1",
        "futbol",
        "Real Betis",
        "FC Porto",
        datetime(2026, 10, 14),
    )
    assert _link_consistent(pick, ev, "futbol") is True


async def test_enlace_ancla_dia_de_busqueda(session):
    """Grupo C: con fecha_evento corrupta, el bucket y el match usan
    la fecha real del OddsEvent enlazado."""
    ev = _event(
        "espn:soccer:x:1",
        "futbol",
        "Albania",
        "San Marino",
        datetime(2026, 10, 6, 18, 45),
    )
    session.add(ev)
    await session.flush()
    pick = _pick(1, 19)
    pick.fecha_evento = datetime(2026, 8, 19)
    pick.evento = "Albania - San Marino"
    pick.odds_event_id = ev.event_ext_id
    src = _FakeSource(fixtures_return=[])
    report = BackfillReport()

    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert date(2026, 10, 6) in src.days_requested
    assert date(2026, 8, 19) not in src.days_requested


async def test_enlace_inconsistente_se_suelta_y_desatribuye(session):
    """Grupo D (apply): enlace a otro partido → odds_event_id=None,
    marcas borradas y filas del evento equivocado desatribuidas."""
    from app.models.odds_snapshot import OddsSnapshot
    from app.services.results.base import mark_missed

    ev = _event(
        "sofascore:1",
        "tenis",
        "Pedro Martinez",
        "Laslo Djere",
        datetime(2026, 10, 10),
    )
    snap = OddsSnapshot(
        provider="sofascore",
        event_ext_id=ev.event_ext_id,
        market_name="Ganador",
        choice_name="1",
        cuota=2.0,
        parsed_pick_id=7,
    )
    session.add_all([ev, snap])
    await session.flush()
    pick = _pick(7, 11)
    pick.deporte = "tenis"
    pick.evento = "Laslo Djere vs Daniil Glinka"
    pick.seleccion = "Laslo Djere gana"
    pick.fecha_evento = datetime(2026, 10, 11)
    pick.odds_event_id = ev.event_ext_id
    mark_missed("fakefeed|nofixture|7")
    src = _FakeSource(fixtures_return=[])
    src.sport_ids = {"tenis": 1}
    report = BackfillReport()

    await _run_source(src, [pick], set(), set(), session, None, True, report)

    assert pick.odds_event_id is None
    assert snap.parsed_pick_id is None
    # El pick se reintentó por su propia fecha (sin ancla) — el
    # nofixture fresco demuestra que la marca vieja se había borrado
    # y la búsqueda volvió a correr de verdad.
    assert report.sin_fixture == 1
    assert date(2026, 10, 11) in src.days_requested
    assert is_missed("fakefeed|nofixture|7")


async def test_ancla_invalida_marcas_de_fecha_corrupta(session):
    """Grupo C (apply): las marcas escritas buscando el día equivocado
    no blindan el pick — se borran y el pick se reintenta anclado."""
    ev = _event(
        "espn:soccer:x:2",
        "futbol",
        "Albania",
        "San Marino",
        datetime(2026, 10, 6, 18, 45),
    )
    session.add(ev)
    await session.flush()
    pick = _pick(1, 19)
    pick.fecha_evento = datetime(2026, 8, 19)
    pick.evento = "Albania - San Marino"
    pick.odds_event_id = ev.event_ext_id
    from app.services.results.base import mark_missed

    mark_missed("fakefeed|nofixture|1")
    src = _FakeSource(fixtures_return=[])
    report = BackfillReport()

    await _run_source(src, [pick], set(), set(), session, None, True, report)

    assert report.sin_fixture == 1  # reintentado, no saltado por marca
    assert date(2026, 10, 6) in src.days_requested


async def test_ancla_no_salta_marcas_si_fecha_coincide(session):
    """Enlace con la MISMA fecha que el pick: las marcas no están
    contaminadas y el skip funciona como siempre."""
    ev = _event(
        "espn:soccer:x:3",
        "futbol",
        "Albania",
        "San Marino",
        datetime(2026, 9, 20, 18, 45),
    )
    session.add(ev)
    await session.flush()
    pick = _pick(1, 20)
    pick.evento = "Albania - San Marino"
    pick.odds_event_id = ev.event_ext_id
    from app.services.results.base import mark_missed

    mark_missed("fakefeed|nofixture|1")
    src = _FakeSource(fixtures_return=[])
    report = BackfillReport()

    await _run_source(src, [pick], set(), set(), session, None, True, report)

    assert report.sin_fixture == 0  # marca respetada, ni una llamada
    assert src.days_requested == []
