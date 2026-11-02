"""Tests del pipeline de cuotas de mercado (odds).

Cubre la conversión fraccional->decimal, el mapeo pick->opción de
mercado del proveedor (lados home/away, líneas over/under, mercados
sin equivalente -> NULL) y la comparación temporal (apertura /
publicación / cierre).
"""

from datetime import datetime, timedelta

import pytest

from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.odds.base import odds_to_decimal
from app.services.odds.compare import (
    PickOddsComparison,
    aggregate_odds_stats,
    compare_pick,
    compare_picks,
    map_pick_choices,
)
from app.services.odds.snapshotter import _lookup_hint
from app.services.results.base import translate_team_aliases

EVENT = "sofascore:123"


def _event(sport="futbol", home="Real Betis", away="Getafe") -> OddsEvent:
    return OddsEvent(event_ext_id=EVENT, sport=sport, home_team=home, away_team=away)


def _snap(
    market: str,
    choice: str,
    cuota: float,
    group: str | None = None,
    apertura: float | None = None,
    captured_at: datetime | None = None,
    suspended: bool = False,
) -> OddsSnapshot:
    return OddsSnapshot(
        provider="allsportsapi2",
        event_ext_id=EVENT,
        market_name=market,
        choice_name=choice,
        choice_group=group,
        cuota=cuota,
        cuota_apertura=apertura,
        suspended=suspended,
        captured_at=captured_at or datetime(2026, 9, 18, 12, 0),
    )


def _pick(
    seleccion: str,
    mercado: str = "ganador",
    deporte: str = "fútbol",
    linea: float | None = None,
    cuota: float | None = 1.9,
) -> ParsedPick:
    return ParsedPick(
        es_apuesta=True,
        deporte=deporte,
        mercado=mercado,
        seleccion=seleccion,
        linea=linea,
        cuota=cuota,
        odds_event_id=EVENT,
        raw_message_id=1,
    )


# --- odds_to_decimal --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,esperado",
    [
        ("10/11", 1.9091),
        ("6/5", 2.2),
        ("2/1", 3.0),
        ("1/2", 1.5),
        ("2.20", 2.20),
        (3.0, 3.0),
        ("SP", None),
        ("", None),
        (None, None),
        (0, None),
        ("-1", None),
    ],
)
def test_odds_to_decimal(raw, esperado):
    assert odds_to_decimal(raw) == esperado


# --- map_pick_choices --------------------------------------------------------


def _futbol_snaps() -> list[OddsSnapshot]:
    return [
        _snap("Full time", "1", 1.6),
        _snap("Full time", "X", 3.8),
        _snap("Full time", "2", 5.5),
        _snap("Match goals", "Over", 1.9, group="2.5"),
        _snap("Match goals", "Under", 1.9, group="2.5"),
        _snap("Corners 2-Way", "Over", 1.8, group="9.5"),
        _snap("Corners 2-Way", "Under", 2.0, group="9.5"),
        _snap("Both teams to score", "Yes", 1.8),
        _snap("Both teams to score", "No", 2.0),
        _snap("Double chance", "1X", 1.15),
        _snap("Double chance", "X2", 2.3),
        _snap("Draw no bet", "1", 1.3),
        _snap("Draw no bet", "2", 3.4),
        _snap("Asian handicap", "(-0.5) Real Betis", 1.6),
        _snap("Asian handicap", "(0.5) Getafe", 2.4),
        _snap("1st half goals", "Over", 2.1, group="1.5"),
        _snap("1st half goals", "Under", 1.7, group="1.5"),
        _snap("1st half corners", "Over", 1.8, group="4.5"),
        _snap("1st half corners", "Under", 1.95, group="4.5"),
        _snap("2nd half corners", "Over", 1.85, group="5.5"),
        _snap("2nd half corners", "Under", 1.9, group="5.5"),
        _snap("1st half", "1", 2.5),
        _snap("1st half", "X", 2.1),
        _snap("1st half", "2", 4.5),
        _snap("2nd half result", "1", 1.9),
        _snap("2nd half result", "2", 4.2),
        _snap("1st half btts", "Yes", 3.4),
        _snap("1st half btts", "No", 1.3),
        _snap("1st half handicap", "(-0.5) Real Betis", 1.8),
        _snap("1st half handicap", "(0.5) Getafe", 2.0),
        _snap("Team 1 corners", "Over", 1.8, group="4.5"),
        _snap("Team 1 corners", "Under", 1.9, group="4.5"),
        _snap("Team 2 corners", "Over", 2.1, group="3.5"),
        _snap("Team 2 corners", "Under", 1.7, group="3.5"),
        _snap("Team 1 goals", "Over", 2.0, group="1.5"),
        _snap("Team 1 goals", "Under", 1.75, group="1.5"),
        _snap("Team 2 cards", "Over", 1.85, group="2.5"),
        _snap("Team 2 cards", "Under", 1.9, group="2.5"),
        _snap("1st half Team 1 corners", "Over", 2.0, group="2.5"),
        _snap("1st half Team 1 corners", "Under", 1.75, group="2.5"),
        _snap("2nd half Team 2 corners", "Over", 1.9, group="2.5"),
        _snap("2nd half Team 2 corners", "Under", 1.85, group="2.5"),
        _snap("2nd half Team 1 goals", "Over", 2.6, group="0.5"),
        _snap("2nd half Team 1 goals", "Under", 1.45, group="0.5"),
        _snap("Team 1 clean sheet", "Yes", 2.3),
        _snap("Team 1 clean sheet", "No", 1.6),
        _snap("Team 2 clean sheet", "Yes", 4.0),
        _snap("Team 1 win to nil", "Yes", 3.1),
        _snap("Team 1 win to nil", "No", 1.35),
        _snap("Team 1 to score", "Yes", 1.15),
        _snap("Team 1 to score", "No", 5.0),
        _snap("1st half Team 1 to score", "Yes", 1.6),
        _snap("1st half Team 1 to score", "No", 2.2),
        _snap("Team 2 to score both halves", "Yes", 2.4),
        _snap("Team 2 to score both halves", "No", 1.55),
        _snap("European handicap", "1", 4.5, group="-1"),
        _snap("European handicap", "X", 4.0, group="-1"),
        _snap("European handicap", "2", 1.5, group="-1"),
        _snap("1st half european handicap", "1", 3.2, group="-1"),
        _snap("1st half european handicap", "X", 2.1, group="-1"),
        _snap("1st half european handicap", "2", 2.8, group="-1"),
        _snap("Corners 1X2", "1", 1.7),
        _snap("Corners 1X2", "X", 7.5),
        _snap("Corners 1X2", "2", 4.2),
        _snap("1st half corners 1X2", "1", 1.85),
        _snap("1st half corners 1X2", "X", 8.0),
        _snap("1st half corners 1X2", "2", 3.6),
        _snap("Corners handicap", "(-3.5) Real Betis", 1.9),
        _snap("Corners handicap", "(3.5) Getafe", 1.9),
        _snap("Cards 1X2", "1", 2.2),
        _snap("Cards 1X2", "X", 6.0),
        _snap("Cards 1X2", "2", 2.4),
        _snap("Cards handicap", "(-1.5) Real Betis", 1.95),
        _snap("Cards handicap", "(1.5) Getafe", 1.85),
        _snap("2nd half cards 1X2", "2", 2.9),
        _snap("2nd half cards handicap", "(0.5) Getafe", 1.7),
        _snap("Half time/Full time", "1/1", 2.4),
        _snap("Half time/Full time", "X/1", 4.5),
        _snap("Half time/Full time", "1/X", 12.0),
        _snap("Half time/Full time", "2/1", 21.0),
        _snap("Half time/Full time", "X/X", 5.0),
        _snap("1st half correct score", "1-0", 3.4),
        _snap("1st half correct score", "0-0", 3.6),
        _snap("1st half correct score", "1-1", 7.0),
        _snap("1st half correct score", "2-1", 15.0),
        _snap("2nd half correct score", "1-0", 6.0),
        _snap("Last team to score", "1", 2.1),
        _snap("Last team to score", "X", 10.0),
        _snap("Last team to score", "2", 3.6),
        _snap("Goals odd/even", "Odd", 1.95),
        _snap("Goals odd/even", "Even", 1.85),
        _snap("1st half goals odd/even", "Odd", 2.1),
        _snap("1st half goals odd/even", "Even", 1.75),
        _snap("Team 1 goals odd/even", "Even", 1.9),
        _snap("To win from behind", "1", 7.5),
        _snap("To win from behind", "X", 4.8),
        _snap("To win from behind", "2", 12.0),
        _snap("Highest scoring half", "1st", 2.9),
        _snap("Highest scoring half", "X", 3.4),
        _snap("Highest scoring half", "2nd", 1.95),
        _snap("1st half double chance", "1X", 1.35),
        _snap("1st half double chance", "X2", 2.1),
        _snap("1st half draw no bet", "1", 1.8),
        _snap("1st half draw no bet", "2", 3.2),
        _snap("2nd half btts", "Yes", 2.4),
        _snap("2nd half btts", "No", 1.55),
        _snap("2nd half handicap", "(-0.5) Real Betis", 2.0),
        _snap("2nd half handicap", "(0.5) Getafe", 1.8),
    ]


