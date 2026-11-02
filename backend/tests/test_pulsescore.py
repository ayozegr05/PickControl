"""Tests del provider PulseScore (espejo normalizado del feed bet365).

Los fixtures replican el formato REAL observado en
`GET /api/v3/bet365/soccer/events` (evento Brisbane Roar vs Perth
Glory, FI 201637412): selections con `canonicalOutcome`, `rawName`,
`odds`, `line` y `moreInfo` (CO_LINE/HA/HD).
"""

from __future__ import annotations

from datetime import datetime

from app.services.odds import pulsescore
from app.services.odds.pulsescore import PulseScoreOddsProvider, translate_event


def _sel(outcome, raw, odds, line=None, info=None):
    sel = {
        "canonicalOutcome": outcome,
        "rawName": raw,
        "odds": odds,
        "isActive": True,
    }
    if line is not None:
        sel["line"] = line
    if info:
        sel["moreInfo"] = info
    return sel


def _market(canon, raw, selections, period="FULL_TIME"):
    return {
        "canonicalMarket": canon,
        "rawName": raw,
        "period": period,
        "isActive": True,
        "selections": selections,
    }


# Réplica recortada del evento real: corners 3-way + escalera cards.
EVENT = {
    "eventId": "201637412",
    "sport": "soccer",
    "league": "Australia||A-League",
    "home": "Brisbane Roar",
    "away": "Perth Glory",
    "live": False,
    "startTime": "2026-10-24T08:00:00.000Z",
    "markets": [
        _market(
            "MATCH_RESULT",
            "Full Time Result",
            [
                _sel("HOME", "Brisbane Roar", 2.1),
                _sel("DRAW", "Draw", 3.4),
                _sel("AWAY", "Perth Glory", 3.2),
            ],
        ),
        _market(
            "OVER_UNDER",
            "Goals Over/Under",
            [
                _sel("OVER", "Over", 1.9, 2.5, {"CO_LINE": "2.5"}),
                _sel("UNDER", "Under", 1.9, 2.5, {"CO_LINE": "2.5"}),
            ],
        ),
        # El mercado principal: Over/Exactly/Under sobre línea entera.
        _market(
            "CORNERS_OVER_UNDER",
            "Corners",
            [
                _sel("OVER", "Over", 2.0, 9.5, {"CO_LINE": "9"}),
                _sel("OTHER", "Exactly", 7.5, 9, {"CO_LINE": "9"}),
                _sel("UNDER", "Under", 2.25, 8.5, {"CO_LINE": "9"}),
            ],
        ),
        _market(
            "CORNERS_OVER_UNDER",
            "Alternative Corners",
            [
                _sel("OVER", "Over", 1.004, 2.5, {"CO_LINE": "2"}),
                _sel("OTHER", "Exactly", 41, 2, {"CO_LINE": "2"}),
                _sel("UNDER", "Under", 15, 1.5, {"CO_LINE": "2"}),
            ],
        ),
        _market(
            "CORNERS_OVER_UNDER",
            "Corners 2-Way",
            [
                _sel("OVER", "Over", 1.9, 9.5, {"CO_LINE": "9.5"}),
                _sel("UNDER", "Under", 1.9, 9.5, {"CO_LINE": "9.5"}),
            ],
        ),
        _market(
            "HOME_CORNERS_OVER_UNDER",
            "Corners — Brisbane Roar",
            [
                _sel("OVER", "Over", 1.8, 4.5, {"CO_LINE": "4.5"}),
                _sel("UNDER", "Under", 2.0, 4.5, {"CO_LINE": "4.5"}),
            ],
        ),
        _market(
            "AWAY_CORNERS_OVER_UNDER",
            "Team Corners — Perth Glory",
            [
                _sel("OVER", "Over", 1.9, 3.5, {"CO_LINE": "3.5"}),
                _sel("UNDER", "Under", 1.9, 3.5, {"CO_LINE": "3.5"}),
            ],
        ),
        # Escalera de tarjetas 3-way (la que el wrapper traía vacía).
        _market(
            "OTHER",
            "Cards",
            [
                _sel("OVER", "Over", 1.9091, 4, {"HA": "4"}),
                _sel("OTHER", "Exactly", 5.0, 4, {"HA": "4"}),
                _sel("UNDER", "Under", 2.625, 4, {"HA": "4"}),
                _sel("OVER", "Over", 2.75, 5, {"HA": "5"}),
            ],
        ),
        _market(
            "OTHER",
            "Number of Cards In Match",
            [
                _sel("OVER", "Over", 1.9091, 4.5, {"CO_LINE": "4.5"}),
                _sel("UNDER", "Under", 1.8, 4.5, {"CO_LINE": "4.5"}),
            ],
        ),
        _market(
            "OTHER",
            "Team Cards",
            [
                _sel(
                    "HOME",
                    "Brisbane Roar",
                    1.5333,
                    1.5,
                    {"HD": "Over 1.5", "HA": "1.5"},
                ),
                _sel(
                    "HOME",
                    "Brisbane Roar",
                    2.375,
                    1.5,
                    {"HD": "Under 1.5", "HA": "1.5"},
                ),
                _sel(
                    "AWAY", "Perth Glory", 1.9091, 2.5, {"HD": "Over 2.5", "HA": "2.5"}
                ),
                _sel("AWAY", "Perth Glory", 1.8, 2.5, {"HD": "Under 2.5", "HA": "2.5"}),
            ],
        ),
        _market(
            "OTHER",
            "Score or Assist",
            [
                _sel("OTHER", "Virgiliu Postolachi", 2.5),
                _sel("OTHER", "Justin Vidic", 2.75),
            ],
        ),
        _market(
            "OTHER",
            "Shots",
            [
                _sel("OTHER", "Justin Vidic", 1.0303, info={"CO_LINE": "1+"}),
                _sel("OTHER", "Tom Lawrence", 1.0833, info={"CO_LINE": "2+"}),
            ],
        ),
        _market(
            "OTHER",
            "Shots On Target",
            [
                _sel("OTHER", "Justin Vidic", 1.5, info={"CO_LINE": "1+"}),
            ],
        ),
        _market(
            "ANYTIME_GOALSCORER",
            "Goalscorers — Anytime",
            [_sel("OTHER", "Virgiliu Postolachi", 2.625)],
        ),
        _market(
            "CORRECT_SCORE",
            "Correct Score",
            [
                _sel("OTHER", "1-0", 9.5),
                _sel("OTHER", "0-1", 12.0),
            ],
        ),
        _market(
            "DOUBLE_CHANCE",
            "Double Chance",
            [
                _sel("HOME_DRAW", "Brisbane Roar or Draw", 1.3636),
                _sel("DRAW_AWAY", "Draw or Perth Glory", 1.6667),
                _sel("HOME_AWAY", "Brisbane Roar or Perth Glory", 1.25),
            ],
        ),
        _market(
            "FIRST_TEAM_TO_SCORE",
            "First Team to Score",
            [
                _sel("HOME", "Brisbane Roar", 1.8),
                _sel("AWAY", "Perth Glory", 2.5),
                _sel("OTHER", "No Goal", 12.0),
            ],
        ),
    ],
}


