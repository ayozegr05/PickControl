"""Tests de `odds/static_pages.py` — cuotas de cierre desde listados
estáticos (tennisexplorer + betexplorer), fuentes gratuitas del
backfill que cubren el hueco de ITF y del 1X2 de fútbol sin key ni
render JS.
"""

from datetime import date, datetime

import pytest

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds import static_pages as sp
from app.services.odds.compare import map_pick_choices
from app.services.odds.historical_backfill import (
    BackfillReport,
    _BetExplorerSource,
    _run_source,
    _TennisExplorerSource,
)

# --- HTML realista (recortado de las páginas vivas 2026-10-04) -----

TE_HTML = """
<table class="result" cellspacing="0"><tbody>
<tr class="head flags"><td class="t-name" colspan="2">
<a href="/itf-men-monastir/2026/itf-men/"><span class="fl">&nbsp;</span>
<span class="type-men">&nbsp;</span>ITF Monastir</a></td>
<td class="score">S</td><td class="course">H</td><td class="course">A</td></tr>
<tr id="s11" class="two bott">
<td class="first time" rowspan="2">15:30</td>
<td class="t-name"><a href="/player/rodionov/">Rodionov J.</a></td>
<td class="result">&nbsp;</td><td class="score">&nbsp;</td><td class="h2h">-</td>
<td class="coursew" rowspan="2">1.49</td>
<td class="course" rowspan="2">3.00</td>
<td rowspan="2"><a href="/match-detail/?id=3338819">info</a></td></tr>
<tr id="s11b" class="two">
<td class="t-name"><a href="/player/ferreira-silva/">Ferreira Silva F.</a></td>
<td class="result">&nbsp;</td><td class="score">&nbsp;</td><td class="h2h">-</td></tr>
<tr id="s12" class="one bott">
<td class="first time" rowspan="2">17:00</td>
<td class="t-name"><a href="/player/coe/">Coe R.</a></td>
<td class="result">&nbsp;</td><td class="score">&nbsp;</td><td class="h2h">-</td>
<td class="coursew" rowspan="2">&nbsp;</td>
<td class="course" rowspan="2">&nbsp;</td>
<td rowspan="2"><a href="/match-detail/?id=3338459">info</a></td></tr>
<tr id="s12b" class="one">
<td class="t-name"><a href="/player/kregar/">Kregar G.</a></td>
<td class="result">&nbsp;</td><td class="score">&nbsp;</td><td class="h2h">-</td></tr>
</tbody></table>
"""

BE_HTML = """
<table class="table-main js-nrbanner-t"><tbody>
<tr class="js-tournament"><th class="h-text-left" colspan="2">
<a href="/football/spain/laliga2/" class="table-main__tournament">
<i><img src="x.svg"></i>Spain: LaLiga2</a></th></tr>
<tr data-dt="3,10,2026,19,30" data-def="1">
<td class="table-main__tt"><span class="table-main__time">19:30</span>
<a href="/football/spain/laliga2/malaga-deportivo-la-coruna/hGycdKve/">
Malaga - <strong>Deportivo La Coruna</strong></a></td>
<td class="table-main__result"><a href="/x/"> <strong>1:2</strong></a></td>
<td class="table-main__odds" data-oid="a" data-odd="2.10"></td>
<td class="table-main__odds" data-oid="b" data-odd="3.30"></td>
<td class="table-main__odds colored" data-oid="c"><span><span>
<span data-odd="2.25"></span></span></span></td></tr>
<tr data-dt="3,10,2026,21,00" data-def="1">
<td class="table-main__tt"><span class="table-main__time">21:00</span>
<a href="/football/spain/laliga2/eibar-leganes/AbCdEf12/">
Eibar - <strong>Leganes</strong></a></td>
<td class="table-main__result"><a href="/x/"> <strong>0:0</strong></a></td>
<td class="table-main__partial" colspan="3">&nbsp;</td></tr>
</tbody></table>
"""


class _Http:
    """Cliente falso: devuelve el HTML dado."""

    def __init__(self, html: str):
        self.html = html

    async def get(self, url, headers=None, timeout=None):
        class _R:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                pass

        return _R(self.html)


# --- TennisExplorer ------------------------------------------------


