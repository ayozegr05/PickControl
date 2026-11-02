"""Tests de `oddspapi.py` (OddsPapi / bet36528 — backfill histórico).

Las funciones de traducción/matching son puras: no hace falta HTTP.
El test de integración pasa las filas virtuales por `map_pick_choices`
— la misma ruta que el script y que `compare.py` — para garantizar que
el vocabulario traducido casa con el comparador real.
"""

from datetime import date, datetime

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.services.odds.compare import map_pick_choices
from app.services.odds.oddspapi import (
    MarketDef,
    OddsPapiFixture,
    PricePoint,
    downsample,
    event_ext_id,
    load_fixture_day,
    load_markets,
    load_participant_fixtures,
    load_participants,
    load_payload,
    match_fixture,
    parse_fixture,
    save_fixture_day,
    save_markets,
    save_participant_fixtures,
    save_participants,
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

    def test_bet365_regional_se_une_al_traducir(self):
        """Las cuotas viven en bet365.es/bet365.de...: el slug global
        "bet365" vacío no debe esconder los mercados regionales."""
        history = {
            "bookmakers": {
                "bet365.es": _HISTORY["bookmakers"]["bet365"],
            }
        }
        choices = translate_history(history, _MARKETS, "Sevilla FC", "FC Barcelona")
        assert choices, "bet365.es debía traducir igual que bet365"
        # bet365 global gana cuando compite con la variante regional:
        both = {
            "bookmakers": {
                "bet365.es": {"markets": {"101": {"outcomes": {}}}},
                "bet365": _HISTORY["bookmakers"]["bet365"],
            }
        }
        merged = translate_history(both, _MARKETS, "Sevilla FC", "FC Barcelona")
        assert len(merged) == len(choices)


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

    def test_fixture_day_roundtrip(self, tmp_path):
        """El listado billable de un día pasado se conserva a disco —
        la próxima pasada no vuelve a pagar el mismo rango."""
        day = date(2026, 9, 15)
        save_fixture_day(10, day, [_FIXTURE], base=tmp_path)
        loaded = load_fixture_day(10, day, base=tmp_path)
        assert loaded is not None
        assert loaded[0].fixture_id == "id1200390574632744"
        assert loaded[0].sofascore_id == 17085930
        assert loaded[0].home_name == "Sevilla FC"
        assert loaded[0].start == datetime(2026, 9, 15, 18, 0)
        assert loaded[0].sport_id == 10
        assert load_fixture_day(10, date(2026, 9, 16), base=tmp_path) is None
        # Día sin fixtures: la lista vacía también se conserva.
        save_fixture_day(10, date(2026, 9, 17), [], base=tmp_path)
        assert load_fixture_day(10, date(2026, 9, 17), base=tmp_path) == []

    def test_participants_roundtrip(self, tmp_path):
        """El catálogo equipo->id (1 llamada billable) se conserva —
        el descubrimiento dirigido no lo re-paga cada pasada."""
        save_participants(10, {34: "Real Betis", 35: "Sevilla FC"}, base=tmp_path)
        assert load_participants(10, base=tmp_path) == {
            34: "Real Betis",
            35: "Sevilla FC",
        }
        assert load_participants(12, base=tmp_path) is None

    def test_participant_fixtures_roundtrip(self, tmp_path):
        save_participant_fixtures(10, 35, [_FIXTURE], base=tmp_path)
        loaded = load_participant_fixtures(10, 35, base=tmp_path)
        assert loaded is not None
        assert loaded[0].fixture_id == "id1200390574632744"
        assert loaded[0].home_name == "Sevilla FC"
        assert load_participant_fixtures(10, 99, base=tmp_path) is None


class TestDirectMode:
    """Modo directo (api.oddspapi.io): /v4 + apiKey por query,
    cooldown por endpoint y historical-odds fuera de la cuota."""

    def _client_cls(self):
        from app.services.odds.oddspapi import OddsPapiClient

        return OddsPapiClient

    def _http(self, seen: list, payload=None):
        import httpx

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=payload if payload is not None else [])

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_url_v4_y_apikey_en_query(self):
        OddsPapiClient = self._client_cls()
        client = OddsPapiClient(api_key="KEY123", direct=True)
        seen: list = []
        async with self._http(seen, payload={"ok": True}) as http:
            status, _ = await client._get(http, "/historical-odds")
        assert status == 200
        request = seen[0]
        assert str(request.url).startswith("https://api.oddspapi.io/v4/historical-odds")
        assert request.url.params["apiKey"] == "KEY123"
        assert "x-rapidapi-key" not in request.headers

    async def test_rapidapi_sigue_con_headers(self):
        OddsPapiClient = self._client_cls()
        client = OddsPapiClient(api_key="K", api_host="h.test")
        seen: list = []
        async with self._http(seen) as http:
            await client._get(http, "/fixtures")
        request = seen[0]
        assert str(request.url).startswith("https://h.test/fixtures")
        assert request.headers["x-rapidapi-key"] == "K"
        assert "apiKey" not in request.url.params

    async def test_historical_odds_no_cuenta_cuota_en_directo(self):
        OddsPapiClient = self._client_cls()
        client = OddsPapiClient(api_key="K", direct=True)
        async with self._http([]) as http:
            await client._get(http, "/historical-odds")
            await client._get(http, "/fixtures")
            await client._get(http, "/markets")
        # Solo fixtures y markets son billables en directo.
        assert client.calls == 2

    async def test_rapidapi_cuenta_todo(self):
        OddsPapiClient = self._client_cls()
        client = OddsPapiClient(api_key="K", api_host="h")
        async with self._http([]) as http:
            await client._get(http, "/historical-odds")
            await client._get(http, "/fixtures")
        assert client.calls == 2

    async def test_cooldown_espera_entre_llamadas(self, monkeypatch):
        import app.services.odds.oddspapi as mod

        OddsPapiClient = self._client_cls()
        client = OddsPapiClient(api_key="K", direct=True)
        sleeps: list[float] = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
        async with self._http([]) as http:
            await client._get(http, "/historical-odds")
            await client._get(http, "/historical-odds")
        # La segunda llamada espera los 5 s completos del cooldown
        # (el reloj mockeado no avanza durante el test).
        assert sleeps and max(sleeps) > 0

    async def test_billable_count_persistente(self):
        """Las billables del canal directo se acumulan en
        provider_state entre procesos — el histórico gratis no suma."""
        import app.services.odds.oddspapi as mod

        OddsPapiClient = self._client_cls()
        client = OddsPapiClient(api_key="K", direct=True)
        assert mod.direct_billable_count() == 0
        async with self._http([]) as http:
            await client._get(http, "/fixtures")
            await client._get(http, "/historical-odds")
        assert mod.direct_billable_count() == 1

    async def test_tope_mensual_frena_billables_no_gratis(self):
        """Al llegar al tope mensual, las billables se cortan ANTES de
        emitir HTTP — pero el histórico gratis sigue pasando. Es el
        seguro para que la cuota nunca llegue a 250/250 (donde el
        histórico también muere)."""
        import app.services.odds.oddspapi as mod
        from app.services.results.base import _load_state, _save_state

        OddsPapiClient = self._client_cls()
        client = OddsPapiClient(api_key="K", direct=True)
        state = _load_state()
        state.setdefault("billable_month", {})["oddspapi"] = {
            "period": mod._billable_period(),
            "count": mod._DIRECT_MONTHLY_CAP,
        }
        _save_state()
        seen: list = []
        async with self._http(seen) as http:
            status, _ = await client._get(http, "/fixtures")
            status2, _ = await client._get(http, "/historical-odds")
        assert status == 0  # freno: ni siquiera emitió la request
        assert status2 == 200
        assert len(seen) == 1  # solo el histórico hizo HTTP


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


