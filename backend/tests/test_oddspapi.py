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
    load_markets,
    load_payload,
    match_fixture,
    parse_fixture,
    save_markets,
    save_payload,
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
    3000: MarketDef(
        market_name="Corners - Over Under Team 1",
        market_type="teamtotals-corners-team1",
        period="fulltime",
        handicap=7.5,
        outcomes={3000: "Over", 3001: "Under"},
    ),
    3100: MarketDef(
        market_name="Corners - Over Under Full Time",
        market_type="totals-corners",
        period="fulltime",
        handicap=7.5,
        outcomes={3100: "Over", 3101: "Under"},
    ),
}


def _point(ts: str, price: float, active: bool = True) -> dict:
    return {"createdAt": ts, "price": price, "limit": None, "active": active}


# Partido con la MISMA línea (7.5) publicada como total de equipo y
# total del partido: prueba que el comparador no los mezcla.
_CORNER_HISTORY = {
    "bookmakers": {
        "bet365": {
            "markets": {
                "3000": {
                    "outcomes": {
                        "3000": {
                            "players": {"0": [_point("2026-09-14T12:00:00.000Z", 1.85)]}
                        }
                    }
                },
                "3100": {
                    "outcomes": {
                        "3100": {
                            "players": {"0": [_point("2026-09-14T12:00:00.000Z", 1.95)]}
                        }
                    }
                },
            }
        }
    }
}


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


