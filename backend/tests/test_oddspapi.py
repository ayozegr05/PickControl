"""Tests de `oddspapi.py` (OddsPapi / bet36528 — backfill histórico).

Las funciones de traducción/matching son puras: no hace falta HTTP.
El test de integración pasa las filas virtuales por `map_pick_choices`
— la misma ruta que el script y que `compare.py` — para garantizar que
el vocabulario traducido casa con el comparador real.
"""

from datetime import datetime

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds.compare import map_pick_choices
from app.services.odds.oddspapi import (
    MarketDef,
    OddsPapiFixture,
    PricePoint,
    downsample,
    event_ext_id,
    match_fixture,
    parse_fixture,
    translate_history,
)

_FIXTURE_RAW = {
    "fixtureId": "id1200390574632744",
    "participant1Id": 284801,
    "participant2Id": 671229,
    "sportId": 10,
    "tournamentId": 3905,
    "startTime": "2026-09-15T18:00:00.000Z",
    "statusName": "Finished",
    "participant1Name": "Sevilla FC",
    "participant2Name": "FC Barcelona",
    "externalProviders": {"sofascoreId": 17085930, "betradarId": 74632744},
}

_FIXTURE = OddsPapiFixture(
    fixture_id="id1200390574632744",
    sofascore_id=17085930,
    home_name="Sevilla FC",
    away_name="FC Barcelona",
    start=datetime(2026, 9, 15, 18, 0),
    sport_id=10,
)

_MARKETS = {
    101: MarketDef(
        market_name="Full Time Result",
        market_type="1x2",
        period="fulltime",
        handicap=0.0,
        outcomes={101: "1", 102: "X", 103: "2"},
    ),
    1010: MarketDef(
        market_name="Over Under Full Time",
        market_type="totals",
        period="fulltime",
        handicap=2.5,
        outcomes={1010: "Over", 1011: "Under"},
    ),
    1068: MarketDef(
        market_name="Asian Handicap",
        market_type="spreads",
        period="fulltime",
        handicap=-0.5,
        outcomes={1068: "1", 1069: "2"},
    ),
    101902: MarketDef(
        market_name="Double Chance Full Time",
        market_type="doublechance",
        period="fulltime",
        handicap=0.0,
        outcomes={101902: "1X", 101903: "12", 101904: "2X"},
    ),
    999: MarketDef(
        market_name="Mercado Sin Traduccion",
        market_type="rarissimo",
        period="fulltime",
        handicap=0.0,
        outcomes={999: "A"},
    ),
}


def _point(ts: str, price: float, active: bool = True) -> dict:
    return {"createdAt": ts, "price": price, "limit": None, "active": active}


_HISTORY = {
    "fixtureId": "id1200390574632744",
    "bookmakers": {
        "bet365": {
            "markets": {
                "101": {
                    "outcomes": {
                        "101": {
                            "players": {
                                "0": [
                                    _point("2026-09-14T10:00:00.000Z", 3.4),
                                    _point("2026-09-15T09:00:00.000Z", 3.6),
                                ]
                            }
                        },
                        "102": {
                            "players": {"0": [_point("2026-09-14T10:00:00.000Z", 3.3)]}
                        },
                        "103": {
                            "players": {"0": [_point("2026-09-14T10:00:00.000Z", 2.1)]}
                        },
                    }
                },
                "1010": {
                    "outcomes": {
                        "1010": {
                            "players": {"0": [_point("2026-09-14T11:00:00.000Z", 1.9)]}
                        },
                        "1011": {
                            "players": {"0": [_point("2026-09-14T11:00:00.000Z", 1.9)]}
                        },
                    }
                },
                "1068": {
                    "outcomes": {
                        "1068": {
                            "players": {"0": [_point("2026-09-14T12:00:00.000Z", 1.85)]}
                        },
                        "1069": {
                            "players": {"0": [_point("2026-09-14T12:00:00.000Z", 1.95)]}
                        },
                    }
                },
                "101902": {
                    "outcomes": {
                        "101904": {
                            "players": {"0": [_point("2026-09-14T12:00:00.000Z", 1.4)]}
                        }
                    }
                },
                "999": {
                    "outcomes": {
                        "999": {
                            "players": {"0": [_point("2026-09-14T12:00:00.000Z", 5.0)]}
                        }
                    }
                },
            }
        }
    },
}


class TestParseFixture:
    def test_parse_completo(self):
        fixture = parse_fixture(_FIXTURE_RAW, 10)
        assert fixture is not None
        assert fixture.fixture_id == "id1200390574632744"
        assert fixture.sofascore_id == 17085930
        assert fixture.home_name == "Sevilla FC"
        assert fixture.start == datetime(2026, 9, 15, 18, 0)

    def test_sin_nombres_devuelve_none(self):
        raw = dict(_FIXTURE_RAW, participant1Name=None)
        assert parse_fixture(raw, 10) is None

    def test_event_ext_id_espacio_sofascore(self):
        assert event_ext_id(_FIXTURE) == "sofascore:17085930"
        sin_sofa = OddsPapiFixture("id1", None, "A", "B", None, 10)
        assert event_ext_id(sin_sofa) == "oddspapi:id1"


