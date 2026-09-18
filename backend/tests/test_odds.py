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
from app.services.odds.compare import compare_pick, map_pick_choices

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
) -> OddsSnapshot:
    return OddsSnapshot(
        provider="allsportsapi2",
        event_ext_id=EVENT,
        market_name=market,
        choice_name=choice,
        choice_group=group,
        cuota=cuota,
        cuota_apertura=apertura,
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


def test_map_empate_no_valido():
    pick = _pick("Betis", mercado="resultado sin empate")
    m = map_pick_choices(pick, _event(), _futbol_snaps())
    assert m is not None
    assert (m.market_name, m.choice_name) == ("Draw no bet", "1")


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