def test_map_ganador_local():
    pick = _pick("Betis gana")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Full time", "1")


def test_map_ganador_visitante():
    pick = _pick("Getafe gana")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert m.choice_name == "2"


def test_map_empate():
    pick = _pick("Empate", mercado="ganador")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert m.choice_name == "X"


def test_map_ganador_tenis_lado_away():
    snaps = [_snap("Full time", "1", 2.4), _snap("Full time", "2", 1.6)]
    event = _event(sport="tenis", home="Gorzny", away="Nishesh Basavareddy")
    pick = _pick("Nishesh Basavareddy gana", deporte="tenis")
    m = map_pick_choices(pick, event, snaps)
    assert m is not None
    assert m.choice_name == "2"


def test_map_over_under_con_linea():
    pick = _pick("Más de 2.5 goles", mercado="over/under goles", linea=2.5)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "Match goals",
        "Over",
        "2.5",
    )


def test_map_under_corners():
    pick = _pick("Menos de 9.5 corners", mercado="over/under corners", linea=9.5)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Corners 2-Way", "Under")


def _tenis_snaps() -> list[OddsSnapshot]:
    return [
        _snap("Full time", "1", 1.57),
        _snap("Full time", "2", 2.37),
        _snap("Total games won", "Over", 1.83, group="22.5"),
        _snap("Total games won", "Under", 1.83, group="22.5"),
        _snap("1st set games", "Over", 1.07, group="7.5"),
        _snap("1st set games", "Under", 9.0, group="7.5"),
        _snap("1st set games", "Over", 1.25, group="8.5"),
        _snap("2nd set games", "Over", 1.44, group="9.5"),
        _snap("Total aces", "Over", 1.9, group="24.5"),
        _snap("Player 1 games", "Over", 1.8, group="12.5"),
        _snap("Player 1 games", "Under", 1.9, group="12.5"),
        _snap("Player 2 games", "Over", 1.7, group="11.5"),
        _snap("Player 2 games", "Under", 2.0, group="11.5"),
        _snap("Second set winner", "1", 1.6),
        _snap("Second set winner", "2", 2.3),
        _snap("Third set winner", "1", 1.7),
        _snap("Third set winner", "2", 2.1),
        _snap("1st set game handicap", "(-1.5) Carlos Alcaraz", 1.9),
        _snap("1st set game handicap", "(1.5) Jannik Sinner", 1.85),
        _snap("2nd set game handicap", "(-2.5) Carlos Alcaraz", 2.0),
        _snap("Game handicap", "(-3.5) Carlos Alcaraz", 1.75),
        _snap("Game handicap", "(3.5) Jannik Sinner", 2.05),
        _snap("Player 1 to win a set", "Yes", 1.05),
        _snap("Player 1 to win a set", "No", 9.0),
        _snap("Player 2 to win a set", "Yes", 1.4),
        _snap("Player 2 to win a set", "No", 2.8),
        _snap("Both to win a set", "Yes", 1.5),
        _snap("Both to win a set", "No", 2.4),
        _snap("Exact sets", "2", 1.7),
        _snap("Exact sets", "3", 2.4),
        _snap("Games odd/even", "Odd", 1.9),
        _snap("Games odd/even", "Even", 1.9),
        _snap("Correct score", "2-0", 1.5),
        _snap("Correct score", "2-1", 4.0),
        _snap("1st set correct score", "6-4", 3.2),
        _snap("1st set correct score", "4-6", 5.5),
        _snap("2nd set correct score", "6-3", 3.0),
    ]


def test_map_tenis_primer_set_juegos():
    """'Más de 7.5 juegos en el 1º set' -> 1st set games, no el total."""
    event = _event(sport="tenis", home="Luca Van Assche", away="Clement Tabur")
    pick = _pick(
        "Más de 7.5 juegos en el 1º set",
        mercado="over/under juegos",
        deporte="tenis",
        linea=7.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "1st set games",
        "Over",
        "7.5",
    )


def test_map_tenis_linea_baja_sin_mencionar_set():
    """Una línea <=13 en tenis solo puede ser de set: 'Más de 7.5
    juegos' sin decir 'set' también va al mercado de 1er set."""
    event = _event(sport="tenis", home="Luca Van Assche", away="Clement Tabur")
    pick = _pick(
        "Más de 7.5 juegos",
        mercado="over/under juegos",
        deporte="tenis",
        linea=7.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert m.market_name == "1st set games"


def test_map_tenis_total_partido_sigue_al_total():
    """Una línea alta sigue yendo al total del partido."""
    event = _event(sport="tenis", home="Luca Van Assche", away="Clement Tabur")
    pick = _pick(
        "Más de 22.5 juegos",
        mercado="over/under juegos",
        deporte="tenis",
        linea=22.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_group) == ("Total games won", "22.5")


def test_map_tenis_signo_mas_es_over():
    """'+7,5 juegos en el 1° set' — el signo '+' marca el over sin
    'más de' (caso real: pick 4283 quedó 'sin opción comparable')."""
    event = _event(sport="tenis", home="Tsitsipas", away="Hijikata")
    pick = _pick(
        "+7,5 juegos en el 1° set",
        mercado="over/under juegos",
        deporte="tenis",
        linea=7.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "1st set games",
        "Over",
        "7.5",
    )


def test_map_tenis_aces():
    """'25+ aces en el partido' -> Total aces (línea 24.5) — OddsPapi
    sí publica el mercado; faltaba el vocabulario (pick 3629)."""
    event = _event(sport="tenis", home="Vacherot", away="Harris")
    pick = _pick(
        "25+ aces en el partido",
        mercado="over/under",
        deporte="tenis",
        linea=24.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "Total aces",
        "Over",
        "24.5",
    )


def test_map_tenis_segundo_set():
    """'+9.5 juegos en el 2º set' -> 2nd set games, no el primero."""
    event = _event(sport="tenis", home="Herbert", away="Mrva")
    pick = _pick(
        "+9.5 juegos en el 2º set",
        mercado="over/under juegos",
        deporte="tenis",
        linea=9.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "2nd set games",
        "Over",
        "9.5",
    )


def test_map_tenis_juegos_jugador_local():
    """'Alcaraz más de 12.5 juegos' -> Player 1 games (total del
    jugador, no del partido). El canal escribe solo el apellido."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Alcaraz más de 12.5 juegos",
        mercado="over/under juegos",
        deporte="tenis",
        linea=12.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "Player 1 games",
        "Over",
        "12.5",
    )


def test_map_tenis_juegos_jugador_visitante():
    """El apellido del visitante casa con Player 2 games."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Sinner menos de 11.5 juegos",
        mercado="over/under juegos",
        deporte="tenis",
        linea=11.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "Player 2 games",
        "Under",
        "11.5",
    )


