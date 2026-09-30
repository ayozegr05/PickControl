"""Tests del snapshotter en vivo: enriquecimiento cruzado ESPN→Sofascore.

Un evento resuelto vía ESPN solo captura ganador/total/hándicap; si el
mismo partido existe en la familia Sofascore, sus mercados ricos
(córners, tarjetas, BTTS...) se guardan bajo el `event_ext_id` del
pick. Sin HTTP real: los providers son fakes del protocolo
`OddsProvider`.
"""

from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.core.dates import utc_now
from app.models.odds_snapshot import OddsEvent, OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.odds import snapshotter
from app.services.odds.base import EventRef, MarketChoice

pytestmark = pytest.mark.asyncio


class _FakeProvider:
    """Provider mínimo del protocolo: eventos fijos + odds fijas."""

    def __init__(self, name, prefix, sports, event=None, odds=None):
        self.NAME = name
        self.ID_PREFIX = prefix
        self.SUPPORTED_SPORTS = sports
        self._event = event
        self._odds = odds or []
        self.find_calls: list[str] = []
        self.fetch_calls: list[str] = []

    async def find_event(self, sport, date, team_hint):
        self.find_calls.append(team_hint)
        return self._event

    async def fetch_odds(self, event_ext_id, sport):
        self.fetch_calls.append(event_ext_id)
        return self._odds


def _session_cm(session):
    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


async def _pick_linked(session, message_id: int, event_id: str) -> ParsedPick:
    raw = TelegramRawMessage(
        channel_id=1,
        message_id=message_id,
        channel_name="Test",
        text="pick",
        processed=True,
    )
    session.add(raw)
    await session.flush()
    start = utc_now() + timedelta(hours=5)
    session.add(
        OddsEvent(
            event_ext_id=event_id,
            sport="futbol",
            home_team="Ipswich Town",
            away_team="Liverpool",
            start=start,
        )
    )
    pick = ParsedPick(
        raw_message_id=raw.id,
        es_apuesta=True,
        deporte="fútbol",
        evento="Ipswich - Liverpool",
        mercado="ganador",
        seleccion="Liverpool",
        cuota=1.5,
        fecha_evento=start,
        odds_event_id=event_id,
    )
    session.add(pick)
    await session.commit()
    return pick


_ESPN_ODDS = [
    MarketChoice(market_name="Full time", choice_name="1", cuota=10.0),
    MarketChoice(market_name="Full time", choice_name="2", cuota=1.3),
]
_SOFA_ODDS = _ESPN_ODDS + [
    MarketChoice(
        market_name="Corners 2-Way",
        choice_name="Under",
        choice_group="9.5",
        cuota=1.9,
    ),
    MarketChoice(market_name="Both teams to score", choice_name="Yes", cuota=1.7),
]


async def test_evento_espn_se_enriquece_con_sofascore(session, monkeypatch):
    """Un `espn:*` captura sus 3 mercados y, como el partido también
    está en Sofascore, los mercados ricos se añaden bajo el mismo
    evento con provider del espejo que los trajo."""
    espn = _FakeProvider("espn", "espn:", frozenset({"futbol"}), odds=_ESPN_ODDS)
    sofa = _FakeProvider(
        "allsportsapi2",
        "sofascore:",
        frozenset({"futbol"}),
        event=EventRef(
            event_ext_id="sofascore:777",
            home_team="Ipswich Town",
            away_team="Liverpool",
            start=utc_now() + timedelta(hours=5),
        ),
        odds=_SOFA_ODDS,
    )
    await _pick_linked(session, 1, "espn:futbol:eng:123")
    monkeypatch.setattr(snapshotter, "AsyncSessionLocal", _session_cm(session))
    monkeypatch.setattr(
        snapshotter, "_get_odds_providers", AsyncMock(return_value=[espn, sofa])
    )

    stats = await snapshotter.run_odds_snapshot_cycle()

    rows = (
        await session.exec(
            select(OddsSnapshot).where(
                OddsSnapshot.event_ext_id == "espn:futbol:eng:123"
            )
        )
    ).all()
    markets_sofa = {r.market_name for r in rows if r.provider == "allsportsapi2"}
    assert "Corners 2-Way" in markets_sofa
    assert "Both teams to score" in markets_sofa
    assert all(r.event_ext_id == "espn:futbol:eng:123" for r in rows)
    assert stats["filas"] == len(rows) == len(_ESPN_ODDS) + len(_SOFA_ODDS)


async def test_evento_sofascore_no_se_cruza(session, monkeypatch):
    """Un evento ya enlazado a la familia Sofascore no provoca la
    búsqueda cruzada (solo enriquecemos `espn:*`)."""
    espn = _FakeProvider("espn", "espn:", frozenset({"futbol"}))
    sofa = _FakeProvider(
        "allsportsapi2",
        "sofascore:",
        frozenset({"futbol"}),
        odds=_SOFA_ODDS,
    )
    await _pick_linked(session, 2, "sofascore:555")
    monkeypatch.setattr(snapshotter, "AsyncSessionLocal", _session_cm(session))
    monkeypatch.setattr(
        snapshotter, "_get_odds_providers", AsyncMock(return_value=[espn, sofa])
    )

    await snapshotter.run_odds_snapshot_cycle()

    assert sofa.find_calls == []  # nunca se buscó el evento de nuevo
    assert sofa.fetch_calls == ["sofascore:555"]


async def test_cruce_descarta_partido_distinto(session, monkeypatch):
    """Si la familia Sofascore devuelve OTRO partido, no se escribe
    nada (el check de equipos evita odds ajenas bajo el evento)."""
    espn = _FakeProvider("espn", "espn:", frozenset({"futbol"}), odds=_ESPN_ODDS)
    sofa = _FakeProvider(
        "allsportsapi2",
        "sofascore:",
        frozenset({"futbol"}),
        event=EventRef(
            event_ext_id="sofascore:999",
            home_team="Real Madrid",
            away_team="Barcelona",
            start=utc_now() + timedelta(hours=5),
        ),
        odds=_SOFA_ODDS,
    )
    await _pick_linked(session, 3, "espn:futbol:eng:123")
    monkeypatch.setattr(snapshotter, "AsyncSessionLocal", _session_cm(session))
    monkeypatch.setattr(
        snapshotter, "_get_odds_providers", AsyncMock(return_value=[espn, sofa])
    )

    await snapshotter.run_odds_snapshot_cycle()

    rows = (
        await session.exec(
            select(OddsSnapshot).where(
                OddsSnapshot.event_ext_id == "espn:futbol:eng:123"
            )
        )
    ).all()
    assert {r.provider for r in rows} == {"espn"}
    assert sofa.fetch_calls == []  # ni siquiera se pidieron sus odds
