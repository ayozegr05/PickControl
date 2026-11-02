"""Tests del provider BetsAPI (api.b365api.com — feed bet365 real)."""

from datetime import datetime

from app.services.odds import betsapi


def _fixture(**kw):
    base = dict(
        fixture_id="111",
        home_name="PSG",
        away_name="Barcelona",
        start=datetime(2026, 10, 20, 19, 0),
        sport_id=1,
        league_name="UEFA Champions League",
    )
    base.update(kw)
    return betsapi.BetsFixture(**base)


class TestParseEvent:
    def test_parse_event_ok(self):
        raw = {
            "id": "202645739",
            "time": "1792530000",
            "league": {"name": "UEFA Champions League"},
            "home": {"name": "PSG"},
            "away": {"name": "Barcelona"},
        }
        f = betsapi._parse_event(raw, 1)
        assert f is not None
        assert f.fixture_id == "202645739"
        assert f.home_name == "PSG"
        assert f.start is not None

    def test_parse_event_skips_esoccer(self):
        raw = {
            "id": "1",
            "time": "1792530000",
            "league": {"name": "Esoccer Battle Volta - 6 mins play"},
            "home": {"name": "Man City (Andrew)"},
            "away": {"name": "Real Madrid (Senya)"},
        }
        assert betsapi._parse_event(raw, 1) is None

    def test_parse_event_skips_non_1v1(self):
        raw = {
            "id": "2",
            "time": "1792530000",
            "league": {"name": "Lone Star"},
            "home": {"name": "Hipo Chile"},
            "away": None,
        }
        assert betsapi._parse_event(raw, 2) is None


class TestMatchFixture:
    def test_strict_window(self):
        f = _fixture()
        assert (
            betsapi.match_fixture(
                [f], "PSG vs Barcelona", datetime(2026, 10, 20, 20, 0)
            )
            is f
        )

    def test_wide_window_rescues_repost_date(self):
        f = _fixture()
        found = betsapi.match_fixture(
            [f], "PSG vs Barcelona", datetime(2026, 10, 17, 12, 0)
        )
        assert found is f

    def test_wide_window_rejects_weak_names(self):
        f = _fixture()
        assert (
            betsapi.match_fixture(
                [f], "Alcorcon vs Trival", datetime(2026, 10, 17, 12, 0)
            )
            is None
        )

    def test_no_fixture(self):
        assert (
            betsapi.match_fixture([], "PSG vs Barcelona", datetime(2026, 10, 20, 19, 0))
            is None
        )

    def test_alias_expanded_both_sides(self):
        """Caso real: el hint del pick ya viene expandido
        ('paris saint germain') y bet365 nombra la sigla ('PSG') — sin
        expandir también el fixture el score queda bajo umbral."""
        f = _fixture()
        found = betsapi.match_fixture(
            [f], "paris saint germain vs barcelona", datetime(2026, 10, 20, 20, 0)
        )
        assert found is f

    def test_country_names_translated(self):
        """El hint viaja en español y el feed en inglés."""
        f = _fixture(home_name="Netherlands", away_name="Poland")
        found = betsapi.match_fixture(
            [f], "paises bajos vs polonia", datetime(2026, 10, 20, 20, 0)
        )
        assert found is f