@pytest.mark.asyncio
async def test_te_parse_partidos_y_cuotas():
    rows, suspicious = await sp.fetch_tennisexplorer_day(
        _Http(TE_HTML), date(2026, 10, 3), health=False
    )
    assert not suspicious
    assert len(rows) == 2
    r = rows[0]
    assert r.home_name == "Rodionov J."
    assert r.away_name == "Ferreira Silva F."
    assert r.odds1 == 1.49 and r.odds2 == 3.00
    assert r.oddsX is None
    assert r.tournament == "ITF Monastir"
    assert r.ext_id == "tennisexplorer:3338819"
    assert r.start == datetime(2026, 10, 3, 15, 30)
    # Partido sin cuota publicada: la fila existe pero has_odds False.
    assert not rows[1].has_odds


@pytest.mark.asyncio
async def test_te_listado_vacio_grande_es_sospechoso():
    big = "<html><body>" + "x" * 60_000 + "</body></html>"
    rows, suspicious = await sp.fetch_tennisexplorer_day(
        _Http(big), date(2026, 10, 3), health=False
    )
    assert rows == []
    assert suspicious


def test_te_match_por_apellidos():
    row = sp.StaticMatch(
        home_name="Rodionov J.",
        away_name="Ferreira Silva F.",
        start=datetime(2026, 10, 3),
        ext_id="tennisexplorer:1",
        tournament="",
        odds1=1.49,
        oddsX=None,
        odds2=3.0,
    )
    assert sp.te_match([row], "Rodionov vs Ferreira Silva") is row
    assert sp.te_match([row], "Rodionov") is row
    assert sp.te_match([row], "Alcaraz vs Sinner") is None


def test_te_choices_full_time():
    row = sp.StaticMatch(
        home_name="A",
        away_name="B",
        start=datetime(2026, 10, 3),
        ext_id="tennisexplorer:1",
        tournament="",
        odds1=1.49,
        oddsX=None,
        odds2=3.0,
    )
    choices = sp.te_choices(row)
    assert [(c.market_name, c.choice_name) for c in choices] == [
        ("Full time", "1"),
        ("Full time", "2"),
    ]
    assert choices[0].points[0].cuota == 1.49


# --- BetExplorer ---------------------------------------------------


@pytest.mark.asyncio
async def test_be_parse_partidos_y_cuotas():
    rows, suspicious = await sp.fetch_betexplorer_day(
        _Http(BE_HTML), date(2026, 10, 3), health=False
    )
    assert not suspicious
    assert len(rows) == 2
    r = rows[0]
    assert r.home_name == "Malaga"
    assert r.away_name == "Deportivo La Coruna"
    assert (r.odds1, r.oddsX, r.odds2) == (2.10, 3.30, 2.25)
    assert r.tournament == "Spain: LaLiga2"
    assert r.ext_id == "betexplorer:hGycdKve"
    assert r.start == datetime(2026, 10, 3, 19, 30)
    assert not rows[1].has_odds


def test_be_match_equipos():
    row = sp.StaticMatch(
        home_name="Malaga",
        away_name="Deportivo La Coruna",
        start=datetime(2026, 10, 3),
        ext_id="betexplorer:x",
        tournament="",
        odds1=2.1,
        oddsX=3.3,
        odds2=2.25,
    )
    assert sp.be_match([row], "Malaga vs Deportivo La Coruna") is row
    assert sp.be_match([row], "Deportivo vs Malaga") is row
    assert sp.be_match([row], "Barcelona vs Real Madrid") is None


def test_be_choices_full_time_1x2():
    row = sp.StaticMatch(
        home_name="A",
        away_name="B",
        start=datetime(2026, 10, 3),
        ext_id="betexplorer:x",
        tournament="",
        odds1=2.1,
        oddsX=3.3,
        odds2=2.25,
    )
    choices = sp.be_choices(row)
    assert [(c.market_name, c.choice_name) for c in choices] == [
        ("Full time", "1"),
        ("Full time", "X"),
        ("Full time", "2"),
    ]


# --- Integración con la cascada ------------------------------------