class TestMatchFixtureVentanaAncha:
    """Segunda pasada: `fecha_evento` de repost a ±4 días exige nombre
    fuerte — el fixture equivocado envenena la auditoría."""

    def test_repost_a_3_dias_casa_nombre_fuerte(self):
        found = match_fixture(
            [_FIXTURE], "Sevilla - Barcelona", datetime(2026, 9, 18, 12, 0)
        )
        assert found is _FIXTURE

    def test_ventana_ancha_exige_mas_similitud(self):
        # "Sevilla - Valencia": comparte medio nombre pero no llega a
        # 0.75 — la ventana ancha no baja el listón de identidad.
        found = match_fixture(
            [_FIXTURE], "Sevilla - Valencia", datetime(2026, 9, 18, 12, 0)
        )
        assert found is None

    def test_mas_de_4_dias_no_casa(self):
        found = match_fixture(
            [_FIXTURE], "Sevilla - Barcelona", datetime(2026, 9, 20, 12, 0)
        )
        assert found is None

    def test_ventana_estricta_tiene_prioridad(self):
        """Fixture a ±1 día con nombre decente gana a uno a ±3 con
        nombre algo mejor: cerca de la fecha la pasada estricta manda."""
        cerca = OddsPapiFixture(
            "id-cerca",
            None,
            "Sevilla FC",
            "Barcelona B",
            datetime(2026, 9, 15, 20, 0),
            10,
        )
        lejos = OddsPapiFixture(
            "id-lejos",
            None,
            "Sevilla FC",
            "FC Barcelona",
            datetime(2026, 9, 12, 18, 0),
            10,
        )
        found = match_fixture(
            [lejos, cerca], "Sevilla - Barcelona", datetime(2026, 9, 15, 12, 0)
        )
        assert found is cerca

    def test_desempate_ancha_por_cercania(self):
        """Dos fixtures con igual nombre en la ventana ancha: gana el
        de la fecha más cercana al repost."""
        dia3 = OddsPapiFixture(
            "id-3",
            None,
            "Sevilla FC",
            "FC Barcelona",
            datetime(2026, 9, 12, 18, 0),
            10,
        )
        dia2 = OddsPapiFixture(
            "id-2",
            None,
            "Sevilla FC",
            "FC Barcelona",
            datetime(2026, 9, 17, 18, 0),
            10,
        )
        found = match_fixture(
            [dia3, dia2], "Sevilla - Barcelona", datetime(2026, 9, 15, 12, 0)
        )
        assert found is dia2


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

    def test_total_corners_equipo_se_traduce(self):
        history = {
            "bookmakers": {
                "bet365": {
                    "markets": {
                        "3000": {
                            "outcomes": {
                                "3000": {
                                    "players": {
                                        "0": [_point("2026-09-14T12:00:00.000Z", 1.9)]
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        choices = translate_history(history, _MARKETS, "Sevilla FC", "FC Barcelona")
        team = [c for c in choices if c.market_name == "Team 1 corners"]
        assert len(team) == 1
        assert team[0].choice_name == "Over"
        assert team[0].choice_group == "7.5"

    def test_payload_vacio(self):
        assert translate_history({}, _MARKETS, "A", "B") == []


class TestPersistencia:
    """Payloads y catálogo a disco: re-traducir no vuelve a pagar la
    llamada de API."""

    def test_payload_roundtrip(self, tmp_path):
        save_payload(_FIXTURE, "sofascore:17085930", _HISTORY, base=tmp_path)
        envelope = load_payload("sofascore:17085930", base=tmp_path)
        assert envelope is not None
        assert envelope["payload"] == _HISTORY
        assert envelope["home_name"] == "Sevilla FC"
        assert envelope["fixture_id"] == "id1200390574632744"

    def test_payload_inexistente_devuelve_none(self, tmp_path):
        assert load_payload("sofascore:1", base=tmp_path) is None

    def test_markets_roundtrip(self, tmp_path):
        save_markets(_MARKETS, base=tmp_path)
        loaded = load_markets(base=tmp_path)
        assert loaded is not None
        assert loaded[101].market_name == "Full Time Result"
        assert loaded[101].outcomes == {101: "1", 102: "X", 103: "2"}
        assert loaded[3000].handicap == 7.5

    def test_payload_corrupto_devuelve_none(self, tmp_path):
        path = tmp_path / "payloads" / "sofascore_9.json"
        path.parent.mkdir(parents=True)
        path.write_text("{no json", encoding="utf-8")
        assert load_payload("sofascore:9", base=tmp_path) is None


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

    def _corner_event(self):
        return OddsEvent(
            event_ext_id="sofascore:17085930",
            sport="futbol",
            home_team="Sevilla FC",
            away_team="FC Barcelona",
            start=datetime(2026, 9, 15, 18, 0),
        )

    def test_corners_de_equipo_casan_mercado_de_equipo(self):
        """'Sevilla +7.5 córners' casa con Team 1 corners, NO con el
        total del partido aunque exista la misma línea 7.5."""
        choices = translate_history(
            _CORNER_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona"
        )
        pick = ParsedPick(
            mercado="córners",
            seleccion="Sevilla FC más de 7.5 córners",
            linea=7.5,
            deporte="fútbol",
            es_apuesta=True,
        )
        matched = map_pick_choices(
            pick, self._corner_event(), self._virtual_rows(choices)
        )
        assert matched is not None
        assert matched.market_name == "Team 1 corners"
        assert matched.choice_name == "Over"
        assert matched.rows[-1].cuota == 1.85

    def test_corners_sin_equipo_casan_total_partido(self):
        choices = translate_history(
            _CORNER_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona"
        )
        pick = ParsedPick(
            mercado="córners",
            seleccion="Más de 7.5 córners",
            linea=7.5,
            deporte="fútbol",
            es_apuesta=True,
        )
        matched = map_pick_choices(
            pick, self._corner_event(), self._virtual_rows(choices)
        )
        assert matched is not None
        assert matched.market_name == "Corners 2-Way"
        assert matched.rows[-1].cuota == 1.95

    def test_corners_equipo_sin_mercado_equipo_no_cae_al_total(self):
        """Guard anti-falso-positivo: pick de equipo sin mercado de
        equipo publicado devuelve None — nunca el total del partido."""
        choices = translate_history(
            _CORNER_HISTORY, _MARKETS, "Sevilla FC", "FC Barcelona"
        )
        choices = [c for c in choices if not c.market_name.startswith("Team")]
        pick = ParsedPick(
            mercado="córners",
            seleccion="Sevilla FC más de 7.5 córners",
            linea=7.5,
            deporte="fútbol",
            es_apuesta=True,
        )
        matched = map_pick_choices(
            pick, self._corner_event(), self._virtual_rows(choices)
        )
        assert matched is None
