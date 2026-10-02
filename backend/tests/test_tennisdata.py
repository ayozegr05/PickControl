"""Tests de `odds/tennisdata.py` — el CSV/XLSX anual gratuito de
tennis-data.co.uk (ganador ATP/WTA: Pinnacle/B365), primera fuente de
la cascada de backfill porque no gasta cuota de nada.
"""

from datetime import date, datetime
from io import BytesIO

import pytest

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds import tennisdata
from app.services.odds.compare import map_pick_choices
from app.services.odds.historical_backfill import (
    BackfillReport,
    _run_source,
    _TennisDataSource,
)


def _xlsx(rows: list[list]) -> bytes:
    """Un XLSX mínimo con la cabecera real de tennis-data."""
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(
        [
            "ATP",
            "Location",
            "Tournament",
            "Date",
            "Series",
            "Court",
            "Surface",
            "Round",
            "Best of",
            "Winner",
            "Loser",
            "WRank",
            "LRank",
            "WPts",
            "LPts",
            "W1",
            "L1",
            "W2",
            "L2",
            "W3",
            "L3",
            "W4",
            "L4",
            "W5",
            "L5",
            "Wsets",
            "Lsets",
            "Comment",
            "B365W",
            "B365L",
            "PSW",
            "PSL",
            "MaxW",
            "MaxL",
            "AvgW",
            "AvgL",
        ]
    )
    for row in rows:
        sheet.append(row)
    buf = BytesIO()
    book.save(buf)
    return buf.getvalue()


def test_parse_xlsx_cabecera_real():
    content = _xlsx(
        [
            [
                1,
                "Brisbane",
                "Brisbane International",
                datetime(2026, 1, 5),
                "ATP250",
                "Outdoor",
                "Hard",
                "1st Round",
                3,
                "Tiafoe F.",
                "Alcaraz C.",
                20,
                1,
                900,
                5000,
                6,
                4,
                6,
                3,
                None,
                None,
                None,
                None,
                None,
                None,
                2,
                0,
                "Completed",
                3.5,
                1.3,
                3.6,
                1.32,
                3.8,
                1.35,
                3.55,
                1.31,
            ],
        ]
    )
    rows = tennisdata.parse_xlsx(content, "atp")
    assert len(rows) == 1
    row = rows[0]
    assert row.day == date(2026, 1, 5)
    assert row.winner == "Tiafoe F." and row.loser == "Alcaraz C."
    assert row.winner_odds["ps"] == 3.6 and row.loser_odds["ps"] == 1.32


def test_parse_xlsx_fecha_serial_y_texto():
    content = _xlsx(
        [
            [
                1,
                "X",
                "Torneo",
                46000,
                "ATP250",
                "Outdoor",
                "Hard",
                "F",
                3,
                "Sinner J.",
                "Medvedev D.",
                1,
                3,
                0,
                0,
                6,
                4,
                6,
                2,
                None,
                None,
                None,
                None,
                None,
                None,
                2,
                0,
                "Completed",
                1.5,
                2.6,
                1.55,
                2.7,
                1.6,
                2.8,
                1.52,
                2.65,
            ],
            [
                1,
                "X",
                "Torneo",
                "15/03/2026",
                "ATP250",
                "Outdoor",
                "Hard",
                "F",
                3,
                "Zverev A.",
                "Ruud C.",
                2,
                5,
                0,
                0,
                6,
                4,
                6,
                2,
                None,
                None,
                None,
                None,
                None,
                None,
                2,
                0,
                "Completed",
                1.4,
                3.0,
                1.45,
                3.1,
                1.5,
                3.2,
                1.42,
                3.05,
            ],
        ]
    )
    rows = tennisdata.parse_xlsx(content, "atp")
    assert rows[0].day == date(2025, 12, 9)  # serial Excel 46000
    assert rows[1].day == date(2026, 3, 15)


def _row(winner="Sinner J.", loser="Alcaraz C.", psw=1.65, psl=2.3):
    return tennisdata.TennisMatch(
        day=date(2026, 9, 20),
        winner=winner,
        loser=loser,
        tournament="Beijing",
        circuit="atp",
        winner_odds={"ps": psw, "b365": None, "avg": None, "max": None},
        loser_odds={"ps": psl, "b365": None, "avg": None, "max": None},
    )