def _football_payload():
    """Estructura real de /v4/bet365/prematch (PSG-Barça, reducida)."""
    return {
        "success": 1,
        "results": [
            {
                "FI": "202645739",
                "sport_id": "1",
                "result": {
                    "sp": {
                        "full_time_result": {
                            "name": "Full Time Result",
                            "odds": [
                                {"odds": "2.40", "name": "1"},
                                {"odds": "4.50", "name": "Draw"},
                                {"odds": "2.40", "name": "2"},
                            ],
                        },
                        "draw_no_bet": {
                            "name": "Draw No Bet",
                            "odds": [
                                {"odds": "1.83", "name": "1"},
                                {"odds": "1.83", "name": "2"},
                            ],
                        },
                    }
                },
                "goals": {
                    "sp": {
                        "goals_over_under": {
                            "name": "Goals Over/Under",
                            "odds": [
                                {"odds": "1.20", "name": "2.5", "header": "Over"},
                                {"odds": "4.50", "name": "2.5", "header": "Under"},
                            ],
                        },
                        "alternative_total_goals": {
                            "name": "Alternative Total Goals",
                            "odds": [
                                {"odds": "1.05", "name": "1.5", "header": "Over"},
                                {"odds": "1.53", "name": "3.5", "header": "Over"},
                                {"odds": "9.00", "name": "1.5", "header": "Under"},
                            ],
                        },
                        "team_total_goals": {
                            "name": "Team Total Goals",
                            "odds": [
                                {"odds": "1.72", "header": "1", "handicap": "Over 1.5"},
                                {
                                    "odds": "2.00",
                                    "header": "1",
                                    "handicap": "Under 1.5",
                                },
                                {"odds": "1.90", "header": "2", "handicap": "Over 1.5"},
                            ],
                        },
                        "both_teams_to_score": {
                            "name": "Both Teams to Score",
                            "odds": [
                                {"odds": "1.22", "name": "Yes"},
                                {"odds": "4.0", "name": "No"},
                            ],
                        },
                    }
                },
                "corners": {
                    "sp": {
                        "corners_2_way": {
                            "name": "Corners 2-Way",
                            "odds": [
                                {"odds": "1.83", "name": "9.5", "header": "Over"},
                                {"odds": "1.83", "name": "9.5", "header": "Under"},
                            ],
                        },
                        # Mercado 760: el 3-way de línea entera del
                        # tipster (Más de | Exactamente | Menos de).
                        "corners": {
                            "name": "Corners",
                            "odds": [
                                {"odds": "1.91", "name": "8", "header": "Over"},
                                {"odds": "7.50", "name": "8", "header": "Exactly"},
                                {"odds": "2.38", "name": "8", "header": "Under"},
                            ],
                        },
                        "alternative_corners": {
                            "name": "Alternative Corners",
                            "odds": [
                                {"odds": "1.14", "name": "6", "header": "Over"},
                                {"odds": "9.00", "name": "7", "header": "Exactly"},
                                {"odds": "10.0", "name": "6", "header": "Under"},
                            ],
                        },
                        "team_corners": {
                            "name": "Team Corners",
                            "odds": [
                                {"odds": "1.72", "header": "1", "handicap": "Over 4.5"},
                                {"odds": "1.90", "header": "2", "handicap": "Over 4.5"},
                            ],
                        },
                        # Mercado por bandas ("6 - 8"): no comparable.
                        "total_corners": {
                            "name": "Total Corners",
                            "odds": [
                                {"odds": "5.50", "name": "Under 6"},
                                {"odds": "2.50", "name": "6 - 8"},
                            ],
                        },
                    }
                },
                "cards_fouls": {
                    "sp": {
                        # 2-way sin Exactly -> "Cards in match"
                        "number_of_cards_in_match": {
                            "name": "Number of Cards In Match",
                            "odds": [
                                {"odds": "1.80", "name": "3.5", "header": "Over"},
                                {"odds": "1.90", "name": "3.5", "header": "Under"},
                            ],
                        },
                        # 3-way con Exactly -> "Cards 3-Way"
                        "cards": {
                            "name": "Cards",
                            "odds": [
                                {"odds": "2.20", "name": "4", "header": "Over"},
                                {"odds": "6.00", "name": "4", "header": "Exactly"},
                                {"odds": "2.50", "name": "4", "header": "Under"},
                            ],
                        },
                    }
                },
                "asian_lines": {
                    "sp": {
                        "asian_handicap": {
                            "name": "Asian Handicap",
                            "odds": [
                                {"odds": "1.85", "header": "1", "handicap": "0.0"},
                                {"odds": "2.00", "header": "2", "handicap": "0.0"},
                            ],
                        }
                    }
                },
                "scorers": {
                    "sp": {
                        "goalscorers": {
                            "name": "Goalscorers",
                            "odds": [
                                {"odds": "2.50", "name": "Lamine Yamal"},
                                {"odds": "3.10", "name": "Ousmane Dembele"},
                            ],
                        }
                    }
                },
            }
        ],
    }