def test_map_tenis_juegos_jugador_en_set_es_miss_honesto():
    """'Sinner +4.5 juegos en el primer set' = juegos del jugador EN
    ESE set: no hay mercado — no debe caer a Player games ni al total
    del set."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Sinner más de 4.5 juegos en el primer set",
        mercado="over/under juegos",
        deporte="tenis",
        linea=4.5,
    )
    assert map_pick_choices(pick, event, _tenis_snaps()) is None


def test_map_tenis_aces_en_set_no_es_set_games():
    """'+4.5 aces en el 1er set' no es juegos del set: miss honesto
    en vez de casar contra '1st set games'."""
    event = _event(sport="tenis", home="Vacherot", away="Harris")
    pick = _pick(
        "+4.5 aces en el 1° set",
        mercado="over/under",
        deporte="tenis",
        linea=4.5,
    )
    assert map_pick_choices(pick, event, _tenis_snaps()) is None


def test_map_tenis_gana_segundo_set():
    """'Tsitsipas gana el 2º set' -> Second set winner."""
    event = _event(sport="tenis", home="Tsitsipas", away="Hijikata")
    pick = _pick("Tsitsipas gana el 2º set", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Second set winner", "1")


def test_map_tenis_gana_tercer_set_visitante():
    event = _event(sport="tenis", home="Tsitsipas", away="Hijikata")
    pick = _pick("Hijikata gana el 3er set", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Third set winner", "2")


def test_map_tenis_pick_con_linea_no_es_set_winner():
    """Un pick de juegos con línea nunca cae al set winner aunque el
    texto mencione el set."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Sinner más de 4.5 juegos en el primer set",
        mercado="over/under juegos",
        deporte="tenis",
        linea=4.5,
    )
    snaps = [s for s in _tenis_snaps() if "winner" not in s.market_name]
    snaps.append(_snap("Second set winner", "2", 1.4))
    assert map_pick_choices(pick, event, snaps) is None


def test_map_tenis_handicap_juegos_set():
    """'Alcaraz -1.5 juegos 1er set' -> 1st set game handicap, no el
    hándicap del partido."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Alcaraz -1.5 juegos 1er set",
        mercado="hándicap",
        deporte="tenis",
        linea=-1.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert m.market_name == "1st set game handicap"
    assert "Alcaraz" in m.choice_name


def test_map_tenis_handicap_segundo_set():
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Alcaraz -2.5 juegos en el 2º set",
        mercado="hándicap",
        deporte="tenis",
        linea=-2.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert m.market_name == "2nd set game handicap"


def test_map_tenis_handicap_sin_set_va_al_partido():
    """'Alcaraz -3.5 juegos' sin mencionar set -> Game handicap del
    partido, no el de set."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Alcaraz -3.5 juegos",
        mercado="hándicap",
        deporte="tenis",
        linea=-3.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert m.market_name == "Game handicap"


def test_map_tenis_handicap_set_sin_mercado_es_miss():
    """Sin el mercado de set, un pick de hándicap de set NO cae al
    ganador del partido."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Alcaraz -1.5 juegos 1er set",
        mercado="hándicap",
        deporte="tenis",
        linea=-1.5,
    )
    snaps = [s for s in _tenis_snaps() if "handicap" not in s.market_name.lower()]
    assert map_pick_choices(pick, event, snaps) is None


def test_map_tenis_gana_un_set():
    """'Alcaraz gana un set' -> Player 1 to win a set Yes."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick("Alcaraz gana un set", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Player 1 to win a set", "Yes")


def test_map_tenis_no_gana_ningun_set():
    """'Sinner no gana ningún set' -> Player 2 to win a set No."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick("Sinner no gana ningún set", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Player 2 to win a set", "No")


def test_map_tenis_ambos_ganan_un_set():
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick("Ambos ganan un set", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Both to win a set", "Yes")


def test_map_tenis_sets_exactos():
    """'Se juegan 3 sets' -> Exact sets "3"."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick("Se juegan 3 sets", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Exact sets", "3")


def test_map_tenis_25_sets_no_es_exact():
    """'+2.5 sets' no cae a Exact sets (el dígito viene de decimal)."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick(
        "Más de 2.5 sets",
        mercado="over/under",
        deporte="tenis",
        linea=2.5,
    )
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is None or m.market_name != "Exact sets"


def test_map_tenis_juegos_pares():
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick("Número par de juegos", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Games odd/even", "Even")


def test_map_tenis_gana_20_sets():
    """'Alcaraz gana 2-0' -> Correct score (marcador en sets)."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick("Alcaraz gana 2-0", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Correct score", "2-0")


