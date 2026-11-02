"""Tests de `odds/football_csv.py` — la fuente CSV gratis de
football-data.co.uk (fixtures de temporada + cuotas 1X2/O-U/AH)."""

from datetime import date, datetime

from app.services.odds.football_csv import (
    _OPENING_ANCHOR,
    FootballFixture,
    event_ext_id,
    match_row,
    parse_csv,
    row_choices,
    season_codes,
)

_HEADER = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
    "B365H,B365D,B365A,PSH,PSD,PSA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,"
    "B365>2.5,B365<2.5,P>2.5,P<2.5,Max>2.5,Max<2.5,Avg>2.5,Avg<2.5,"
    "AHh,B365AHH,B365AHA,PAHH,PAHA,MaxAHH,MaxAHA,AvgAHH,AvgAHA,"
    "B365CH,B365CD,B365CA,PSCH,PSCD,PSCA,MaxCH,MaxCD,MaxCA,"
    "AvgCH,AvgCD,AvgCA,"
    "B365C>2.5,B365C<2.5,PC>2.5,PC<2.5,MaxC>2.5,MaxC<2.5,"
    "AvgC>2.5,AvgC<2.5,"
    "AHCh,B365CAHH,B365CAHA,PCAHH,PCAHA,MaxCAHH,MaxCAHA,"
    "AvgCAHH,AvgCAHA"
)

# Girona-Vallecano con cierre Pinnacle 2.10/3.40/3.60, apertura B365
# 2.25/3.25/3.30, O/U 2.5 cierre Pinnacle 1.97/1.95 y AH -0.25 Pinnacle
# 1.97/1.95. Se construye por dict columna->valor para no contar comas.
_ROW_VALUES = {
    "Div": "SP1",
    "Date": "15/08/2026",
    "Time": "18:00",
    "HomeTeam": "Girona",
    "AwayTeam": "Vallecano",
    "B365H": "2.25",
    "B365D": "3.25",
    "B365A": "3.30",
    "AHh": "-0.25",
    "B365AHH": "1.98",
    "B365AHA": "1.88",
    "B365CH": "2.20",
    "B365CD": "3.20",
    "B365CA": "3.50",
    "PSCH": "2.10",
    "PSCD": "3.40",
    "PSCA": "3.60",
    "PC>2.5": "1.97",
    "PC<2.5": "1.95",
    "AHCh": "-0.25",
    "B365CAHH": "1.99",
    "B365CAHA": "1.98",
    "PCAHH": "1.97",
    "PCAHA": "1.95",
}
_ROW = ",".join(_ROW_VALUES.get(col, "") for col in _HEADER.split(","))

_EXTRA_HEADER = (
    "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,"
    "PSCH,PSCD,PSCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,"
    "BFECH,BFECD,BFECA,B365CH,B365CD,B365CA"
)
_EXTRA_ROW = (
    "Argentina,Liga Profesional,2026,21/09/2026,01:15,"
    "River Plate,Huracan,2,0,H,"
    "1.50,4.00,7.00,1.55,4.20,7.50,1.45,3.90,6.80,"
    "1.52,4.05,7.10,1.48,3.95,6.90"
)


def _fixture(**kw) -> FootballFixture:
    base = dict(
        day=date(2026, 8, 15),
        start=datetime(2026, 8, 15, 18, 0),
        home="Girona",
        away="Vallecano",
        league="SP1",
        win_open={"1": None, "X": None, "2": None},
        win_close={"1": None, "X": None, "2": None},
        ou25_open={"Over": None, "Under": None},
        ou25_close={"Over": None, "Under": None},
        ah_line=None,
        ah_open={"home": None, "away": None},
        ah_close={"home": None, "away": None},
    )
    base.update(kw)
    return FootballFixture(**base)


def test_season_codes():
    # Temporada 26-27: jul-dic de 2026 y ene-jun de 2027, con la
    # anterior y la siguiente para el skew del repost.
    assert season_codes(date(2026, 9, 12)) == ["2526", "2627", "2728"]
    assert season_codes(date(2027, 3, 1)) == ["2526", "2627", "2728"]
    assert season_codes(date(2026, 7, 15)) == ["2526", "2627", "2728"]