class TestTranslatePrematch:
    def test_football_markets(self):
        choices = betsapi.translate_prematch(
            _football_payload(), "futbol", "PSG", "Barcelona"
        )
        by_market = {}
        for c in choices:
            by_market.setdefault(c.market_name, []).append(
                (c.choice_name, c.choice_group)
            )

        assert ("1", None) in by_market["Full time"]
        assert ("X", None) in by_market["Full time"]  # Draw -> X
        assert ("1", None) in by_market["Draw no bet"]
        assert ("Over", "2.5") in by_market["Match goals"]
        # alternas se fusionan en el mismo mercado
        assert ("Over", "3.5") in by_market["Match goals"]
        assert ("Over", "9.5") in by_market["Corners 2-Way"]
        # EL mercado del tipster: líneas enteras con Exactly
        assert ("Over", "8") in by_market["Corners 3-Way"]
        assert ("Exactly", "8") in by_market["Corners 3-Way"]
        assert ("Under", "8") in by_market["Corners 3-Way"]
        # la escalera alterna también es 3-way (lleva Exactly)
        assert ("Over", "6") in by_market["Corners 3-Way"]
        assert ("Exactly", "7") in by_market["Corners 3-Way"]
        # nada entero contamina el 2-way
        assert all(g in (None, "9.5") for _, g in by_market["Corners 2-Way"])
        assert ("Over", "4.5") in by_market["Team 1 corners"]
        assert ("Over", "4.5") in by_market["Team 2 corners"]
        assert ("Over", "1.5") in by_market["Team 1 goals"]
        # cards 2-way -> "Cards in match"; 3-way -> "Cards 3-Way"
        assert ("Over", "3.5") in by_market["Cards in match"]
        assert ("Over", "4") in by_market["Cards 3-Way"]
        assert ("Exactly", "4") in by_market["Cards 3-Way"]
        # mercado por bandas: no comparable -> no se traduce
        assert "Total Corners" not in by_market
        assert ("Yes", None) in by_market["Both teams to score"]
        assert any(c.startswith("(0) PSG") for c, _ in by_market["Asian handicap"])
        assert "Lamine Yamal" in [c for c, _ in by_market["Goalscorer anytime"]]

    def test_empty_payload(self):
        assert betsapi.translate_prematch({"results": []}, "futbol", "A", "B") == []

    def test_point_is_single_closing(self):
        choices = betsapi.translate_prematch(
            _football_payload(), "futbol", "PSG", "Barcelona"
        )
        assert all(len(c.points) == 1 for c in choices)


def _tennis_payload():
    return {
        "success": 1,
        "results": [
            {
                "FI": "203185172",
                "sport_id": "13",
                "main": {
                    "sp": {
                        "to_win_match": {
                            "name": "To Win Match",
                            "odds": [
                                {"odds": "1.57", "name": "1"},
                                {"odds": "2.37", "name": "2"},
                            ],
                        },
                        "total_games_2_way": {
                            "name": "Total Games 2-Way",
                            "odds": [
                                {"odds": "1.83", "name": "22.5", "header": "Over"},
                                {"odds": "1.83", "name": "22.5", "header": "Under"},
                            ],
                        },
                        "match_handicap_(games)": {
                            "name": "Match Handicap (Games)",
                            "odds": [
                                {"odds": "1.83", "name": "-2.5", "header": "1"},
                                {"odds": "1.83", "name": "+2.5", "header": "2"},
                            ],
                        },
                        "1st_set_winner": {
                            "name": "1st Set Winner",
                            "odds": [
                                {"odds": "1.61", "name": "1"},
                                {"odds": "2.30", "name": "2"},
                            ],
                        },
                        "1st_set_total_games": {
                            "name": "1st Set Total Games",
                            "odds": [
                                {"odds": "1.07", "name": "7.5", "header": "Over"},
                                {"odds": "9.00", "name": "7.5", "header": "Under"},
                                {"odds": "1.25", "name": "8.5", "header": "Over"},
                            ],
                        },
                    }
                },
                "set": {
                    "sp": {
                        "first_set_handicap": {
                            "name": "First Set Handicap",
                            "odds": [
                                {"odds": "1.83", "header": "1", "handicap": "-1.5"},
                                {"odds": "1.83", "header": "2", "handicap": "+1.5"},
                            ],
                        }
                    }
                },
            }
        ],
    }


class TestTranslateTennis:
    def test_first_set_games(self):
        choices = betsapi.translate_prematch(
            _tennis_payload(), "tenis", "Luca Van Assche", "Clement Tabur"
        )
        by_market = {}
        for c in choices:
            by_market.setdefault(c.market_name, []).append(
                (c.choice_name, c.choice_group)
            )

        assert ("1", None) in by_market["Full time"]
        assert ("Over", "22.5") in by_market["Total games won"]
        # LA estrella: juegos del 1er set
        assert ("Over", "7.5") in by_market["1st set games"]
        assert ("Under", "7.5") in by_market["1st set games"]
        assert ("Over", "8.5") in by_market["1st set games"]
        assert ("1", None) in by_market["First set winner"]
        assert any("Luca Van Assche" in c for c, _ in by_market["Game handicap"])
        assert any("(-2.5)" in c for c, _ in by_market["Game handicap"])
        # El signo '+' del feed se normaliza a la convención sin '+'
        assert any("(2.5)" in c for c, _ in by_market["Game handicap"])
        assert any("Set handicap" == m for m in by_market)


