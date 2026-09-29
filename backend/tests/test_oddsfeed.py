"""Tests de `oddsfeed.py` (OddsFeed / odds-feed — backfill histórico).

Funciones puras de traducción/matching — sin HTTP. El test de
integración pasa las filas virtuales por `map_pick_choices` (la misma
ruta que el backfill) para garantizar que el vocabulario traducido
casa con el comparador real.
"""

from datetime import datetime

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds.compare import map_pick_choices
from app.services.odds.oddsfeed import (
    FeedFixture,
    event_ext_id,
    match_fixture,
    parse_event,
    points_from_history,
    translate_market_books,
)

_EVENT_RAW = {
    "id": 700926,
    "sport": {"id": 1, "name": "Football", "slug": "football"},
    "category": {"id": 2, "name": "Spain", "slug": "spain"},
    "tournament": {"id": 1, "name": "La Liga"},
    "team_home": {"id": 1, "name": "Sevilla FC"},
    "team_away": {"id": 2, "name": "FC Barcelona"},
    "status": "FINISHED",
    "start_at": "2026-09-15 18:00:00",
}

_FIXTURE = FeedFixture(
    event_id=700926,
    home_name="Sevilla FC",
    away_name="FC Barcelona",
    start=datetime(2026, 9, 15, 18, 0),
    sport_id=1,
)


def _market(market_name, value=None, books=None, period="FULL_TIME"):
    return {
        "market_name": market_name,
        "value": value,
        "period": period,
        "placing": "PREMATCH",
        "market_books": books or [{"market_book_id": 1, "book": "BET365"}],
    }


class TestParseEvent:
    def test_parse_completo(self):
        fixture = parse_event(_EVENT_RAW, 1)
        assert fixture is not None
        assert fixture.event_id == 700926
        assert fixture.home_name == "Sevilla FC"
        assert fixture.start == datetime(2026, 9, 15, 18, 0)

    def test_sin_nombres_devuelve_none(self):
        raw = dict(_EVENT_RAW, team_home={"id": 1, "name": ""})
        assert parse_event(raw, 1) is None

    def test_event_ext_id(self):
        assert event_ext_id(_FIXTURE) == "oddsfeed:700926"


class TestMatchFixture:
    def test_por_nombres_en_fecha(self):
        found = match_fixture(
            [_FIXTURE], "Sevilla - Barcelona", datetime(2026, 9, 15, 20, 0)
        )
        assert found is _FIXTURE

    def test_fuera_de_fecha_no_casa(self):
        found = match_fixture(
            [_FIXTURE], "Sevilla - Barcelona", datetime(2026, 9, 20, 20, 0)
        )
        assert found is None


class TestTranslateMarketBooks:
    def test_1x2_a_full_time(self):
        books = translate_market_books(
            [_market("1X2")], "futbol", "Sevilla FC", "FC Barcelona"
        )
        assert len(books) == 1
        book, defs = books[0]
        assert book.translated == "Full time"
        assert [(i, n, g) for i, n, g in defs] == [
            (0, "1", None),
            (1, "X", None),
            (2, "2", None),
        ]

    def test_home_away_tenis_2way(self):
        books = translate_market_books(
            [_market("HOME_AWAY")], "tenis", "Ayeni A.", "Bassem M."
        )
        book, defs = books[0]
        assert book.translated == "Full time"
        assert [(i, n) for i, n, _ in defs] == [(0, "1"), (1, "2")]

    def test_over_under_futbol_linea_en_grupo(self):
        books = translate_market_books(
            [_market("OVER_UNDER", value=2.5)], "futbol", "A", "B"
        )
        book, defs = books[0]
        assert book.translated == "Match goals"
        assert [(i, n, g) for i, n, g in defs] == [
            (0, "Over", "2.5"),
            (1, "Under", "2.5"),
        ]

    def test_over_under_tenis_es_juegos(self):
        books = translate_market_books(
            [_market("OVER_UNDER", value=22.5)], "tenis", "A", "B"
        )
        assert books[0][0].translated == "Total games won"

    def test_handicap_formato_sofascore(self):
        books = translate_market_books(
            [_market("ASIAN_HANDICAP", value=-0.5)],
            "futbol",
            "Sevilla FC",
            "FC Barcelona",
        )
        book, defs = books[0]
        assert book.translated == "Asian handicap"
        nombres = {n for _, n, _ in defs}
        assert "(-0.5) Sevilla FC" in nombres
        assert "(0.5) FC Barcelona" in nombres

    def test_btts_si_no(self):
        books = translate_market_books(
            [_market("BOTH_TEAMS_TO_SCORE")], "futbol", "A", "B"
        )
        book, defs = books[0]
        assert book.translated == "Both teams to score"
        assert [(i, n) for i, n, _ in defs] == [(0, "Yes"), (1, "No")]

    def test_periodo_1a_parte_se_descarta(self):
        books = translate_market_books(
            [_market("1X2", period="HALF_TIME")], "futbol", "A", "B"
        )
        assert books == []

    def test_mercado_desconocido_se_omite(self):
        books = translate_market_books([_market("PLAYER_PROPS")], "futbol", "A", "B")
        assert books == []

    def test_preferencia_bet365(self):
        books = translate_market_books(
            [
                _market(
                    "1X2",
                    books=[
                        {"market_book_id": 1, "book": "PINNACLE"},
                        {"market_book_id": 2, "book": "BET365"},
                    ],
                )
            ],
            "futbol",
            "A",
            "B",
        )
        assert books[0][0].book == "BET365"