@pytest.mark.asyncio
async def test_fuente_te_resuelve_pick_ganador(session, monkeypatch):
    """La fuente casa el pick por apellidos en la fecha y escribe el
    'Full time' — cero llamadas extra por partido."""
    src = _TennisExplorerSource()
    row = sp.StaticMatch(
        home_name="Rodionov J.",
        away_name="Ferreira Silva F.",
        start=datetime(2026, 10, 3, 17, 0),
        ext_id="tennisexplorer:9",
        tournament="",
        odds1=1.49,
        oddsX=None,
        odds2=3.0,
    )
    monkeypatch.setattr(src, "_days", {date(2026, 10, d): [row] for d in (2, 3, 4)})

    pick = ParsedPick(
        id=1,
        es_apuesta=True,
        deporte="tenis",
        evento="Rodionov vs Ferreira Silva",
        mercado="ganador",
        seleccion="Rodionov gana",
        cuota=1.53,
        fecha_evento=datetime(2026, 10, 3),
    )
    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert report.con_fixture == 1
    assert report.mapeados == 1
    assert report.filas == 1
    assert "tipster 1.53 vs mercado 1.49" in report.detalles[-1]
    assert "[tennisexplorer]" in report.detalles[-1]


@pytest.mark.asyncio
async def test_fuente_be_resuelve_pick_ganador(session, monkeypatch):
    src = _BetExplorerSource()
    row = sp.StaticMatch(
        home_name="Malaga",
        away_name="Deportivo La Coruna",
        start=datetime(2026, 10, 3, 19, 30),
        ext_id="betexplorer:x1",
        tournament="",
        odds1=2.10,
        oddsX=3.30,
        odds2=2.25,
    )
    monkeypatch.setattr(src, "_days", {date(2026, 10, d): [row] for d in (2, 3, 4)})

    pick = ParsedPick(
        id=2,
        es_apuesta=True,
        deporte="fútbol",
        evento="Málaga - Deportivo de la Coruña",
        mercado="ganador",
        seleccion="Deportivo gana",
        cuota=2.30,
        fecha_evento=datetime(2026, 10, 3),
    )
    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert report.con_fixture == 1
    assert report.mapeados == 1
    assert report.filas == 1
    assert "[betexplorer]" in report.detalles[-1]


@pytest.mark.asyncio
async def test_fuente_listado_sospechoso_no_marca_nofixture(session, monkeypatch):
    """Página descargada pero sin filas -> fixtures() devuelve None:
    el pick queda 'sin revisar' (reintento) en vez de comerse un
    nofixture falso que bloquearía el reintento ~15 días."""
    src = _TennisExplorerSource()
    monkeypatch.setattr(src, "_days", {date(2026, 10, d): None for d in (2, 3, 4)})

    pick = ParsedPick(
        id=3,
        es_apuesta=True,
        deporte="tenis",
        evento="A vs B",
        mercado="ganador",
        seleccion="A gana",
        cuota=1.5,
        fecha_evento=datetime(2026, 10, 3),
    )
    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert report.sin_revisar == 1
    assert report.sin_fixture == 0


def test_map_pick_choices_contra_filas_be():
    """Las filas 'Full time' 1/X/2 se mapean a un pick 'ganador'."""
    event = OddsEvent(
        event_ext_id="betexplorer:x1",
        sport="futbol",
        home_team="Malaga",
        away_team="Deportivo La Coruna",
        start=datetime(2026, 10, 3),
    )
    virtual = [
        OddsSnapshot(
            provider="betexplorer",
            event_ext_id="betexplorer:x1",
            market_name="Full time",
            choice_name=name,
            choice_group=None,
            cuota=price,
            is_live=False,
            suspended=False,
            captured_at=datetime(2026, 10, 3),
        )
        for name, price in (("1", 2.1), ("X", 3.3), ("2", 2.25))
    ]
    pick = ParsedPick(
        id=4,
        es_apuesta=True,
        deporte="fútbol",
        evento="Malaga vs Deportivo",
        mercado="ganador",
        seleccion="Deportivo gana",
        cuota=2.3,
        fecha_evento=datetime(2026, 10, 3),
    )
    matched = map_pick_choices(pick, event, virtual)
    assert matched is not None
    assert matched.choice_name == "2"
    assert matched.rows[0].cuota == 2.25