def _by_market(choices):
    out = {}
    for c in choices:
        out.setdefault(c.market_name, []).append((c.choice_name, c.choice_group))
    return out


def test_corners_3way_keeps_exactly_and_integer_line() -> None:
    by = _by_market(translate_event(EVENT, "futbol", "Brisbane Roar", "Perth Glory"))
    c3 = by["Corners 3-Way"]
    # La línea compartida sale de CO_LINE (entera), no del `line` por
    # opción (9.5/9/8.5) — es la línea que escribe el tipster.
    assert ("Over", "9") in c3
    assert ("Exactly", "9") in c3
    assert ("Under", "9") in c3
    # La escalera alterna también aporta sus líneas enteras.
    assert ("Exactly", "2") in c3
    # El 2-way puro va aparte con medias líneas.
    assert ("Over", "9.5") in by["Corners 2-Way"]
    assert all(g != "9" for _, g in by["Corners 2-Way"])


def test_cards_3way_and_2way() -> None:
    by = _by_market(translate_event(EVENT, "futbol", "Brisbane Roar", "Perth Glory"))
    assert ("Over", "4") in by["Cards 3-Way"]
    assert ("Exactly", "4") in by["Cards 3-Way"]
    assert ("Under", "4") in by["Cards 3-Way"]
    assert ("Over", "5") in by["Cards 3-Way"]
    assert ("Over", "4.5") in by["Cards in match"]
    # Tarjetas por equipo con lado y dirección (HD "Over 1.5").
    assert ("Over", "1.5") in by["Team 1 cards"]
    assert ("Under", "1.5") in by["Team 1 cards"]
    assert ("Over", "2.5") in by["Team 2 cards"]
    # Córners por equipo.
    assert ("Over", "4.5") in by["Team 1 corners"]
    assert ("Over", "3.5") in by["Team 2 corners"]