def test_map_tenis_64_primer_set():
    """'6-4 en el 1er set' -> 1st set correct score "6-4", no el
    ganador del set ni el marcador del partido."""
    event = _event(sport="tenis", home="Carlos Alcaraz", away="Jannik Sinner")
    pick = _pick("Alcaraz gana el 1er set 6-4", deporte="tenis")
    m = map_pick_choices(pick, event, _tenis_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("1st set correct score", "6-4")


def test_map_primera_parte_goles():
    """'+1.5 goles 1ª parte' -> 1st half goals, no Match goals."""
    pick = _pick(
        "Más de 1.5 goles en la primera parte",
        mercado="over/under goles",
        linea=1.5,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "1st half goals",
        "Over",
        "1.5",
    )


def test_map_primera_parte_corners():
    pick = _pick(
        "Más de 4.5 córners en la 1ª parte",
        mercado="over/under córners",
        linea=4.5,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_group) == ("1st half corners", "4.5")


def test_map_segunda_parte_corners():
    pick = _pick(
        "Más de 5.5 córners en la segunda parte",
        mercado="over/under córners",
        linea=5.5,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_group) == ("2nd half corners", "5.5")


def test_map_primera_parte_no_cae_a_fulltime():
    """Sin mercado de 1ª parte, el pick NO se compara contra el
    total del partido."""
    pick = _pick(
        "Más de 1.5 goles en la primera parte",
        mercado="over/under goles",
        linea=1.5,
    )
    snaps = [s for s in _futbol_snaps() if "half" not in s.market_name.lower()]
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_ambos_marcan_primera_parte():
    pick = _pick(
        "Ambos marcan en la primera parte",
        mercado="ambos marcan",
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("1st half btts", "Yes")


def test_map_gana_segunda_parte():
    pick = _pick("Betis gana la segunda parte", mercado="ganador")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("2nd half result", "1")


def test_map_handicap_primera_parte():
    """'Betis -0.5 en la primera parte' -> 1st half handicap."""
    pick = _pick(
        "Betis -0.5 en la primera parte",
        mercado="hándicap",
        linea=-0.5,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert m.market_name == "1st half handicap"
    assert "Betis" in m.choice_name


def test_map_marcador_primera_parte_no_cae_en_fulltime():
    """'gana 1-0 en la primera parte' NO es el marcador del partido:
    sin el mercado "1st half correct score" -> miss honesto."""
    snaps = _futbol_snaps() + [
        _snap("Correct score", "1-0", 6.5),
        _snap("Correct score", "2-0", 8.0),
    ]
    snaps = [s for s in snaps if s.market_name != "1st half correct score"]
    pick = _pick("Betis gana 1-0 en la primera parte", mercado="ganador")
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_marca_primero_primera_parte_no_cae_en_fulltime():
    """'marca primero en la 1ª parte' NO es "First team to score" del
    partido -> miss honesto."""
    snaps = _futbol_snaps() + [
        _snap("First team to score", "1", 1.7),
        _snap("First team to score", "2", 2.9),
    ]
    pick = _pick(
        "Betis marca primero en la primera parte",
        mercado="primer gol",
    )
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_equipo_corners_nombre_corto():
    """'+4 córners del Betis' casa "Real Betis" por token distintivo
    -> Team 1 corners, NO el total del partido."""
    pick = _pick(
        "Más de 4.5 córners del Betis",
        mercado="over/under córners",
        linea=4.5,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "Team 1 corners",
        "Over",
        "4.5",
    )


def test_map_equipo_corners_segunda_parte():
    """'+3 córners del Getafe 2ª parte' -> 2nd half Team 2 corners."""
    pick = _pick(
        "Más de 2.5 córners del Getafe en la segunda parte",
        mercado="over/under córners",
        linea=2.5,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_group) == (
        "2nd half Team 2 corners",
        "2.5",
    )


def test_map_equipo_goles_segunda_parte():
    pick = _pick(
        "Betis marca menos de 0.5 goles en la segunda parte",
        mercado="over/under goles",
        linea=0.5,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "2nd half Team 1 goals",
        "Under",
    )


def test_map_equipo_tarjetas_fulltime():
    pick = _pick(
        "Más de 2.5 tarjetas del Getafe",
        mercado="over/under tarjetas",
        linea=2.5,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Team 2 cards", "Over")


def test_map_equipo_veto_del_partido():
    """'+9 córners en el partido del Betis' = total del partido,
    no del equipo."""
    pick = _pick(
        "Más de 9.5 córners en el partido del Betis",
        mercado="over/under córners",
        linea=9.5,
    )
    snaps = _futbol_snaps() + [
        _snap("Corners 2-Way", "Over", 1.9, group="9.5"),
        _snap("Corners 2-Way", "Under", 1.85, group="9.5"),
    ]
    m = map_pick_choices(pick, _event(), snaps)
    assert m is not None
    assert m.market_name == "Corners 2-Way"


def test_map_equipo_token_ambiguo_miss():
    """'Madrid +5 córners' en Real Madrid vs Atlético Madrid: el
    token casa con AMBOS nombres — miss honesto, nunca el total
    del partido ni adivinar el lado."""
    from app.models.odds_snapshot import OddsEvent

    ev = OddsEvent(
        event_ext_id="x",
        sport="futbol",
        home_team="Real Madrid",
        away_team="Atletico Madrid",
    )
    snaps = [
        _snap("Corners 2-Way", "Over", 1.9, group="5.5"),
        _snap("Corners 2-Way", "Under", 1.85, group="5.5"),
    ]
    pick = _pick(
        "Más de 5.5 córners del Madrid",
        mercado="over/under córners",
        linea=5.5,
    )
    assert map_pick_choices(pick, ev, snaps) is None


def test_map_porteria_a_cero():
    """Pick #6150 real: 'Portería a 0' / 'Betis - Sí' -> Team 1
    clean sheet Yes."""
    pick = _pick("Betis - Sí", mercado="portería a 0")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Team 1 clean sheet", "Yes")


def test_map_porteria_a_cero_no():
    pick = _pick("Betis - No", mercado="portería a 0")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Team 1 clean sheet", "No")


def test_map_gana_sin_encajar():
    """Pick #3662 real: 'Inglaterra gana sin encajar' -> win to nil.
    Aquí con el Betis."""
    pick = _pick(
        "Betis gana sin encajar goles",
        mercado="ganador",
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Team 1 win to nil", "Yes")


def test_map_equipo_marca():
    pick = _pick("El Betis marca", mercado="equipo marca")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Team 1 to score", "Yes")


def test_map_equipo_marca_primera_parte():
    pick = _pick("Betis marca en la primera parte", mercado="equipo marca")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "1st half Team 1 to score",
        "Yes",
    )


def test_map_equipo_marca_ambas_partes():
    """'marca en ambas partes' -> Team N to score both halves — no
    es HT/FT ni to score plano."""
    pick = _pick("Getafe marca en ambas partes", mercado="equipo marca")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "Team 2 to score both halves",
        "Yes",
    )


def test_map_marca_primero_no_es_team_to_score():
    """'Betis marca primero' es First team to score, no to score."""
    snaps = _futbol_snaps() + [
        _snap("First team to score", "1", 1.7),
        _snap("First team to score", "2", 2.9),
    ]
    pick = _pick("Betis marca primero", mercado="primer gol")
    m = map_pick_choices(pick, _event(), snaps)
    assert m is not None
    assert m.market_name == "First team to score"


def test_map_descanso_final_es_1_1():
    """'Betis gana al descanso y al final' = HT/FT "1/1" — nunca el
    1X2 de una mitad (otro contrato)."""
    pick = _pick(
        "Betis gana al descanso y al final",
        mercado="descanso/final",
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Half time/Full time", "1/1")


def test_map_descanso_final_codigo_literal():
    pick = _pick("1/1", mercado="descanso/final")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Half time/Full time", "1/1")


def test_map_empata_descanso_gana_final_es_x_1():
    pick = _pick(
        "Empata al descanso y gana al final",
        mercado="descanso/final",
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    # Sin equipo en el texto no hay lado: "empata" da X al descanso
    # pero el final necesita equipo -> miss honesto... o no.
    assert m is None or m.market_name == "Half time/Full time"


def test_map_descanso_final_sin_mercado_miss():
    """Sin el mercado HT/FT, un pick descanso/final sigue siendo
    miss — nunca cae al 1X2 de una mitad."""
    snaps = [s for s in _futbol_snaps() if s.market_name != "Half time/Full time"]
    pick = _pick(
        "Betis gana al descanso y al final",
        mercado="descanso/final",
    )
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_ambas_partes_miss():
    """'gana ambas partes' = To Win Both Halves: NO es el "1/1" del
    HT/FT (ir ganando al descanso no exige ganar cada mitad)."""
    pick = _pick("Betis gana ambas partes", mercado="ganador")
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def test_map_primera_y_segunda_no_es_htft():
    """'gana la primera y la segunda parte' = To Win Both Halves,
    no el "1/1" del HT/FT."""
    pick = _pick("Betis gana la primera y la segunda parte")
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def test_map_marcador_primera_parte():
    """'gana 1-0 la primera parte' -> '1st half correct score'."""
    pick = _pick("Betis gana 1-0 la primera parte")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "1st half correct score",
        "1-0",
    )


def test_map_marcador_segunda_parte_no_cae_a_ft():
    """'gana 2-0 la segunda parte' solo tiene "2nd half correct
    score" con "1-0" -> miss honesto, nunca el marcador FT."""
    pick = _pick("Betis gana 2-0 la segunda parte")
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def test_map_marca_ultimo_gol():
    pick = _pick("Betis marca el último gol")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Last team to score", "1")


def test_map_marca_ultimo_visitante():
    pick = _pick("último gol el Getafe")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Last team to score", "2")


def test_map_goles_impar():
    pick = _pick("Número impar de goles en el partido")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Goals odd/even", "Odd")


def test_map_goles_par_primera_parte():
    pick = _pick("Número par de goles primera parte")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "1st half goals odd/even",
        "Even",
    )


def test_map_goles_par_equipo():
    pick = _pick("Goles del Betis pares")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "Team 1 goals odd/even",
        "Even",
    )


def test_map_corners_impar_miss():
    """Par/impar de córners: mercado del catálogo sin traducir —
    miss honesto, nunca el odd/even de goles."""
    pick = _pick("Número impar de córners")
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def test_map_remonta():
    pick = _pick("El Betis gana remontando")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("To win from behind", "1")


def test_map_parte_con_mas_goles():
    pick = _pick("Se marcan más goles en la segunda parte")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "Highest scoring half",
        "2nd",
    )


def test_map_doble_oportunidad_primera_parte():
    pick = _pick("Betis o empate primera parte", mercado="doble oportunidad")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "1st half double chance",
        "1X",
    )


def test_map_empate_no_valido_primera_parte():
    pick = _pick("Betis empate no válido primera parte")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("1st half draw no bet", "1")


def test_map_ambos_marcan_segunda_parte():
    pick = _pick("Ambos marcan en la segunda parte")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("2nd half btts", "Yes")


