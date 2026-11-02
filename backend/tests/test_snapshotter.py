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


class _SportAwareProvider(_FakeProvider):
    """Igual que _FakeProvider pero solo responde en el deporte dado."""

    def __init__(self, name, prefix, sports, events_by_sport=None, odds=None):
        super().__init__(name, prefix, sports, odds=odds)
        self._events_by_sport = events_by_sport or {}
        self.tried_sports: list[str] = []

    async def find_event(self, sport, date, team_hint):
        self.find_calls.append(team_hint)
        self.tried_sports.append(sport)
        return self._events_by_sport.get(sport)


async def _pick_unlinked(
    session, message_id, deporte="fútbol", evento="París - Valencia"
):
    raw = TelegramRawMessage(
        channel_id=1,
        message_id=message_id,
        channel_name="Test",
        text="pick",
        processed=True,
    )
    session.add(raw)
    await session.flush()
    pick = ParsedPick(
        raw_message_id=raw.id,
        es_apuesta=True,
        deporte=deporte,
        evento=evento,
        mercado="ganador",
        seleccion="Valencia gana",
        cuota=1.5,
        fecha_evento=utc_now() + timedelta(hours=5),
        odds_event_id=None,
    )
    session.add(pick)
    await session.commit()
    return pick


async def test_cross_sport_fallback_reclasifica(session):
    """El pick dice 'fútbol' pero el evento solo casa en basket:
    se enlaza, se corrige `deporte` y el OddsEvent nace con el
    deporte real."""
    pick = await _pick_unlinked(session, 10)
    basket_ref = EventRef(
        event_ext_id="flashscore:A7ObYlS9",
        home_team="Paris",
        away_team="Valencia",
        start=utc_now() + timedelta(hours=5),
    )
    providers = [
        _SportAwareProvider("espn", "espn:", frozenset({"futbol"})),
        _SportAwareProvider(
            "apihubflashscore",
            "flashscore:",
            frozenset({"baloncesto"}),
            events_by_sport={"baloncesto": basket_ref},
        ),
    ]

    resolved = await snapshotter._resolve_missing_event_ids(session, [pick], providers)

    assert resolved == 1
    assert pick.odds_event_id == "flashscore:A7ObYlS9"
    assert pick.deporte == "baloncesto"
    ev = (
        await session.exec(
            select(OddsEvent).where(OddsEvent.event_ext_id == "flashscore:A7ObYlS9")
        )
    ).first()
    assert ev is not None and ev.sport == "baloncesto"


async def test_cross_sport_no_toca_si_casa_en_su_deporte(session):
    """Si el deporte declarado casa, no se prueba nada más ni se
    toca `deporte`."""
    pick = await _pick_unlinked(session, 11, evento="Ipswich - Liverpool")
    ref = EventRef(
        event_ext_id="espn:soccer:eng:1",
        home_team="Ipswich Town",
        away_team="Liverpool",
        start=utc_now() + timedelta(hours=5),
    )
    espn = _SportAwareProvider(
        "espn", "espn:", frozenset({"futbol"}), events_by_sport={"futbol": ref}
    )
    basket = _SportAwareProvider(
        "apihubflashscore", "flashscore:", frozenset({"baloncesto"})
    )

    resolved = await snapshotter._resolve_missing_event_ids(
        session, [pick], [espn, basket]
    )

    assert resolved == 1
    assert pick.deporte == "fútbol"
    assert basket.tried_sports == []  # nunca se probó el board de basket


async def test_pulsescore_resuelve_como_ultima_red(session, monkeypatch):
    """Si toda la cadena falla, PulseScore resuelve el pick y el
    evento queda bajo su propio espacio de ids."""
    monkeypatch.setattr(snapshotter, "is_rate_limited", lambda name: False)
    monkeypatch.setattr(snapshotter, "is_missed", lambda key, ttl: False)
    monkeypatch.setattr(snapshotter, "mark_missed", lambda key: None)
    pick = await _pick_unlinked(session, 12, evento="Obscuro FC - Nadie FC")
    pulse = _SportAwareProvider(
        "pulsescore",
        "pulsescore:",
        frozenset({"futbol", "baloncesto", "tenis"}),
        events_by_sport={
            "futbol": EventRef(
                event_ext_id="pulsescore:555",
                home_team="Obscuro FC",
                away_team="Nadie FC",
                start=utc_now() + timedelta(hours=5),
            )
        },
    )
    providers = [_SportAwareProvider("espn", "espn:", frozenset({"futbol"}))]

    resolved = await snapshotter._resolve_missing_event_ids(
        session, [pick], providers, pulse
    )

    assert resolved == 1
    assert pick.odds_event_id == "pulsescore:555"
    # Solo probó el deporte declarado: casó en el primero.
    assert pulse.tried_sports == ["futbol"]


async def test_pulsescore_no_se_consulta_lejos(session, monkeypatch):
    """Eventos a >3 días: el board 'soonest first' no los lista —
    ni se gasta la página."""
    monkeypatch.setattr(snapshotter, "is_rate_limited", lambda name: False)
    pick = await _pick_unlinked(session, 13)
    pick.fecha_evento = utc_now() + timedelta(days=10)
    await session.commit()
    pulse = _SportAwareProvider("pulsescore", "pulsescore:", frozenset({"futbol"}))
    providers = [_SportAwareProvider("espn", "espn:", frozenset({"futbol"}))]

    resolved = await snapshotter._resolve_missing_event_ids(
        session, [pick], providers, pulse
    )

    assert resolved == 0
    assert pulse.find_calls == []  # ni una página gastada


async def test_capture_missed_expira_en_ventana_cierre(monkeypatch):
    """La marca nomatch se respeta lejos del inicio y se ignora en
    las ~3 h previas (última oportunidad antes del pitido)."""
    monkeypatch.setattr(snapshotter, "is_missed", lambda key, ttl: True)
    lejos = utc_now() + timedelta(hours=10)
    cerca = utc_now() + timedelta(hours=1)
    assert snapshotter._capture_missed("k", timedelta(hours=12), lejos) is True
    assert snapshotter._capture_missed("k", timedelta(hours=12), cerca) is False
    assert snapshotter._capture_missed("k", timedelta(hours=12), None) is True