def test_parse_csv_main_league():
    rows = parse_csv(_HEADER + "\n" + _ROW, "SP1")
    assert len(rows) == 1
    row = rows[0]
    assert row.home == "Girona" and row.away == "Vallecano"
    assert row.day == date(2026, 8, 15)
    assert row.start == datetime(2026, 8, 15, 18, 0)
    # Cierre: Pinnacle gana a B365 en la cadena de preferencia.
    assert row.win_close == {"1": 2.10, "X": 3.40, "2": 3.60}
    assert row.win_open == {"1": 2.25, "X": 3.25, "2": 3.30}
    assert row.ou25_close == {"Over": 1.97, "Under": 1.95}
    assert row.ah_line == -0.25
    assert row.ah_close == {"home": 1.97, "away": 1.95}


def test_parse_csv_extra_league_only_winner():
    rows = parse_csv(_EXTRA_HEADER + "\n" + _EXTRA_ROW, "ARG")
    assert len(rows) == 1
    row = rows[0]
    assert row.home == "River Plate" and row.away == "Huracan"
    # Extra: solo 1X2 de cierre — Pinnacle primero.
    assert row.win_close["1"] == 1.50
    assert row.win_open["1"] is None
    assert row.ou25_close["Over"] is None
    assert row.ah_line is None


def test_event_ext_id_estable():
    row = _fixture()
    assert event_ext_id(row) == "footballdata:2026-08-15:girona|vallecano"


def test_match_row_casa_aunque_la_fecha_guarde_el_repost():
    """La fecha del pick es la del repost (una semana después): el
    fixture real se encuentra igual dentro de la ventana."""
    rows = [_fixture()]
    hit = match_row(rows, "Girona - Vallecano", datetime(2026, 8, 22))
    assert hit is rows[0]


def test_match_row_fuera_de_ventana_no_casa():
    """Fuera de ±60 días no casa: dos equipos se reencuentran cada
    temporada y el pick podría ser del próximo partido."""
    rows = [_fixture()]
    assert match_row(rows, "Girona - Vallecano", datetime(2026, 12, 1)) is None


def test_match_row_exige_los_dos_lados():
    """Un solo equipo no basta: 'Girona - Betis' no casa con
    'Girona v Vallecano'."""
    rows = [_fixture()]
    assert match_row(rows, "Girona - Betis", datetime(2026, 8, 15)) is None


def test_match_row_empate_de_nota_gana_la_fecha_cercana():
    """Dos jornadas del mismo emparejamiento dentro de la ventana
    (p. ej. liga + copa): elige la más cercana a la fecha guardada."""
    ida = _fixture()
    vuelta = _fixture(
        day=date(2026, 10, 5),
        start=datetime(2026, 10, 5, 20, 0),
        league="SP1",
    )
    rows = [ida, vuelta]
    assert match_row(rows, "Girona v Vallecano", datetime(2026, 8, 20)) is ida
    assert match_row(rows, "Girona v Vallecano", datetime(2026, 9, 15)) is vuelta


def test_row_choices_mercados_canonicos():
    row = parse_csv(_HEADER + "\n" + _ROW, "SP1")[0]
    choices = row_choices(row)
    by_name = {(c.market_name, c.choice_name): c for c in choices}
    assert ("Full time", "1") in by_name
    assert ("Full time", "X") in by_name
    assert ("Full time", "2") in by_name
    win = by_name[("Full time", "1")]
    assert win.choice_group is None
    # Dos puntos: apertura anclada a inicio-7d y cierre al inicio.
    assert [p.cuota for p in win.points] == [2.25, 2.10]
    assert win.points[0].captured_at == row.start - _OPENING_ANCHOR
    assert win.points[1].captured_at == row.start
    ou = by_name[("Match goals", "Over")]
    assert ou.choice_group == "2.5"
    assert [p.cuota for p in ou.points] == [1.97]
    ah_home = by_name[("Asian handicap", "(-0.25) Girona")]
    assert ah_home.choice_group == "-0.25"
    ah_away = by_name[("Asian handicap", "(0.25) Vallecano")]
    assert ah_away.choice_group == "0.25"


def test_row_choices_vacio_sin_cuotas():
    assert row_choices(_fixture()) == []