def test_map_handicap_segunda_parte():
    pick = _pick("Betis hándicap -0.5 segunda parte", linea=-0.5)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "2nd half handicap",
        "(-0.5) Real Betis",
    )


# --- hándicap europeo + 1X2/hándicap de córners y tarjetas ------------------


def test_map_handicap_europeo_local():
    """'Betis hándicap europeo -1' -> 'European handicap' 1 con línea -1."""
    pick = _pick("Betis hándicap europeo -1", mercado="hándicap", linea=-1.0)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("European handicap", "1")


def test_map_handicap_europeo_empate():
    """El empate del hándicap europeo es la opción 'X' con la línea."""
    pick = _pick("Empate hándicap europeo -1", mercado="hándicap", linea=-1.0)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("European handicap", "X")


def test_map_handicap_europeo_visitante():
    pick = _pick("Getafe hándicap europeo +1", mercado="hándicap", linea=1.0)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("European handicap", "2")


def test_map_handicap_europeo_linea_no_ofrecida():
    """Solo se publicó la línea -1: un pick -2 es miss honesto."""
    pick = _pick("Betis hándicap europeo -2", mercado="hándicap", linea=-2.0)
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def test_map_handicap_europeo_primera_parte():
    pick = _pick(
        "Betis hándicap europeo -1 primera parte",
        mercado="hándicap",
        linea=-1.0,
    )
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("1st half european handicap", "1")


def test_map_corners_1x2_local():
    """'saca más córners el Betis' -> 'Corners 1X2' 1 (quién saca
    más, no el total O/U)."""
    pick = _pick("El Betis saca más córners", mercado="córners")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Corners 1X2", "1")


def test_map_corners_1x2_empate():
    pick = _pick("Empate a córners", mercado="córners")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Corners 1X2", "X")


def test_map_corners_1x2_visitante():
    pick = _pick("Más córners el Getafe", mercado="córners")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Corners 1X2", "2")


def test_map_corners_1x2_primera_parte():
    pick = _pick("Betis más córners primera parte", mercado="córners")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("1st half corners 1X2", "1")


def test_map_corners_1x2_sin_mercado_no_cae_al_1x2():
    """Sin 'Corners 1X2' el pick de stats nunca cae al 'Full time'."""
    snaps = [s for s in _futbol_snaps() if s.market_name != "Corners 1X2"]
    pick = _pick("Más córners el Getafe", mercado="córners")
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_corners_handicap():
    """'hándicap córners -3.5 Betis' -> 'Corners handicap' con signo."""
    pick = _pick("hándicap córners Betis -3.5", mercado="hándicap", linea=-3.5)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "Corners handicap",
        "(-3.5) Real Betis",
    )


def test_map_corners_handicap_sin_palabra_handicap():
    """'Betis -3.5 córners' (línea negativa = hándicap, no total)."""
    pick = _pick("Betis -3.5 córners", mercado="córners", linea=-3.5)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "Corners handicap",
        "(-3.5) Real Betis",
    )


def test_map_corners_handicap_lado_no_publicado_miss():
    """'Getafe -3.5 córners': solo se vende (-3.5) Betis/(+3.5)
    Getafe — el lado contrario no existe -> miss honesto."""
    pick = _pick("hándicap córners Getafe -3.5", mercado="hándicap", linea=-3.5)
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def test_map_tarjetas_1x2():
    pick = _pick("El Getafe recibe más tarjetas", mercado="tarjetas")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Cards 1X2", "2")


def test_map_tarjetas_handicap():
    pick = _pick("hándicap tarjetas Betis -1.5", mercado="hándicap", linea=-1.5)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "Cards handicap",
        "(-1.5) Real Betis",
    )


def test_map_tarjetas_1x2_segunda_parte():
    pick = _pick("Más tarjetas el Getafe segunda parte", mercado="tarjetas")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("2nd half cards 1X2", "2")


def test_map_over_under_linea_no_ofrecida():
    """La línea del pick no existe en el mercado -> NULL, no se inventa."""
    pick = _pick("Más de 11.5 corners", mercado="over/under corners", linea=11.5)
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def test_map_ambos_marcan():
    pick = _pick("Ambos marcan", mercado="ambos marcan")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Both teams to score", "Yes")


def test_map_doble_oportunidad_codigo():
    pick = _pick("1X", mercado="doble oportunidad")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Double chance", "1X")


def test_map_doble_oportunidad_equipo():
    pick = _pick("Betis o empate", mercado="doble oportunidad")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert m.choice_name == "1X"


def test_map_doble_oportunidad_sin_mercado_dc():
    """El proveedor solo trae 1X2 seco (betexplorer): un pick de
    doble oportunidad NO puede caer a la victoria seca — X2 @1.5
    contra el 2 seco @2.91 sería comparación inventada (bug real,
    pick 5530)."""
    snaps = [
        _snap("Full time", "1", 2.10),
        _snap("Full time", "X", 3.30),
        _snap("Full time", "2", 2.91),
    ]
    pick = _pick("Getafe gana o empata el partido", mercado="doble oportunidad")
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_doble_oportunidad_ambigua_no_adivina():
    """mercado='doble oportunidad' con selección 'X gana' (sin señal
    de empate ni 'o'): NO se adivina 1X/X2 por el lado — los casos
    reales (5429/5645) eran 'resultado sin empate' mal extraídos, y
    adivinar habría comparado contra la pata equivocada."""
    pick = _pick("Getafe gana", mercado="doble oportunidad")
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def test_map_doble_oportunidad_gana_o_empata():
    """'gana o empata' (variante verbal de 'o empate') sigue casando
    con 1X/X2 — la señal 'empat' es explícita, no es adivinanza."""
    pick = _pick("Getafe gana o empata", mercado="doble oportunidad")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Double chance", "X2")


def test_map_doble_oportunidad_gana_cualquiera_es_12():
    """'EquipoA o EquipoB' (gana cualquiera) casa con la opción 12."""
    snaps = _futbol_snaps() + [_snap("Double chance", "12", 1.9)]
    pick = _pick("Betis o Getafe", mercado="doble oportunidad")
    m = map_pick_choices(pick, _event(), snaps)
    assert m is not None
    assert m.choice_name == "12"


def test_map_empate_no_cuenta_es_dnb():
    """'empate no cuenta'/'empate no válido' = Draw no bet (variante
    que el regex no cubría — el texto del pick 5645 lo dice así)."""
    pick = _pick("Getafe gana el partido, empate no cuenta", mercado="ganador")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Draw no bet", "2")


def test_map_gana_marcador_no_es_ganador():
    """ "gana 2-0" = marcador de sets (~1.5-2.0), NO la victoria
    seca (~1.15): sin mercado de marcador en el proveedor -> None.
    Casos reales: picks 662/4007/4551 casados mal a Full time."""
    snaps = [
        _snap("Full time", "1", 1.16),
        _snap("Full time", "2", 4.5),
    ]
    pick = _pick("Mannarino gana 2-0", mercado="ganador")
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_gana_marcador_con_mercado():
    """Si el proveedor publica 'Correct score', casa la puntuación."""
    snaps = [
        _snap("Full time", "1", 1.16),
        _snap("Correct score", "2-0", 1.55),
        _snap("Correct score", "2-1", 3.1),
    ]
    pick = _pick("Betis gana 2-0", mercado="ganador")
    m = map_pick_choices(pick, _event(), snaps)
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Correct score", "2-0")


def test_map_empate_no_valido():
    pick = _pick("Betis", mercado="resultado sin empate")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Draw no bet", "1")


def test_map_dnb_sin_mercado_no_cae_a_ganador():
    """Pick DNB sin filas "Draw no bet" en el evento -> None. Antes
    el bloque caía al fallback de ganador y comparaba la cuota DNB
    contra la victoria seca (precios distintos) — caso real: pick
    5429 tras re-etiquetar, el mapper daba Full time/Atlético-MG."""
    snaps = [
        _snap("Full time", "1", 1.6),
        _snap("Full time", "X", 3.8),
        _snap("Full time", "2", 5.5),
        _snap("Double chance", "X2", 2.3),
    ]
    pick = _pick("Getafe gana", mercado="resultado sin empate")
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_handicap():
    pick = _pick("Real Betis -0.5", mercado="hándicap asiático", linea=-0.5)
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert m.market_name == "Asian handicap"
    assert "Betis" in m.choice_name


