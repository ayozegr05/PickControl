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
    assert map_pick_choices(pick, _event(), _futbol_snaps()) is None


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