def _real_formats_payload():
    """Formatos observados en el payload real /v3 (Sevilla-Osasuna):
    sección `others` como LISTA, props con enlazado PC<id>->name,
    umbrales 'N+' en shots, DC con nombres 'X or Draw'."""
    return {
        "success": 1,
        "results": [
            {
                "FI": "201587957",
                "sport_id": "1",
                "main": {
                    "sp": {
                        "score_or_assist": {
                            "name": "Score or Assist",
                            "odds": [
                                # En `main` el nombre va en la fila PC
                                # enlazada por el id numérico.
                                {
                                    "id": "PC111",
                                    "odds": "",
                                    "name": "Robbie Ure",
                                    "header": "Player/Last 5",
                                },
                                {"id": "111", "odds": "2.38", "header": "Score"},
                            ],
                        },
                        "shots_on_target": {
                            "name": "Shots On Target",
                            "odds": [
                                {
                                    "id": "9",
                                    "odds": "1.29",
                                    "name": "Robbie Ure",
                                    "header": "1+",
                                    "team": "Player/Last 5",
                                },
                                {
                                    "id": "10",
                                    "odds": "3.40",
                                    "name": "Robbie Ure",
                                    "header": "2+",
                                    "team": "Player/Last 5",
                                },
                            ],
                        },
                        "correct_score": {
                            "name": "Correct Score",
                            "odds": [
                                {
                                    "id": "20",
                                    "odds": "6.50",
                                    "name": "1-0",
                                    "header": "1",
                                },
                                {
                                    "id": "21",
                                    "odds": "8.50",
                                    "name": "0-1",
                                    "header": "2",
                                },
                            ],
                        },
                    }
                },
                "scorers": {
                    "sp": {
                        "score_or_assist": {
                            "name": "Score or Assist",
                            "odds": [
                                # En `scorers` el nombre viaja en la
                                # fila de precio; existe la opción
                                # combinada "Score or Assist".
                                {
                                    "id": "30",
                                    "odds": "2.38",
                                    "header": "Score",
                                    "name": "Robbie Ure",
                                },
                                {
                                    "id": "31",
                                    "odds": "7.00",
                                    "header": "Assist",
                                    "name": "Robbie Ure",
                                },
                                {
                                    "id": "32",
                                    "odds": "1.95",
                                    "header": "Score or Assist",
                                    "name": "Robbie Ure",
                                },
                            ],
                        },
                        "team_goalscorer": {
                            "name": "Team Goalscorer",
                            "odds": [
                                {
                                    "id": "40",
                                    "odds": "3.25",
                                    "header": "First",
                                    "name": "Isaac Romero",
                                    "team": "1",
                                }
                            ],
                        },
                        "multi_scorers": {
                            "name": "Multi Scorers",
                            "odds": [
                                {
                                    "id": "50",
                                    "odds": "9.50",
                                    "header": "2 or More",
                                    "name": "Robbie Ure",
                                }
                            ],
                        },
                    }
                },
                # `others` es una LISTA de secciones {sp: ...}
                "others": [
                    {
                        "sp": {
                            "double_chance": {
                                "name": "Double Chance",
                                "odds": [
                                    {"odds": "1.22", "name": "Sevilla or Draw"},
                                    {"odds": "1.80", "name": "Draw or Osasuna"},
                                    {"odds": "1.33", "name": "Sevilla or Osasuna"},
                                ],
                            }
                        }
                    },
                    {
                        "sp": {
                            "first_team_to_score": {
                                "name": "First Team to Score",
                                "odds": [
                                    {"odds": "1.67", "name": "1"},
                                    {"odds": "8.50", "name": "No Goals"},
                                    {"odds": "2.50", "name": "2"},
                                ],
                            },
                            "team_total_goals": {
                                "name": "Team Total Goals",
                                "odds": [
                                    {
                                        "odds": "1.22",
                                        "header": "1",
                                        "handicap": "Over 0.5",
                                    }
                                ],
                            },
                        }
                    },
                ],
            }
        ],
    }