# --- Props de jugador --------------------------------------------------------

_PROP_MARKETS = {
    10730: MarketDef(
        market_name="Anytime Goal Scorer",
        market_type="players-anytimegoalscorer",
        period="fulltime",
        handicap=0.0,
        outcomes={10730: "Yes"},
    ),
    102598: MarketDef(
        market_name="Over Under Player Assists (incl. overtime)",
        market_type="playertotals-assists",
        period="result",
        handicap=0.5,
        outcomes={102598: "Over", 102599: "Under"},
    ),
    102700: MarketDef(
        market_name="Player Fouls Committed (incl. overtime)",
        market_type="players-foulscommitted",
        period="result",
        handicap=0.0,
        outcomes={102700: "0", 102701: "1+", 102702: "2+"},
    ),
    102732: MarketDef(
        market_name="Player To Be Carded (incl. overtime)",
        market_type="players-cards",
        period="result",
        handicap=0.0,
        outcomes={102732: "Yes"},
    ),
}

# Ipswich Town - Liverpool FC: playerIds reales del payload historico
# y del diccionario /v4/players (ya normalizados "Nombre Apellido").
_PLAYERS = {
    "1058437": "Cody Gakpo",
    "1245194": "Alexis Mac Allister",
    "9999999": "Alexander Isak",
}