def test_map_mercado_sin_equivalente():
    """Mercados sin traducción (props de jugador) -> None."""
    pick = _pick("Mérlín marca", mercado="jugador")
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


def _futbol_prop_snaps() -> list[OddsSnapshot]:
    """Props de jugador: el choice_name ES el jugador y la línea
    viaja en choice_group ("Over 0.5") — como emite oddspapi.py."""
    return _futbol_snaps() + [
        _snap("Goalscorer anytime", "Alexander Isak", 2.1),
        _snap("Goalscorer anytime", "Nico Paz", 3.4, group="Over 0.5"),
        _snap("Goalscorer anytime", "Nico Paz", 9.0, group="Under 0.5"),
        _snap("First goalscorer", "Alexander Isak", 5.5),
        _snap("Last goalscorer", "Alexander Isak", 5.5),
        _snap("Player to be booked", "Alexis Mac Allister", 2.6, group="Over 0.5"),
        _snap("Player to be booked", "Alexis Mac Allister", 1.45, group="Under 0.5"),
        _snap("Player assists", "Nico Paz", 2.2, group="Over 0.5"),
        _snap("Player assists", "Nico Paz", 1.6, group="Under 0.5"),
        _snap("Player shots", "Alexander Isak", 1.8, group="Over 1.5"),
        _snap("Player shots", "Alexander Isak", 1.9, group="Under 1.5"),
        _snap("Player shots on target", "Alexander Isak", 2.0, group="Over 0.5"),
        _snap("Player goals", "Alexander Isak", 1.4, group="Over 0.5"),
        _snap("Player goals", "Alexander Isak", 4.5, group="Over 1.5"),
        _snap("Player saves", "David Raya", 2.0, group="Over 3.5"),
        _snap("Player tackles", "Pepelu", 2.1, group="Over 1.5"),
        _snap("Player offsides", "Alexander Isak", 3.2, group="Over 0.5"),
        _snap("Player fouls", "Pepelu", 1.9, group="Over 0.5"),
    ]


def test_prop_scorer_anytime():
    """ "Isak marca gol" -> Goalscorer anytime (jugador, no equipo)."""
    m = map_pick_choices(
        _pick("Isak marca gol", mercado="jugador"), _event(), _futbol_prop_snaps()
    )
    assert m is not None
    assert (m.market_name, m.choice_name) == (
        "Goalscorer anytime",
        "Alexander Isak",
    )


def test_prop_first_and_last_scorer_distintos():
    """Primer/último goleador son contratos separados del anytime."""
    m = map_pick_choices(
        _pick("Isak marca el primer gol", mercado="jugador"),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_name) == ("First goalscorer", "Alexander Isak")
    m = map_pick_choices(
        _pick("marca el último gol Isak", mercado="jugador"),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_name) == ("Last goalscorer", "Alexander Isak")


def test_prop_carded():
    """ "Mac Allister verá tarjeta" -> Player to be booked (Over 0.5)."""
    m = map_pick_choices(
        _pick("Mac Allister verá tarjeta", mercado="jugador"),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_group) == ("Player to be booked", "Over 0.5")


def test_prop_formas_reales_del_canal():
    """Las redacciones reales de la BD: nombre al FINAL y
    "Recibe amonestación <nombre>"."""
    m = map_pick_choices(
        _pick("Anota gol en cualquier momento Alexander Isak", mercado="jugador"),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_name) == (
        "Goalscorer anytime",
        "Alexander Isak",
    )
    m = map_pick_choices(
        _pick("Recibe amonestación Alexis Mac Allister", mercado="jugador"),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_group) == ("Player to be booked", "Over 0.5")


def test_prop_marca_o_asiste_sin_mercado_es_miss():
    """ "Marca o asiste Gakpo": el proveedor no publica un mercado
    "score or assist" — miss honesto, nunca cae al scorer anytime."""
    m = map_pick_choices(
        _pick("Marca o asiste Cody Gakpo", mercado="jugador"),
        _event(),
        _futbol_prop_snaps(),
    )
    assert m is None


def test_prop_goals_con_linea_no_es_match_goals():
    """ "Isak marca 2 goles": prop de jugador, nunca el total del
    partido — y la línea 2+ casa Over 1.5, no Over 0.5."""
    m = map_pick_choices(
        _pick("Isak marca 2 goles", mercado="jugador", linea=2.0),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_group) == ("Player goals", "Over 1.5")


def test_prop_jugador_con_linea_no_cae_a_total_partido():
    """Guarda O/U: "Morata +1.5 goles" sin mercado de jugador NO
    puede caer a "Match goals" — es miss honesto."""
    m = map_pick_choices(
        _pick("Morata +1.5 goles", mercado="jugador", linea=1.5),
        _event(),
        _futbol_prop_snaps(),
    )
    assert m is None


def test_prop_saves_tackles_offsides():
    """Paradas de portero, tackles y fueras de juego de jugador."""
    m = map_pick_choices(
        _pick("Raya +3.5 paradas", mercado="jugador", linea=3.5),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_group) == ("Player saves", "Over 3.5")
    m = map_pick_choices(
        _pick("Pepelu +1.5 tackles", mercado="jugador", linea=1.5),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_name) == ("Player tackles", "Pepelu")
    m = map_pick_choices(
        _pick("Isak cae en fuera de juego", mercado="jugador"),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_group) == ("Player offsides", "Over 0.5")


def test_prop_linea_equivocada_es_miss():
    """ "Isak +4.5 goles": el proveedor solo vende Over 0.5/1.5 —
    la línea pedida no existe -> miss, nunca otra línea."""
    m = map_pick_choices(
        _pick("Isak +4.5 goles", mercado="jugador", linea=4.5),
        _event(),
        _futbol_prop_snaps(),
    )
    assert m is None


def test_prop_jugador_desconocido_es_miss():
    """Jugador que el proveedor no lista -> None (no fuzzy forzado)."""
    m = map_pick_choices(
        _pick("Mérlín marca gol", mercado="jugador"), _event(), _futbol_prop_snaps()
    )
    assert m is None


def test_prop_apellido_compartido_ambiguo():
    """Dos jugadores con el mismo apellido en el mercado: "Silva
    marca" casa con Bernardo Y André Silva (0.8 ambos) -> ambiguo,
    miss en vez de casar el primero de la lista."""
    snaps = _futbol_prop_snaps() + [
        _snap("Goalscorer anytime", "Bernardo Silva", 3.5),
        _snap("Goalscorer anytime", "Andre Silva", 4.0),
    ]
    m = map_pick_choices(_pick("Silva marca gol", mercado="jugador"), _event(), snaps)
    assert m is None


def test_prop_no_clasifica_picks_de_equipo():
    """Picks normales NO entran en la rama de props: "Betis gana",
    "Más de 2.5 goles", "ambos marcan" siguen su camino habitual."""
    m = map_pick_choices(_pick("Betis gana"), _event(), _futbol_prop_snaps())
    assert (m.market_name, m.choice_name) == ("Full time", "1")
    m = map_pick_choices(
        _pick("Más de 2.5 goles", mercado="goles", linea=2.5),
        _event(),
        _futbol_prop_snaps(),
    )
    assert (m.market_name, m.choice_group) == ("Match goals", "2.5")


def test_map_primera_parte_usa_1st_half():
    snaps = _futbol_snaps() + [
        _snap("1st half", "1", 2.2),
        _snap("1st half", "X", 2.1),
        _snap("1st half", "2", 4.5),
    ]
    pick = _pick("Betis gana la primera parte", mercado="ganador 1ª parte")
    m = map_pick_choices(pick, _event(), snaps)
    assert m is not None
    assert (m.market_name, m.choice_name) == ("1st half", "1")