def test_main_and_player_markets() -> None:
    by = _by_market(translate_event(EVENT, "futbol", "Brisbane Roar", "Perth Glory"))
    assert sorted(c for c, _ in by["Full time"]) == ["1", "2", "X"]
    assert ("Over", "2.5") in by["Match goals"]
    assert sorted(c for c, _ in by["Double chance"]) == ["12", "1X", "X2"]
    assert sorted(c for c, _ in by["First team to score"]) == ["1", "2", "X"]
    assert ("Virgiliu Postolachi", None) in by["Goalscorer anytime"]
    assert ("Justin Vidic", None) in by["Goalscorer or assist"]
    # Props de tiros: "N+" -> grupo "Over N-0.5" (convención betsapi).
    assert ("Justin Vidic", "Over 0.5") in by["Player shots"]
    assert ("Tom Lawrence", "Over 1.5") in by["Player shots"]
    assert ("Justin Vidic", "Over 0.5") in by["Player shots on target"]
    scores = [c for c, _ in by["Correct score"]]
    assert "1-0" in scores and "0-1" in scores


def test_unknown_market_kept_as_evidence() -> None:
    event = {
        "eventId": "1",
        "markets": [
            _market(
                "OTHER",
                "Multicorners",
                [
                    _sel("OVER", "Over", 1.83, 19.5, {"CO_LINE": "19.5"}),
                    _sel("UNDER", "Under", 1.83, 19.5, {"CO_LINE": "19.5"}),
                ],
            )
        ],
    }
    by = _by_market(translate_event(event, "futbol"))
    assert ("Over", "19.5") in by["Multicorners"]


def test_tennis_market_names() -> None:
    event = {
        "eventId": "9",
        "markets": [
            _market(
                "MATCH_RESULT",
                "Match Winner",
                [
                    _sel("HOME", "Sinner", 1.4),
                    _sel("AWAY", "Alcaraz", 3.0),
                ],
            ),
            _market(
                "OVER_UNDER",
                "Total Games",
                [
                    _sel("OVER", "Over", 1.9, 22.5, {"CO_LINE": "22.5"}),
                    _sel("UNDER", "Under", 1.9, 22.5, {"CO_LINE": "22.5"}),
                ],
            ),
        ],
    }
    by = _by_market(translate_event(event, "tenis", "Sinner", "Alcaraz"))
    assert sorted(c for c, _ in by["Full time"]) == ["1", "2"]
    assert ("Over", "22.5") in by["Total games won"]


async def test_fetch_odds_uses_cached_event(monkeypatch) -> None:
    """El evento ya cacheado por find_event no gasta otra llamada."""
    provider = PulseScoreOddsProvider(api_key="k")
    provider._events["201637412"] = EVENT

    async def _boom(*a, **kw):  # pragma: no cover - no debe llamarse
        raise AssertionError("fetch_odds debía resolver desde caché")

    provider._client.event = _boom
    choices = await provider.fetch_odds("pulsescore:201637412", "futbol")
    assert choices is not None
    assert any(c.market_name == "Corners 3-Way" for c in choices)


async def test_fetch_odds_gone_event_returns_empty(monkeypatch) -> None:
    """data:null (empezado/desconocido) -> [] definitivo, no error."""
    provider = PulseScoreOddsProvider(api_key="k")

    async def _fake_event(client, sport_path, event_id):
        return 200, None, True

    provider._client.event = _fake_event
    assert await provider.fetch_odds("pulsescore:196565074", "futbol") == []


def test_parse_event_skips_live_and_esoccer() -> None:
    live = dict(EVENT, live=True)
    assert pulsescore._parse_event(live, "futbol") is None
    esoccer = dict(EVENT, league="Esoccer Battle")
    assert pulsescore._parse_event(esoccer, "futbol") is None
    fx = pulsescore._parse_event(EVENT, "futbol")
    assert fx is not None and fx.fixture_id == "201637412"
    assert fx.start == datetime(2026, 10, 24, 8, 0, 0)
