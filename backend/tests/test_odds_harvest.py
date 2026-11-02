"""Tests de `odds/harvest.py` — cosecha manual vía userscript.

Cubre la extracción del match_id de la URL, la traducción de
mercados/selecciones del comparador al vocabulario interno y la
ingesta end-to-end: matching de picks pendientes, escritura de
snapshots con provider "oddsportal" e idempotencia ante re-cosecha.
"""

from datetime import datetime

import pytest
from sqlmodel import select

from app.models.odds_snapshot import OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.schemas.odds_harvest import HarvestEventPayload, HarvestOddsRow
from app.services.odds.harvest import (
    _comparator_supported,
    event_ext_id,
    ingest_harvest,
    translate_market,
    translate_row,
)

pytestmark = pytest.mark.asyncio


def _row(**kw) -> HarvestOddsRow:
    base = {"market": "1X2", "selection": "1", "closing": 2.0}
    return HarvestOddsRow(**{**base, **kw})


# ------------------------------------------------------------ helpers


def test_ext_id_desde_url():
    url = "https://www.cuotasahora.com/football/england/premier-league/ipswich-liverpool-Ab3Cd4Ef/"
    assert event_ext_id(url, "Ipswich", "Liverpool", None) == "oddsportal:Ab3Cd4Ef"


def test_ext_id_fallback_estable():
    a = event_ext_id("https://ejemplo.com/x", "A", "B", datetime(2026, 9, 4))
    b = event_ext_id("https://ejemplo.com/x", "A", "B", datetime(2026, 9, 4))
    assert a == b and a.startswith("oddsportal:x")


def test_translate_market_vocabulario():
    assert translate_market("1X2", "futbol") == "Full time"
    assert translate_market("Over/Under", "futbol") == "Match goals"
    assert translate_market("Over/Under", "tenis") == "Total games won"
    assert translate_market("Over/Under", "baloncesto") == "Total points"
    assert translate_market("Corners O/U", "futbol") == "Corners 2-Way"
    assert translate_market("Asian Handicap", "futbol") == "Asian handicap"
    assert translate_market("Player props", "futbol") is None


def test_translate_row_over_under_espanol():
    t = translate_row(
        _row(market="Over/Under", selection="Más", line=2.5, closing=1.9),
        "futbol",
        "A",
        "B",
    )
    assert t is not None
    assert (t.market_name, t.choice_name, t.choice_group) == (
        "Match goals",
        "Over",
        "2.5",
    )


def test_translate_row_handicap_reescribe_equipo():
    t = translate_row(
        _row(market="Asian Handicap", selection="1", line=1.5, closing=2.1),
        "futbol",
        "Ipswich",
        "Liverpool",
    )
    assert t is not None
    assert t.market_name == "Asian handicap"
    assert t.choice_group == "-1.5"  # el local lleva el signo negativo
    assert t.choice_name == "Ipswich"


def test_translate_row_rechaza_cuota_invalida():
    assert translate_row(_row(closing=1.0), "futbol", "A", "B") is None
    assert translate_row(_row(closing=None), "futbol", "A", "B") is None


# ------------------------------------------------------------- ingesta


def _pick(pid: int, evento: str = "Ipswich - Liverpool") -> ParsedPick:
    return ParsedPick(
        id=pid,
        raw_message_id=1,
        es_apuesta=True,
        deporte="fútbol",
        evento=evento,
        mercado="ganador",
        seleccion="Liverpool gana",
        fecha_evento=datetime(2026, 9, 4, 20, 0),
    )


def _payload(**kw) -> HarvestEventPayload:
    base = {
        "url": "https://www.cuotasahora.com/football/x/ipswich-liverpool-Ab3Cd4Ef/",
        "sport": "futbol",
        "home": "Ipswich",
        "away": "Liverpool",
        "start": datetime(2026, 9, 4, 21, 0),
        "rows": [
            HarvestOddsRow(
                market="1X2", selection="1", closing=5.5, bookmaker="bet365"
            ),
            HarvestOddsRow(
                market="1X2", selection="X", closing=4.2, bookmaker="bet365"
            ),
            HarvestOddsRow(
                market="1X2", selection="2", closing=1.5, bookmaker="bet365"
            ),
        ],
    }
    base.update(kw)
    return HarvestEventPayload(**base)