def test_map_primera_parte_no_cae_en_fulltime():
    """Un pick de descanso sin filas "1st half" queda NULL — nunca se
    compara contra la cuota del tiempo completo (mercados distintos)."""
    pick = _pick("Betis gana la primera parte")
    snaps = [s for s in _futbol_snaps() if s.market_name != "1st half"]
    assert map_pick_choices(pick, _event(), snaps) is None


def test_map_marca_primero():
    snaps = _futbol_snaps() + [
        _snap("First team to score", "1", 1.7),
        _snap("First team to score", "2", 2.9),
        _snap("First team to score", "X", 9.0),
    ]
    pick = _pick("Betis marca primero", mercado="primer gol")
    m = map_pick_choices(pick, _event(), snaps)
    assert m is not None
    assert (m.market_name, m.choice_name) == ("First team to score", "1")


def test_map_set_actual():
    snaps = [
        _snap("Current set winner", "1", 1.5),
        _snap("Current set winner", "2", 2.6),
    ]
    event = _event(sport="tenis", home="Alcaraz", away="Sinner")
    pick = _pick("Alcaraz gana el set actual", deporte="tenis")
    m = map_pick_choices(pick, event, snaps)
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Current set winner", "1")


# --- aliases de equipo en el hint de cuotas ----------------------------------


def test_translate_team_aliases_expande():
    assert translate_team_aliases("depò - barça") == "deportivo - barcelona"
    assert (
        translate_team_aliases("psg vs man utd")
        == "paris saint germain vs manchester united"
    )


def test_translate_team_aliases_sin_alias_no_toca():
    assert translate_team_aliases("Sevilla vs Osasuna") == "sevilla vs osasuna"


def test_lookup_hint_futbol_expande_aliases():
    pick = ParsedPick(es_apuesta=True, deporte="fútbol", evento="depò - barça")
    assert _lookup_hint(pick, "futbol") == "deportivo - barcelona"


def test_lookup_hint_tenis_sin_aliases():
    """En tenis el hint es el jugador: no aplica el diccionario de equipos."""
    pick = ParsedPick(
        es_apuesta=True,
        deporte="tenis",
        evento="ATP Shanghái",
        seleccion="Alcaraz gana",
    )
    hint = _lookup_hint(pick, "tenis")
    assert hint and "alcaraz" in hint.lower()


def test_map_over_under_juegos_tenis():
    snaps = [
        _snap("Full time", "1", 2.4),
        _snap("Full time", "2", 1.6),
        _snap("Total games won", "Over", 1.9, group="22.5"),
        _snap("Total games won", "Under", 1.9, group="22.5"),
    ]
    event = _event(sport="tenis", home="Cecchinato", away="Moeller")
    pick = _pick(
        "Más de 22.5 juegos",
        mercado="over/under juegos",
        deporte="tenis",
        linea=22.5,
    )
    m = map_pick_choices(pick, event, snaps)
    assert m is not None
    assert (m.market_name, m.choice_name, m.choice_group) == (
        "Total games won",
        "Over",
        "22.5",
    )


# --- compare_pick ------------------------------------------------------------


async def _seed_pick(session, seleccion="Betis gana", **kwargs) -> ParsedPick:
    raw = TelegramRawMessage(
        channel_id=1, channel_name="test", message_id=1, text="pick"
    )
    session.add(raw)
    await session.commit()
    pick = _pick(seleccion, **kwargs)
    pick.raw_message_id = raw.id
    pick.created_at = datetime(2026, 9, 18, 10, 0)
    session.add(pick)
    await session.commit()
    await session.refresh(pick)
    return pick


async def test_compare_pick_sin_evento(session):
    """Pick sin `odds_event_id` -> mapeado=False, sin inventar."""
    pick = await _seed_pick(session)
    pick.odds_event_id = None
    c = await compare_pick(session, pick)
    assert c.mapeado is False
    assert c.cuota_tipster == pick.cuota


async def test_compare_pick_puntos_temporales(session):
    """Apertura = la que regala la API; publicación = captura más
    cercana a `created_at`; cierre = última captura."""
    pick = await _seed_pick(session)  # created_at = 10:00
    session.add(_event())
    base = datetime(2026, 9, 18, 12, 0)
    session.add(_snap("Full time", "1", 1.6, apertura=1.7, captured_at=base))
    session.add(
        _snap(
            "Full time",
            "1",
            1.4,
            apertura=1.7,
            captured_at=base + timedelta(hours=5),
        )
    )
    await session.commit()
    c = await compare_pick(session, pick)
    assert c.mapeado is True
    assert c.cuota_apertura == 1.7
    # La captura de las 12:00 es la más cercana a las 10:00.
    assert c.cuota_publicacion == 1.6
    assert c.cuota_cierre == 1.4
    assert c.capturas == 2
    # Tipster @1.9 > mercado 1.6 al publicar -> cuota no existía.
    assert c.cuota_disponible is False
    # CLV = (1.9/1.4 - 1)*100 = +35.71% — batió el cierre.
    assert c.clv_pct == 35.71


async def test_compare_pick_ignora_suspendidos(session):
    """Un punto suspendido (mercado cerrado, cuota 1.00) no es un
    precio apostable: ni publicación ni cierre pueden caer en él,
    aunque sea la captura más cercana a `created_at`. Sin el filtro
    el pick saldría "inflado" contra una cuota que nadie pudo tomar."""
    pick = await _seed_pick(session)  # created_at = 10:00
    session.add(_event())
    base = datetime(2026, 9, 18, 9, 0)
    session.add(_snap("Full time", "1", 1.6, captured_at=base))
    session.add(
        _snap(
            "Full time",
            "1",
            1.0,
            captured_at=base + timedelta(hours=1, minutes=30),
            suspended=True,
        )
    )
    await session.commit()
    c = await compare_pick(session, pick)
    assert c.mapeado is True
    # Referencia real (1.6 a las 9:00), no el punto cerrado (1.0 a las 10:30).
    assert c.cuota_publicacion == 1.6
    assert c.cuota_cierre == 1.6
    # La comparación sigue funcionando contra el precio real.
    assert c.cuota_disponible is False
    assert c.clv_pct == round((1.9 / 1.6 - 1.0) * 100.0, 2)


async def test_compare_pick_todo_suspendido(session):
    """Si la curva solo tiene puntos suspendidos, la comparación queda
    honestamente vacía (None), no compara contra cuotas imposibles."""
    pick = await _seed_pick(session)
    session.add(_event())
    session.add(_snap("Full time", "1", 1.0, suspended=True))
    await session.commit()
    c = await compare_pick(session, pick)
    assert c.mapeado is True
    assert c.cuota_publicacion is None
    assert c.cuota_cierre is None
    assert c.cuota_disponible is None
    assert c.clv_pct is None


async def test_compare_pick_cierre_prematch(session):
    """El cierre es el último punto ANTES del inicio del evento, no el
    último punto capturado: un precio live de partido casi decidido
    (51.0) no es "la cuota de cierre" que el tipster podía batir."""
    pick = await _seed_pick(session)
    ev = _event()
    ev.start = datetime(2026, 9, 18, 15, 0)
    session.add(ev)
    base = datetime(2026, 9, 18, 12, 0)
    session.add(_snap("Full time", "1", 1.6, apertura=1.7, captured_at=base))
    session.add(_snap("Full time", "1", 1.4, captured_at=base + timedelta(hours=2)))
    # Punto live con el partido ya decidido (15:59 > 15:00).
    session.add(
        _snap("Full time", "1", 51.0, captured_at=base + timedelta(hours=3, minutes=59))
    )
    await session.commit()
    c = await compare_pick(session, pick)
    assert c.cuota_cierre == 1.4  # último prematch, no el 51.0 live
    assert c.clv_pct == 35.71