_PROP_HISTORY = {
    "bookmakers": {
        "bet365.es": {
            "markets": {
                "10730": {
                    "outcomes": {
                        "10730": {
                            "players": {
                                "1058437": [_point("2026-09-04T18:53:00.000Z", 4.33)],
                                "1245194": [_point("2026-09-04T18:53:00.000Z", 6.0)],
                                "9999999": [_point("2026-09-04T18:53:00.000Z", 3.4)],
                            }
                        }
                    }
                },
                "102598": {
                    "outcomes": {
                        "102598": {
                            "players": {
                                "1058437": [_point("2026-09-04T18:53:00.000Z", 2.5)],
                            }
                        }
                    }
                },
                "102700": {
                    "outcomes": {
                        "102701": {
                            "players": {
                                "1245194": [_point("2026-09-04T18:53:00.000Z", 1.8)],
                            }
                        },
                        "102702": {
                            "players": {
                                "1245194": [_point("2026-09-04T18:53:00.000Z", 4.0)],
                            }
                        },
                    }
                },
                "102732": {
                    "outcomes": {
                        "102732": {
                            "players": {
                                "1245194": [_point("2026-09-04T18:53:00.000Z", 3.75)],
                            }
                        }
                    }
                },
            }
        }
    }
}


def _prop_choices(players=_PLAYERS):
    return translate_history(
        _PROP_HISTORY, _PROP_MARKETS, "Ipswich Town", "Liverpool FC", players
    )


