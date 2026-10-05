"""Tests de `odds/static_pages.py` — cuotas de cierre desde listados
estáticos (tennisexplorer + betexplorer), fuentes gratuitas del
backfill que cubren el hueco de ITF y del 1X2 de fútbol sin key ni
render JS.
"""

from datetime import date, datetime

import pytest

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds import historical_backfill as hb
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
<tr id="r1175" class="two bott">
<td class="first time" rowspan="2">15:30</td>
<td class="t-name"><a href="/player/rodionov/">Rodionov J.</a></td>
<td class="result">2</td><td class="score">6</td><td class="h2h">-</td>
<td class="coursew" rowspan="2">1.49</td>
<td class="course" rowspan="2">3.00</td>
<td class="alone-icons" rowspan="2">&nbsp;</td>
<td rowspan="2"><a href="/match-detail/?id=3338819">info</a></td></tr>
<tr id="r1175b" class="two">
<td class="t-name"><a href="/player/ferreira-silva/">Ferreira Silva F.</a></td>
<td class="result">0</td><td class="score">2</td><td class="h2h">-</td></tr>
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

# Fila de la página /football/{liga}/results/ — formato distinto:
# <tr><td h-text-left><a class="in-match">A - B</a></td> + odds +
# celda de fecha "DD.MM." (sin data-dt).
BE_LEAGUE_HTML = """
<table class="table-main"><tr><th class="h-text-left" colspan="2">
7. Round</th></tr>
<tr><td class="h-text-left"><a data-test="10971117"
href="/football/spain/laliga2/malaga-deportivo-la-coruna/hGycdKve/"
class="in-match"><span>Malaga</span> - <span>Deportivo La Coruna</span></a></td>
<td class="h-text-center"><a href="/x/">1:2</a></td>
<td class="table-main__odds" data-oid="a" data-odd="2.10"></td>
<td class="table-main__odds" data-oid="b" data-odd="3.30"></td>
<td class="table-main__odds" data-oid="c" data-odd="2.25"></td>
<td class="h-text-right h-text-no-wrap">15.09.</td></tr>
<tr><td class="h-text-left"><a data-test="10971118"
href="/football/spain/laliga2/eibar-leganes/AbCdEf12/"
class="in-match"><span><strong>Eibar</strong></span> - <span>Leganes</span></a></td>
<td class="h-text-center"><a href="/x/">0:0</a></td>
<td class="table-main__odds" data-oid="d" data-odd="2.60"></td>
<td class="table-main__odds" data-oid="e" data-odd="3.00"></td>
<td class="table-main__odds" data-oid="f" data-odd="2.90"></td>
<td class="h-text-right h-text-no-wrap">15.09.</td></tr>
</table>
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


@pytest.mark.asyncio
async def test_be_choices_lazy_league(monkeypatch):
    """La fila du jour sans `data-odd` : la page de ligue, découverte
    via `league_path`, fournit les clôtures 1X2 (auto-découverte des
    ~360 compétitions sans maintenance du mappe)."""
    src = _BetExplorerSource()
    day_row = sp.StaticMatch(
        home_name="Malaga",
        away_name="Deportivo La Coruna",
        start=datetime(2026, 10, 3, 19, 30),
        ext_id="betexplorer:hGycdKve",
        tournament="Spain: LaLiga2",
        odds1=None,
        oddsX=None,
        odds2=None,
        league_path="/football/spain/laliga2/",
    )
    league_row = sp.StaticMatch(
        home_name="Malaga",
        away_name="Deportivo La Coruna",
        start=datetime(2026, 10, 3, 12, 0),
        ext_id="betexplorer:hGycdKve",
        tournament="spain laliga2",
        odds1=2.10,
        oddsX=3.30,
        odds2=2.25,
        league_path="spain/laliga2",
    )
    calls = []

    async def fake_league(http, path):
        calls.append(path)
        return [league_row]

    monkeypatch.setattr(sp, "fetch_betexplorer_league", fake_league)
    status, choices = await src.choices(None, day_row, "futbol", [])
    assert status == "ok"
    assert {c.choice_name: c.points[0].cuota for c in choices} == {
        "1": 2.10,
        "X": 3.30,
        "2": 2.25,
    }
    assert calls == ["spain/laliga2"]
    # Deuxième appel : la ligue est en cache, pas de re-fetch.
    status2, _ = await src.choices(None, day_row, "futbol", [])
    assert status2 == "ok"
    assert calls == ["spain/laliga2"]


def test_be_day_rows_portent_league_path():
    """La cabecera de torneo donne à chaque fila son chemin de liga
    (découverte à la demande)."""
    rows, _ = sp._parse_betexplorer_day(BE_HTML)
    assert rows[0].league_path == "/football/spain/laliga2/"


# --- Detalle de partido (/match-odds/) -------------------------------

# Recortes del JSON {"odds": ...} real (Atl. Madrid-Real Madrid,
# 2026-10-05): un tbody por línea, celda data-odd + data-pos por
# columna, fila por casa — la mejor cuota es el MAX por columna.
BE_OU_DETAIL_HTML = """
<tbody id="all-odds-0.50">
<tr data-bid="1"><td>n</td>
<td class="table-main__detail-odds" data-odd="1.02"
data-created="13,09,2026,15,19" data-pos="1"></td>
<td class="table-main__detail-odds" data-odd="13.50" data-pos="2"></td></tr>
</tbody>
<tbody id="all-odds-2.50">
<tr data-bid="1"><td>n</td>
<td class="table-main__detail-odds" data-odd="1.95" data-pos="1"></td>
<td class="table-main__detail-odds" data-odd="1.90" data-pos="2"></td></tr>
<tr data-bid="2"><td>n</td>
<td class="table-main__detail-odds" data-odd="2.02"
data-created="3,10,2026,16,00" data-pos="1"></td>
<td class="table-main__detail-odds" data-odd="1.85" data-pos="2"></td></tr>
</tbody>
<tbody id="all-odds-3.50">
<tr data-bid="1"><td>n</td>
<td class="table-main__detail-odds" data-odd="3.10" data-pos="1"></td>
<td class="table-main__detail-odds" data-odd="1.30" data-pos="2"></td></tr>
</tbody>
"""

# OJO: data-pos es el CÓDIGO del outcome, no el ordinal — "10"=1X,
# "12"=12, "02"=X2, "y"/"n"=sí/no (real 2026-10-05).
BE_DC_DETAIL_HTML = """
<tbody id="all-odds-0">
<tr data-bid="1"><td>n</td>
<td class="table-main__detail-odds" data-odd="1.40" data-pos="10"></td>
<td class="table-main__detail-odds" data-odd="2.80" data-pos="12"></td>
<td class="table-main__detail-odds" data-odd="1.60" data-pos="02"></td></tr>
<tr data-bid="2"><td>n</td>
<td class="table-main__detail-odds" data-odd="1.45" data-pos="10"></td>
<td class="table-main__detail-odds" data-odd="2.75" data-pos="12"></td>
<td class="table-main__detail-odds" data-odd="1.65" data-pos="02"></td></tr>
</tbody>
"""

BE_BTS_DETAIL_HTML = """
<tbody id="all-odds-0">
<tr data-bid="1"><td>n</td>
<td class="table-main__detail-odds" data-odd="1.49" data-pos="y"></td>
<td class="table-main__detail-odds" data-odd="2.64" data-pos="n"></td></tr>
</tbody>
"""


def test_be_market_choices_ou_lineas_exactas():
    """Cada línea O/U da su par Over/Under con la MEJOR cuota entre
    casas y la línea en choice_group — nada de vecinas."""
    choices = sp.be_market_choices(BE_OU_DETAIL_HTML, "ou")
    got = {(c.choice_name, c.choice_group): c.points[0].cuota for c in choices}
    assert got == {
        ("Over", "0.5"): 1.02,
        ("Under", "0.5"): 13.50,
        ("Over", "2.5"): 2.02,  # mejor entre 1.95 y 2.02
        ("Under", "2.5"): 1.90,
        ("Over", "3.5"): 3.10,
        ("Under", "3.5"): 1.30,
    }
    assert all(c.market_name == "Match goals" for c in choices)
    # El timestamp del punto es el data-created de la celda ganadora.
    over25 = next(
        c for c in choices if c.choice_name == "Over" and c.choice_group == "2.5"
    )
    assert over25.points[0].captured_at == datetime(2026, 10, 3, 16, 0)


def test_be_market_choices_doble_oportunidad():
    choices = sp.be_market_choices(BE_DC_DETAIL_HTML, "dc")
    got = {c.choice_name: c.points[0].cuota for c in choices}
    assert got == {"1X": 1.45, "12": 2.80, "X2": 1.65}
    assert all(
        c.market_name == "Double chance" and c.choice_group is None for c in choices
    )


def test_be_market_choices_btts():
    choices = sp.be_market_choices(BE_BTS_DETAIL_HTML, "bts")
    got = {c.choice_name: c.points[0].cuota for c in choices}
    assert got == {"Yes": 1.49, "No": 2.64}
    assert all(c.market_name == "Both teams to score" for c in choices)


def test_be_pick_bettype_routing():
    assert sp.be_pick_bettype("over/under goles", "más de 2.5") == "ou"
    assert sp.be_pick_bettype("goles", "más de 3.5 goles") == "ou"
    assert sp.be_pick_bettype("doble oportunidad", "Granada gana o empata") == "dc"
    assert sp.be_pick_bettype("ambos marcan", "sí") == "bts"
    assert sp.be_pick_bettype("resultado", "no anotan ambos") == "bts"
    assert sp.be_pick_bettype("hándicap asiático", "Betis -1.5") == "ah"
    assert sp.be_pick_bettype("empate no válido", "Betis") == "ha"
    # Mercados que BE no publica o que ya da el listado -> None (sin fetch).
    assert sp.be_pick_bettype("córners", "más de 9.5 córners") is None
    assert sp.be_pick_bettype("ganador", "Malaga gana") is None


@pytest.mark.asyncio
async def test_be_choices_detalle_lazy_compartido(monkeypatch):
    """Dos picks del mismo partido (uno O/U, uno ganador): una sola
    descarga del bettype 'ou', reutilizada, junto al 1X2 del listado."""
    src = _BetExplorerSource()
    fixture = sp.StaticMatch(
        home_name="Malaga",
        away_name="Deportivo La Coruna",
        start=datetime(2026, 10, 3, 19, 30),
        ext_id="betexplorer:hGycdKve",
        tournament="",
        odds1=2.10,
        oddsX=3.30,
        odds2=2.25,
    )
    calls = []

    async def fake_market(http, code, bettype):
        calls.append((code, bettype))
        return BE_OU_DETAIL_HTML if bettype == "ou" else ""

    monkeypatch.setattr(sp, "fetch_betexplorer_market", fake_market)
    picks = [
        ParsedPick(
            id=11, mercado="over/under goles", seleccion="más de 2.5 goles", linea=2.5
        ),
        ParsedPick(id=12, mercado="ganador", seleccion="Malaga gana"),
        ParsedPick(
            id=13, mercado="over/under goles", seleccion="menos de 3.5", linea=3.5
        ),
    ]
    status, choices = await src.choices(None, fixture, "futbol", picks)
    assert status == "ok"
    assert calls == [("hGycdKve", "ou")]
    keys = {(c.market_name, c.choice_name, c.choice_group) for c in choices}
    assert ("Full time", "1", None) in keys
    assert ("Match goals", "Over", "2.5") in keys
    assert ("Match goals", "Under", "3.5") in keys


@pytest.mark.asyncio
async def test_be_choices_sin_detalle_para_1x2(monkeypatch):
    """Pick de ganador con listado 1X2 disponible: cero llamadas al
    endpoint de detalle."""
    src = _BetExplorerSource()
    fixture = sp.StaticMatch(
        home_name="A",
        away_name="B",
        start=datetime(2026, 10, 3),
        ext_id="betexplorer:x9",
        tournament="",
        odds1=2.1,
        oddsX=3.3,
        odds2=2.25,
    )
    called = []

    async def fake_market(http, code, bettype):
        called.append(bettype)
        return ""

    monkeypatch.setattr(sp, "fetch_betexplorer_market", fake_market)
    pick = ParsedPick(id=14, mercado="ganador", seleccion="A gana")
    status, choices = await src.choices(None, fixture, "futbol", [pick])
    assert status == "ok"
    assert called == []
    assert {c.choice_name for c in choices} == {"1", "X", "2"}


@pytest.mark.asyncio
async def test_be_choices_detalle_fallo_es_error_no_miss(monkeypatch):
    """Fallo transitorio del endpoint -> status 'error' (sin marcar
    hist, reintento próxima pasada), no 'miss' permanente."""
    src = _BetExplorerSource()
    fixture = sp.StaticMatch(
        home_name="A",
        away_name="B",
        start=datetime(2026, 10, 3),
        ext_id="betexplorer:x9",
        tournament="",
        odds1=None,
        oddsX=None,
        odds2=None,
    )

    async def fake_market(http, code, bettype):
        return None  # timeout/5xx transitorio

    monkeypatch.setattr(sp, "fetch_betexplorer_market", fake_market)
    pick = ParsedPick(
        id=15, mercado="over/under goles", seleccion="más de 2.5", linea=2.5
    )
    status, choices = await src.choices(None, fixture, "futbol", [pick])
    assert status == "error"
    assert choices == []


@pytest.mark.asyncio
async def test_fuente_be_detalle_resuelve_ou_con_atribucion(session, monkeypatch):
    """E2E: dos picks del mismo fixture — la fila O/U se atribuye al
    pick O/U y la de Full time al pick ganador (bug histórico del
    trigger_pick)."""
    src = _BetExplorerSource()
    row = sp.StaticMatch(
        home_name="Malaga",
        away_name="Deportivo La Coruna",
        start=datetime(2026, 10, 3, 19, 30),
        ext_id="betexplorer:hGycdKve",
        tournament="",
        odds1=2.10,
        oddsX=3.30,
        odds2=2.25,
    )
    monkeypatch.setattr(src, "_days", {date(2026, 10, d): [row] for d in (2, 3, 4)})

    async def fake_market(http, code, bettype):
        return BE_OU_DETAIL_HTML if bettype == "ou" else ""

    monkeypatch.setattr(sp, "fetch_betexplorer_market", fake_market)
    # apply=True toca provider_state (marks): se neutraliza para que
    # el test no escriba el archivo real.
    monkeypatch.setattr(hb, "is_missed", lambda key: False)
    monkeypatch.setattr(hb, "mark_missed", lambda key: None)
    monkeypatch.setattr(hb, "clear_missed", lambda key: None)
    pick_ou = ParsedPick(
        id=21,
        raw_message_id=1,
        es_apuesta=True,
        deporte="fútbol",
        evento="Malaga - Deportivo",
        mercado="over/under goles",
        seleccion="más de 2.5 goles",
        linea=2.5,
        cuota=1.90,
        fecha_evento=datetime(2026, 10, 3),
    )
    pick_w = ParsedPick(
        id=22,
        raw_message_id=1,
        es_apuesta=True,
        deporte="fútbol",
        evento="Malaga - Deportivo",
        mercado="ganador",
        seleccion="Malaga gana",
        cuota=2.0,
        fecha_evento=datetime(2026, 10, 3),
    )
    report = BackfillReport()
    await _run_source(src, [pick_ou, pick_w], set(), set(), session, None, True, report)

    assert report.mapeados == 2
    from sqlmodel import select

    result = await session.exec(
        select(OddsSnapshot).where(OddsSnapshot.event_ext_id == "betexplorer:hGycdKve")
    )
    written = result.all()
    by_pick = {
        (s.parsed_pick_id, s.market_name, s.choice_name, s.choice_group)
        for s in written
    }
    assert (21, "Match goals", "Over", "2.5") in by_pick
    assert (22, "Full time", "1", None) in by_pick


def test_be_liga_parse_temporada():
    """La página /results/ de una liga trae la temporada entera con
    `data-odd` y fecha "DD.MM." (año inferido del calendario)."""
    from app.services.odds.static_pages import _parse_betexplorer_league

    rows, suspicious = _parse_betexplorer_league(BE_LEAGUE_HTML, "spain laliga2")
    assert not suspicious
    assert len(rows) == 2
    r = rows[0]
    assert r.home_name == "Malaga"
    assert r.away_name == "Deportivo La Coruna"
    assert (r.odds1, r.oddsX, r.odds2) == (2.10, 3.30, 2.25)
    assert r.ext_id == "betexplorer:hGycdKve"
    assert r.start.date() == date(2026, 9, 15)


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
