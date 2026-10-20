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
                    }
                },
                "cards_fouls": {
                    "sp": {
                        "number_of_cards_in_match": {
                            "name": "Number of Cards In Match",
                            "odds": [
                                {"odds": "1.80", "name": "3.5", "header": "Over"},
                                {"odds": "1.90", "name": "3.5", "header": "Under"},
                            ],
                        }
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
        # línea entera de alternas, Exactly se descarta
        assert ("Over", "6") in by_market["Corners 2-Way"]
        assert all(g != "7" for _, g in by_market["Corners 2-Way"])
        assert ("Over", "4.5") in by_market["Team 1 corners"]
        assert ("Over", "4.5") in by_market["Team 2 corners"]
        assert ("Over", "1.5") in by_market["Team 1 goals"]
        assert ("Over", "3.5") in by_market["Cards in match"]
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