class TestPropTranslation:
    def test_scorer_anytime_jugadores(self):
        choices = _prop_choices()
        scorers = [c for c in choices if c.market_name == "Goalscorer anytime"]
        assert {c.choice_name for c in scorers} == {
            "Cody Gakpo",
            "Alexis Mac Allister",
            "Alexander Isak",
        }
        gakpo = next(c for c in scorers if c.choice_name == "Cody Gakpo")
        assert gakpo.choice_group is None
        assert gakpo.points[0].cuota == 4.33

    def test_prop_linea_ou_lleva_grupo(self):
        choices = _prop_choices()
        assists = [c for c in choices if c.market_name == "Player assists"]
        assert len(assists) == 1
        assert assists[0].choice_name == "Cody Gakpo"
        assert assists[0].choice_group == "Over 0.5"

    def test_prop_conteo_normaliza_linea_entera(self):
        """'1+' == Over 0.5 y '2+' == Over 1.5: mismo contrato."""
        choices = _prop_choices()
        fouls = [c for c in choices if c.market_name == "Player fouls"]
        groups = {c.choice_group for c in fouls}
        assert groups == {"Over 0.5", "Over 1.5"}
        one = next(c for c in fouls if c.choice_group == "Over 0.5")
        assert one.points[0].cuota == 1.8

    def test_prop_cardeado(self):
        choices = _prop_choices()
        cards = [c for c in choices if c.market_name == "Player to be booked"]
        assert len(cards) == 1
        assert cards[0].choice_name == "Alexis Mac Allister"
        assert cards[0].choice_group is None

    def test_sin_diccionario_no_emite_props(self):
        assert _prop_choices(players=None) == []
        assert _prop_choices(players={}) == []

    def test_playerid_desconocido_salta(self):
        choices = _prop_choices(players={"1058437": "Cody Gakpo"})
        names = {c.choice_name for c in choices}
        assert names == {"Cody Gakpo"}

    def test_la_clave_0_no_es_jugador(self):
        # Un outcome con precio de equipo dentro de un mercado prop no
        # genera fila: "0" es la resolucion de equipo, no un jugador.
        hist = {
            "bookmakers": {
                "bet365.es": {
                    "markets": {
                        "10730": {
                            "outcomes": {
                                "10730": {
                                    "players": {
                                        "0": [_point("2026-09-04T18:00:00.000Z", 9.0)]
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        assert translate_history(hist, _PROP_MARKETS, "A", "B", _PLAYERS) == []


class TestPropIntegracionCompare:
    """Filas prop -> map_pick_choices contra picks reales del backlog."""

    def _virtual(self, choices):
        return [
            OddsSnapshot(
                provider="oddspapi",
                event_ext_id="sofascore:16363261",
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

    def _event(self):
        return OddsEvent(
            event_ext_id="sofascore:16363261",
            sport="futbol",
            home_team="Ipswich Town",
            away_team="Liverpool FC",
            start=datetime(2026, 9, 4, 18, 30),
        )

    def test_pick_marca_gol_nombre_al_final(self):
        """'Anota gol en cualquier momento Alexander Isak': el nombre
        va al final — el patron de cierre lo rescata."""
        rows = self._virtual(_prop_choices())
        pick = ParsedPick(
            mercado="ganador",
            seleccion="Anota gol en cualquier momento Alexander Isak",
            deporte="fútbol",
            es_apuesta=True,
        )
        m = map_pick_choices(pick, self._event(), rows)
        assert m is not None
        assert m.market_name == "Goalscorer anytime"
        assert m.choice_name == "Alexander Isak"
        assert m.rows[-1].cuota == 3.4

    def test_pick_tarjeta_jugador(self):
        rows = self._virtual(_prop_choices())
        pick = ParsedPick(
            mercado="ganador",
            seleccion="Recibe amonestación Alexis Mac Allister",
            deporte="fútbol",
            es_apuesta=True,
        )
        m = map_pick_choices(pick, self._event(), rows)
        assert m is not None
        assert m.market_name == "Player to be booked"
        assert m.rows[-1].cuota == 3.75

    def test_pick_prop_linea_minima_en_empate(self):
        """'comete falta' sin linea -> Over 0.5 (al menos una), nunca
        Over 1.5: el desempate por linea menor da el contrato real."""
        rows = self._virtual(_prop_choices())
        pick = ParsedPick(
            mercado="jugador",
            seleccion="Comete falta Alexis Mac Allister",
            deporte="fútbol",
            es_apuesta=True,
        )
        m = map_pick_choices(pick, self._event(), rows)
        assert m is not None
        assert m.market_name == "Player fouls"
        assert m.choice_group == "Over 0.5"
        assert m.rows[-1].cuota == 1.8

    def test_pick_recibira_tarjeta_con_dos_puntos(self):
        """'Alexis Mac Allister: Recibirá una tarjeta' — nombre
        delante con ':', verbo 'recibirá' (forma real del backlog)."""
        rows = self._virtual(_prop_choices())
        pick = ParsedPick(
            mercado="jugador",
            seleccion="Alexis Mac Allister: Recibirá una tarjeta",
            deporte="fútbol",
            es_apuesta=True,
        )
        m = map_pick_choices(pick, self._event(), rows)
        assert m is not None
        assert m.market_name == "Player to be booked"
        assert m.choice_name == "Alexis Mac Allister"

    def test_pick_marca_o_asiste_sin_mercado_es_miss_honesto(self):
        """'marca o asiste' no tiene mercado en OddsPapi: None, no
        una caida silenciosa al anytime scorer."""
        rows = self._virtual(_prop_choices())
        pick = ParsedPick(
            mercado="jugador",
            seleccion="Marca o asiste Cody Gakpo",
            deporte="fútbol",
            es_apuesta=True,
        )
        assert map_pick_choices(pick, self._event(), rows) is None