async def _seed(session, *picks: ParsedPick) -> None:
    """Raw padre + picks (FK obligatoria en sqlite)."""
    session.add(
        TelegramRawMessage(channel_id=1, message_id=1, channel_name="t", text="x")
    )
    for p in picks:
        session.add(p)
    await session.commit()


async def test_ingesta_casa_pick_y_escribe_filas(session):
    await _seed(session, _pick(1))

    res = await ingest_harvest(session, _payload())

    assert res.accepted and res.matched_picks == [1]
    assert res.event_ext_id == "oddsportal:Ab3Cd4Ef"
    rows = (
        await session.exec(
            select(OddsSnapshot).where(OddsSnapshot.event_ext_id == res.event_ext_id)
        )
    ).all()
    assert any(r.provider == "oddsportal" for r in rows)
    # La opción casada ("Full time"/"2") lleva parsed_pick_id.
    attributed = [r for r in rows if r.parsed_pick_id == 1]
    assert len(attributed) == 1
    assert attributed[0].choice_name == "2"
    assert attributed[0].cuota == 1.5


async def test_ingesta_idempotente(session):
    await _seed(session, _pick(1))
    first = await ingest_harvest(session, _payload())
    n_first = len(
        (
            await session.exec(
                select(OddsSnapshot).where(
                    OddsSnapshot.event_ext_id == first.event_ext_id
                )
            )
        ).all()
    )

    second = await ingest_harvest(session, _payload())
    n_second = len(
        (
            await session.exec(
                select(OddsSnapshot).where(
                    OddsSnapshot.event_ext_id == second.event_ext_id
                )
            )
        ).all()
    )
    assert n_second == n_first  # la re-cosecha no duplica opciones


async def test_ingesta_evento_sin_picks_rechazado(session):
    await _seed(session, _pick(1, evento="Barcelona - Madrid"))
    res = await ingest_harvest(session, _payload())
    assert not res.accepted


async def test_ingesta_sin_filas_rechazado(session):
    await _seed(session, _pick(1))
    res = await ingest_harvest(session, _payload(rows=[]))
    assert not res.accepted


async def test_pick_prop_queda_unmatched(session):
    prop = _pick(1)
    prop.mercado = "jugador"
    prop.seleccion = "Salah marca gol"
    await _seed(session, prop)
    res = await ingest_harvest(session, _payload())
    # El evento sí casa (equipos+fecha) pero el prop no tiene mercado
    # en la página -> unmatched honesto, sin fila atribuida.
    assert res.accepted and res.unmatched_picks == [1]


# ---------------------------------------------------------- filtro cola


def test_comparator_supported_mercados_de_la_web():
    p = _pick(1)
    for mercado, seleccion in [
        ("ganador", "Liverpool gana"),
        ("over/under", "Más de 2.5 goles"),
        ("doble oportunidad", "1X"),
        ("hándicap asiático", "Liverpool -0.5"),
        ("resultado sin empate", "Liverpool"),
        ("ganador 1ª mitad", "Liverpool"),
    ]:
        p.mercado, p.seleccion = mercado, seleccion
        assert _comparator_supported(p), (mercado, seleccion)


def test_comparator_supported_descarta_lo_que_la_web_no_tiene():
    p = _pick(1)
    for mercado, seleccion in [
        ("over/under", "Menos de 12 córners"),
        ("ganador", "9,5 córners en total"),  # prop disfrazada de ganador
        ("ganador", "Recibe amonestación Enciso"),
        ("ganador", "Anota gol en cualquier momento Isak"),
        ("jugador", "Salah marca gol"),
        ("paradas del portero", "Diogo Costa 4+ paradas"),
        ("tarjetas", "Más de 3.5 tarjetas"),
        ("portería a 0", "Madrid portería a cero"),
        ("over/under juegos", "Más de 7.5 juegos en el 1º set"),
    ]:
        p.mercado, p.seleccion = mercado, seleccion
        assert not _comparator_supported(p), (mercado, seleccion)


def test_comparator_supported_padre_combinada_no_es_mercado():
    p = _pick(1)
    p.mercado, p.seleccion = "combinada", "4 selecciones"
    assert not _comparator_supported(p)
    p.mercado = "ganador"
    p.es_combinada = True
    assert not _comparator_supported(p)