class TestTranslateRealFormats:
    def test_others_list_and_named_markets(self):
        choices = betsapi.translate_prematch(
            _real_formats_payload(), "futbol", "Sevilla", "Osasuna"
        )
        by_market = {}
        for c in choices:
            by_market.setdefault(c.market_name, []).append(
                (c.choice_name, c.choice_group)
            )

        # DC con nombres: el equipo no-Draw decide 1X/X2; sin Draw -> 12
        assert ("1X", None) in by_market["Double chance"]
        assert ("X2", None) in by_market["Double chance"]
        assert ("12", None) in by_market["Double chance"]
        # 'No Goals' -> 'X'
        assert ("X", None) in by_market["First team to score"]
        # mercados dentro de la sección-lista `others`
        assert ("Over", "0.5") in by_market["Team 1 goals"]
        assert ("1-0", None) in by_market["Correct score"]

    def test_props(self):
        choices = betsapi.translate_prematch(
            _real_formats_payload(), "futbol", "Sevilla", "Osasuna"
        )
        by_market = {}
        for c in choices:
            by_market.setdefault(c.market_name, []).append(
                (c.choice_name, c.choice_group)
            )

        # score_or_assist: la fila PC enlaza el jugador por id
        assert ("Robbie Ure", None) in by_market["Goalscorer anytime"]
        assert ("Robbie Ure", None) in by_market["Player assists"]
        assert ("Robbie Ure", None) in by_market["Goalscorer or assist"]
        # 'N+' -> 'Over N-0.5'
        assert ("Robbie Ure", "Over 0.5") in by_market["Player shots on target"]
        assert ("Robbie Ure", "Over 1.5") in by_market["Player shots on target"]
        # 'First' de equipo no se cuela en 'First goalscorer' del partido
        assert ("Isaac Romero", "First") in by_market["Team goalscorer"]
        assert "First goalscorer" not in by_market
        assert ("Robbie Ure", "2 or More") in by_market["Multi scorers"]


class TestTransports:
    def test_direct_paths(self):
        client = betsapi.BetsApiClient(token="t")
        assert client._prematch_path == "/v4/bet365/prematch"
        assert client._upcoming_path == "/v1/bet365/upcoming"
        assert client._headers == {}

    def test_rapidapi_paths(self):
        client = betsapi.BetsApiClient(rapidapi_key="k")
        assert client._prematch_path == "/v3/bet365/prematch"
        assert client._upcoming_path == "/v3/bet365/upcoming"
        assert client._headers["X-RapidAPI-Key"] == "k"
        assert client._headers["X-RapidAPI-Host"] == "betsapi2.p.rapidapi.com"
        assert client._max_pages == betsapi._FREE_MAX_PAGES


class TestDayBoardResume:
    """Caché progresiva del board: un día cuesta como máximo
    `_max_pages` llamadas entre TODOS sus picks, y los aciertos
    sobre páginas ya leídas son gratis."""

    def _client_with_pages(self, pages, total):
        client = betsapi.BetsApiClient(rapidapi_key="k")
        calls = {"n": 0}

        async def fake_get(client_http, path, params=None):
            calls["n"] += 1
            page = (params or {}).get("page", 1)
            return 200, {
                "pager": {"total": total, "page": page},
                "results": pages.get(page, []),
            }

        client._get = fake_get  # type: ignore[assignment]
        return client, calls

    def _event(self, fid, home, away, ts="1792530000"):
        return {
            "id": fid,
            "time": ts,
            "league": {"name": "LaLiga"},
            "home": {"name": home},
            "away": {"name": away},
        }

    def test_resume_shares_pages_between_picks(self):
        import asyncio
        from datetime import date

        pages = {
            1: [self._event("1", "PSG", "Barcelona")],
            2: [self._event("2", "Real Madrid", "Atletico Madrid")],
            3: [self._event("3", "Sevilla", "Betis")],
        }
        client, calls = self._client_with_pages(pages, total=150)
        day, when = date(2026, 10, 20), datetime(2026, 10, 20, 19, 0)
        http = object()

        # Pick A (página 1): 1 llamada, parada temprana.
        r = asyncio.run(client.upcoming(http, 1, day, hint="PSG", when=when))
        assert calls["n"] == 1 and r is not None

        # Pick B (página 2): reanuda, NO re-lee la página 1.
        r = asyncio.run(client.upcoming(http, 1, day, hint="Real Madrid", when=when))
        assert calls["n"] == 2

        # Miss (página 3 = fin): board completo.
        r = asyncio.run(client.upcoming(http, 1, day, hint="Inexistente FC", when=when))
        assert calls["n"] == 3 and len(r) == 3

        # Otro miss el mismo día: GRATIS (board completo).
        asyncio.run(client.upcoming(http, 1, day, hint="Otro FC", when=when))
        assert calls["n"] == 3

        # Re-buscar a A que ya estaba en páginas leídas: GRATIS.
        asyncio.run(client.upcoming(http, 1, day, hint="PSG", when=when))
        assert calls["n"] == 3