async def test_compare_pick_curva_solo_live_sin_cierre(session):
    """Si toda la curva válida es posterior al inicio, no hay cuota de
    cierre prematch real -> None honesto, no un precio live."""
    pick = await _seed_pick(session)
    ev = _event()
    ev.start = datetime(2026, 9, 18, 15, 0)
    session.add(ev)
    session.add(_snap("Full time", "1", 1.3, captured_at=datetime(2026, 9, 18, 16, 0)))
    await session.commit()
    c = await compare_pick(session, pick)
    assert c.mapeado is True
    assert c.cuota_cierre is None
    assert c.clv_pct is None


async def test_compare_pick_cuota_promo(session):
    """Supercuota de afiliado ("CUOTA 5.00 que debería ser 1.22"): el
    precio declarado nunca existió en mercado — se marca `cuota_promo`
    y los agregados la excluyen de inflada y CLV."""
    pick = await _seed_pick(session, cuota=5.0)
    session.add(_event())
    session.add(_snap("Full time", "1", 1.27))
    await session.commit()
    c = await compare_pick(session, pick)
    assert c.mapeado is True
    assert c.cuota_promo is True
    # Los valores se conservan (auditoría) pero no cuentan en agregados.
    assert c.cuota_publicacion == 1.27
    assert c.cuota_disponible is False


async def test_compare_pick_cuota_normal_no_promo(session):
    """Una cuota algo mejor que mercado (1.9 vs 1.6) NO es promo: ratio
    1.19 << 1.8. Solo dispara un precio desproporcionado."""
    pick = await _seed_pick(session, cuota=1.9)
    session.add(_event())
    session.add(_snap("Full time", "1", 1.6))
    await session.commit()
    c = await compare_pick(session, pick)
    assert c.cuota_promo is False


def test_aggregate_odds_stats_excluye_promo():
    """La supercuota no entra ni en auditables/inflada ni en el CLV:
    contarla fingiría que el tipster bate al mercado sistemáticamente."""
    p_normal = _pick("A", cuota=1.9)
    p_normal.id = 1
    p_promo = _pick("B", cuota=5.0)
    p_promo.id = 2

    comparisons = {
        1: PickOddsComparison(mapeado=True, cuota_disponible=False, clv_pct=-8.0),
        2: PickOddsComparison(
            mapeado=True,
            cuota_disponible=False,
            clv_pct=280.0,
            cuota_promo=True,
        ),
    }
    stats = aggregate_odds_stats([p_normal, p_promo], comparisons)

    assert stats.mapeados == 2
    assert stats.cuotas_promo == 1
    # Solo el pick normal alimenta las métricas.
    assert stats.auditables == 1
    assert stats.cuotas_infladas == 1
    assert stats.con_clv == 1
    assert stats.clv_medio == -8.0


async def test_endpoint_odds_pick(client, session, auth_headers):
    pick = await _seed_pick(session)
    session.add(_event())
    session.add(_snap("Full time", "1", 1.6, apertura=1.7))
    await session.commit()
    r = await client.get(
        f"/api/v1/telegram/parsed-picks/{pick.id}/odds",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mapeado"] is True
    assert data["mercado_api"] == "Full time"
    assert data["opcion_api"] == "1"
    assert data["cuota_tipster"] == pick.cuota


async def test_endpoint_odds_pick_no_existe(client, auth_headers):
    r = await client.get(
        "/api/v1/telegram/parsed-picks/9999/odds", headers=auth_headers
    )
    assert r.status_code == 404


# --- compare_picks (lotes) + aggregate_odds_stats ---------------------------


async def test_compare_picks_lote(session):
    """Dos picks del mismo evento se comparan con una sola pasada:
    el mapeado sale y el que no casa queda mapeado=False."""
    pick1 = await _seed_pick(session, "Betis gana")
    pick2 = await _seed_pick(session, "Mérlín marca", mercado="jugador")
    pick3 = await _seed_pick(session, "Getafe gana")
    pick3.odds_event_id = None  # sin evento enlazado
    session.add(_event())
    session.add(_snap("Full time", "1", 1.6, apertura=1.7))
    session.add(_snap("Full time", "2", 5.5, apertura=6.0))
    await session.commit()

    comparisons = await compare_picks(session, [pick1, pick2, pick3])

    assert comparisons[pick1.id].mapeado is True
    assert comparisons[pick1.id].opcion_api == "1"
    assert comparisons[pick2.id].mapeado is False
    assert comparisons[pick3.id].mapeado is False
    assert comparisons[pick3.id].cuota_tipster == pick3.cuota


def test_aggregate_odds_stats():
    """Veredicto del canal: % inflada, CLV medio y % que bate el cierre
    solo sobre los picks donde cada métrica era calculable."""
    p_inflada = _pick("A")  # cuota 1.9 vs mercado 1.6 -> no existía
    p_inflada.id = 1
    p_real = _pick("B", cuota=1.5)
    p_real.id = 2
    p_sin_clv = _pick("C", cuota=None)
    p_sin_clv.id = 3
    p_sin_evento = _pick("D")
    p_sin_evento.id = 4
    p_sin_evento.odds_event_id = None

    def comp(disponible, clv):
        return PickOddsComparison(
            mapeado=True,
            cuota_disponible=disponible,
            clv_pct=clv,
        )

    comparisons = {
        1: comp(disponible=False, clv=10.0),
        2: comp(disponible=True, clv=-5.0),
        3: comp(disponible=None, clv=None),
        4: PickOddsComparison(mapeado=False, cuota_tipster=1.9),
    }
    picks = [p_inflada, p_real, p_sin_clv, p_sin_evento]
    stats = aggregate_odds_stats(picks, comparisons)

    assert stats.picks == 4
    assert stats.con_evento == 3
    assert stats.mapeados == 3
    assert stats.pct_mapeado == 100.0
    assert stats.auditables == 2
    assert stats.cuotas_infladas == 1
    assert stats.pct_cuota_inflada == 50.0
    assert stats.con_clv == 2
    assert stats.clv_medio == 2.5  # (10 + -5) / 2
    assert stats.pct_bate_cierre == 50.0


def test_aggregate_odds_stats_vacio():
    stats = aggregate_odds_stats([], {})
    assert stats.picks == 0
    assert stats.clv_medio is None
    assert stats.pct_cuota_inflada == 0.0


async def test_endpoint_odds_stats_informante(client, session, crear_canal):
    canal = await crear_canal("CanalOdds")
    pick = await _seed_pick(session)
    pick.informante_id = canal.id
    session.add(_event())
    session.add(_snap("Full time", "1", 1.6, apertura=1.7))
    await session.commit()

    r = await client.get(f"/api/v1/informante/{canal.nombre}/odds-stats")
    assert r.status_code == 200
    data = r.json()
    assert data["informante"] == "CanalOdds"
    odds = data["odds"]
    assert odds["picks"] == 1
    assert odds["con_evento"] == 1
    assert odds["mapeados"] == 1
    assert odds["auditables"] == 1
    # Tipster @1.9 > mercado 1.6 al publicar -> cuota inflada.
    assert odds["cuotas_infladas"] == 1
    assert odds["pct_cuota_inflada"] == 100.0
    assert odds["con_clv"] == 1
    assert odds["clv_medio"] is not None


async def test_endpoint_odds_stats_informante_no_existe(client):
    r = await client.get("/api/v1/informante/Fantasma/odds-stats")
    assert r.status_code == 404


async def test_analisis_incluye_odds_por_canal(client, session, crear_canal):
    canal = await crear_canal("CanalAnalisis")
    pick = await _seed_pick(session)
    pick.informante_id = canal.id
    session.add(_event())
    session.add(_snap("Full time", "1", 1.6, apertura=1.7))
    await session.commit()

    data = (await client.get("/api/v1/analisis")).json()
    c = next(x for x in data["canales"] if x["informante"] == "CanalAnalisis")
    assert c["odds"]["mapeados"] == 1
    assert c["odds"]["cuotas_infladas"] == 1
    assert data["totales_odds"]["mapeados"] >= 1