class TestPointsFromHistory:
    def test_curva_ordenada(self):
        history = [
            {
                "change_at": "2026-09-15 10:00:00",
                "outcome_0": 3.6,
                "is_open": True,
            },
            {
                "change_at": "2026-09-14 10:00:00",
                "outcome_0": 3.4,
                "is_open": True,
            },
        ]
        points = points_from_history(history, 0)
        assert [p.cuota for p in points] == [3.4, 3.6]
        assert points[0].captured_at == datetime(2026, 9, 14, 10, 0)

    def test_nulos_se_omiten(self):
        history = [
            {"change_at": "2026-09-14 10:00:00", "outcome_0": None},
            {"change_at": "2026-09-14 11:00:00", "outcome_0": 2.0},
        ]
        points = points_from_history(history, 0)
        assert [p.cuota for p in points] == [2.0]


class TestIntegracionCompare:
    """La ruta real del backfill: filas virtuales -> map_pick_choices."""

    def _virtual_rows(self, books, fixture=_FIXTURE, sport="futbol"):
        rows = []
        for book, defs in books:
            for idx, choice_name, choice_group in defs:
                rows.append(
                    OddsSnapshot(
                        provider="oddsfeed",
                        event_ext_id=event_ext_id(fixture),
                        market_name=book.translated,
                        choice_name=choice_name,
                        choice_group=choice_group,
                        cuota=book.current.get(idx) or 2.0,
                        captured_at=fixture.start,
                    )
                )
        return rows

    def _event(self, fixture=_FIXTURE, sport="futbol"):
        return OddsEvent(
            event_ext_id=event_ext_id(fixture),
            sport=sport,
            home_team=fixture.home_name,
            away_team=fixture.away_name,
            start=fixture.start,
        )

    def test_pick_ganador_casa_opcion_1(self):
        books = translate_market_books(
            [
                _market(
                    "1X2",
                    books=[
                        {
                            "market_book_id": 1,
                            "book": "BET365",
                            "outcome_0": 3.4,
                            "outcome_1": 3.3,
                            "outcome_2": 2.1,
                        }
                    ],
                )
            ],
            "futbol",
            "Sevilla FC",
            "FC Barcelona",
        )
        pick = ParsedPick(
            mercado="ganador",
            seleccion="Sevilla gana",
            deporte="fútbol",
            es_apuesta=True,
        )
        matched = map_pick_choices(pick, self._event(), self._virtual_rows(books))
        assert matched is not None
        assert matched.market_name == "Full time"
        assert matched.choice_name == "1"
        assert matched.rows[-1].cuota == 3.4

    def test_pick_over_casa_linea(self):
        books = translate_market_books(
            [
                _market(
                    "OVER_UNDER",
                    value=2.5,
                    books=[
                        {
                            "market_book_id": 1,
                            "book": "BET365",
                            "outcome_0": 1.9,
                            "outcome_1": 1.9,
                        }
                    ],
                )
            ],
            "futbol",
            "Sevilla FC",
            "FC Barcelona",
        )
        pick = ParsedPick(
            mercado="over/under",
            seleccion="Más de 2.5 goles",
            linea=2.5,
            deporte="fútbol",
            es_apuesta=True,
        )
        matched = map_pick_choices(pick, self._event(), self._virtual_rows(books))
        assert matched is not None
        assert matched.market_name == "Match goals"
        assert matched.choice_name == "Over"
        assert matched.choice_group == "2.5"