def test_match_row_hint_completo():
    row = tennisdata.match_row([_row()], "Sinner vs Alcaraz")
    assert row is not None and row.winner == "Sinner J."


def test_match_row_solo_apellido():
    """Hint de un jugador ("Sinner"): casa contra cualquiera de los dos
    lados — el partido de ese día ya filtra el evento."""
    row = tennisdata.match_row([_row()], "alcaraz")
    assert row is not None


def test_match_row_rechaza_otro_partido():
    assert tennisdata.match_row([_row()], "Nadal vs Federer") is None


def test_row_choices_pinnacle_primero():
    row = _row()
    row.winner_odds = {"ps": None, "b365": 1.62, "avg": 1.6, "max": 1.7}
    choices = tennisdata.row_choices(row)
    assert [c.choice_name for c in choices] == ["1", "2"]
    assert choices[0].points[0].cuota == 1.62  # fallback B365 sin PS


@pytest.mark.asyncio
async def test_fuente_resuelve_pick_ganador(session, monkeypatch):
    """Integración: la fuente casa el pick por fecha+apellido y mapea
    el punto Pinnacle como 'Full time' — sin una sola llamada HTTP."""
    src = _TennisDataSource()
    row = _row()
    monkeypatch.setattr(src, "_years", {2026: [row]})

    pick = ParsedPick(
        id=1,
        es_apuesta=True,
        deporte="tenis",
        evento="Sinner vs Alcaraz",
        mercado="ganador",
        seleccion="Sinner gana",
        cuota=1.80,
        fecha_evento=datetime(2026, 9, 20),
    )
    report = BackfillReport()
    await _run_source(src, [pick], set(), set(), session, None, False, report)

    assert report.con_fixture == 1
    assert report.mapeados == 1
    assert report.filas == 1  # un punto, solo la opción casada ("1")
    assert "tipster 1.8 vs mercado 1.65" in report.detalles[-1]
    assert "[tennisdata]" in report.detalles[-1]


def test_map_pick_choices_contra_filas_tennisdata():
    """El comparador casa 'Sinner gana' con la opción '1' del evento
    orientado winner/loser del archivo."""
    row = _row()
    event = OddsEvent(
        event_ext_id=tennisdata.event_ext_id(row),
        sport="tenis",
        home_team=row.home_name,
        away_team=row.away_name,
        start=row.start,
    )
    picks = [
        ParsedPick(
            deporte="tenis",
            mercado="ganador",
            seleccion="Sinner gana",
            cuota=1.7,
        ),
        ParsedPick(
            deporte="tenis",
            mercado="ganador",
            seleccion="Alcaraz",
            cuota=2.1,
        ),
    ]
    virtual = [
        OddsSnapshot(
            provider="tennisdata",
            event_ext_id=event.event_ext_id,
            market_name=c.market_name,
            choice_name=c.choice_name,
            choice_group=c.choice_group,
            cuota=p.cuota,
            captured_at=p.captured_at,
        )
        for c in tennisdata.row_choices(row)
        for p in c.points
    ]
    m1 = map_pick_choices(picks[0], event, virtual)
    m2 = map_pick_choices(picks[1], event, virtual)
    assert m1 is not None and m1.choice_name == "1" and m1.rows[0].cuota == 1.65
    assert m2 is not None and m2.choice_name == "2" and m2.rows[0].cuota == 2.3


@pytest.mark.asyncio
async def test_fuente_404_da_lista_vacia_no_error():
    """Un año sin archivo no es fallo de API: devuelve [] y los picks
    cuentan como sin_fixture (marcados), no sin_revisar."""
    from app.services.odds import tennisdata as td

    class _Resp:
        status_code = 404
        content = b""

    class _Http:
        async def get(self, url, headers=None, timeout=None):
            return _Resp()

    rows = await td.fetch_year(_Http(), 1995)
    assert rows == []