class TestMatchFixture:
    def test_por_sofascore_id(self):
        otro = OddsPapiFixture("id2", 999, "X", "Y", None, 10)
        assert (
            match_fixture([otro, _FIXTURE], "", datetime(2026, 9, 15), 17085930)
            is _FIXTURE
        )

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

    def test_nombre_sin_relacion_no_casa(self):
        found = match_fixture(
            [_FIXTURE], "Alavés - Valencia", datetime(2026, 9, 15, 20, 0)
        )
        assert found is None


class TestTranslateHistory:
    def test_1x2_a_full_time(self):
        choices = translate_history(_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona")
        ft = [c for c in choices if c.market_name == "Full time"]
        assert {c.choice_name for c in ft} == {"1", "X", "2"}
        home = next(c for c in ft if c.choice_name == "1")
        assert home.cuota_apertura == 3.4
        # Puntos ordenados por timestamp: apertura 3.4, luego 3.6.
        assert [p.cuota for p in home.points] == [3.4, 3.6]
        assert home.points[0].captured_at == datetime(2026, 9, 14, 10, 0)

    def test_totales_linea_en_choice_group(self):
        choices = translate_history(_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona")
        ou = [c for c in choices if c.market_name == "Match goals"]
        assert {c.choice_name for c in ou} == {"Over", "Under"}
        assert all(c.choice_group == "2.5" for c in ou)

    def test_spread_formato_sofascore(self):
        choices = translate_history(_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona")
        ah = [c for c in choices if c.market_name == "Asian handicap"]
        nombres = {c.choice_name for c in ah}
        assert "(-0.5) Sevilla FC" in nombres
        assert "(0.5) FC Barcelona" in nombres

    def test_doble_oportunidad_alias_2x(self):
        choices = translate_history(_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona")
        dc = [c for c in choices if c.market_name == "Double chance"]
        assert {c.choice_name for c in dc} == {"X2"}

    def test_mercado_sin_traduccion_se_omite(self):
        choices = translate_history(_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona")
        assert all(c.market_name != "Mercado Sin Traduccion" for c in choices)

    def test_payload_vacio(self):
        assert translate_history({}, _MARKETS, "A", "B") == []


class TestDownsample:
    def test_bajo_el_tope_no_toca(self):
        points = [PricePoint(datetime(2026, 9, i + 1), 2.0) for i in range(10)]
        assert downsample(points) is points

    def test_sobre_el_tope_conserva_extremos(self):
        points = [PricePoint(datetime(2026, 9, 1), float(i)) for i in range(1000)]
        sampled = downsample(points)
        assert len(sampled) == 300
        assert sampled[0] is points[0]
        assert sampled[-1] is points[-1]


class TestIntegracionCompare:
    """La ruta real del script: filas virtuales -> map_pick_choices."""

    def _virtual_rows(self, choices):
        return [
            OddsSnapshot(
                provider="oddspapi",
                event_ext_id="sofascore:17085930",
                market_name=c.market_name,
                choice_name=c.choice_name,
                choice_group=c.choice_group,
                cuota=p.cuota,
                cuota_apertura=c.cuota_apertura,
                captured_at=p.captured_at,
            )
            for c in choices
            for p in c.points
        ]

    def test_pick_ganador_casa_opcion_1(self):
        choices = translate_history(_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona")
        event = OddsEvent(
            event_ext_id="sofascore:17085930",
            sport="futbol",
            home_team="Sevilla FC",
            away_team="FC Barcelona",
            start=datetime(2026, 9, 15, 18, 0),
        )
        pick = ParsedPick(
            mercado="ganador",
            seleccion="Sevilla gana",
            deporte="fútbol",
            es_apuesta=True,
        )
        matched = map_pick_choices(pick, event, self._virtual_rows(choices))
        assert matched is not None
        assert matched.market_name == "Full time"
        assert matched.choice_name == "1"
        assert matched.rows[-1].cuota == 3.6

    def test_pick_over_casa_linea(self):
        choices = translate_history(_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona")
        event = OddsEvent(
            event_ext_id="sofascore:17085930",
            sport="futbol",
            home_team="Sevilla FC",
            away_team="FC Barcelona",
        )
        pick = ParsedPick(
            mercado="over/under",
            seleccion="Más de 2.5 goles",
            linea=2.5,
            deporte="fútbol",
            es_apuesta=True,
        )
        matched = map_pick_choices(pick, event, self._virtual_rows(choices))
        assert matched is not None
        assert matched.market_name == "Match goals"
        assert matched.choice_name == "Over"
        assert matched.choice_group == "2.5"
