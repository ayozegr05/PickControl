"""Tests de `catchup.py`: marca de agua, detección de huecos, reparación
de raws incompletos y aislamiento de errores por canal.

El cliente de Telethon y el procesador se mockean; la BD es la SQLite en
memoria del fixture `session` (se sobreescribe `AsyncSessionLocal`).
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telethon.tl.types import Message, PeerChannel

from app.models.channel import Channel
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram import catchup
from app.services.telegram import channels as channels_service

CHANNEL_ID = -100100


class _AsyncIter:
    def __init__(self, items):
        self._it = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeClient:
    """Sustituto de TelegramClient para el catch-up."""

    def __init__(self, entity, messages):
        # `messages` se dan de más nuevo a más viejo, como en Telegram.
        self._entity = entity
        self._messages = messages

    async def get_entity(self, target):
        return self._entity

    def iter_messages(self, entity, limit=None, min_id=None):
        msgs = self._messages
        if min_id is not None:
            msgs = [m for m in msgs if m.id > min_id]
        if limit is not None:
            msgs = msgs[:limit]
        return _AsyncIter(list(msgs))

    async def get_messages(self, entity, limit=None):
        return self._messages[:limit] if limit else self._messages

    def iter_dialogs(self):
        return _AsyncIter([])


def _msg(id: int, age_days: float = 0, action=None) -> Message:
    m = Message(
        id=id,
        peer_id=PeerChannel(100),
        date=datetime.now(timezone.utc) - timedelta(days=age_days),
        message="texto",
    )
    m.action = action
    return m


@pytest.fixture
def telegram_mocks(monkeypatch, session: AsyncSession):
    """Corta el acceso a BD real y a Telethon; devuelve el espía del
    procesador para inspeccionar qué mensajes se procesaron."""

    @asynccontextmanager
    async def _session_cm():
        yield session

    monkeypatch.setattr(catchup, "AsyncSessionLocal", _session_cm)
    proc = AsyncMock()
    monkeypatch.setattr(catchup, "process_incoming_message", proc)
    monkeypatch.setattr(
        catchup, "fetch_message_content", AsyncMock(return_value=("t", None, None))
    )
    # utils.get_peer_id espera tipos TL reales; el fake usa `marked_id`.
    # La resolución vive en `channels.py` (resolve_channel_target).
    monkeypatch.setattr(
        channels_service.utils,
        "get_peer_id",
        lambda e, add_mark=True: e.marked_id,
    )
    return proc


def _client(messages) -> _FakeClient:
    entity = SimpleNamespace(title="Canal", marked_id=CHANNEL_ID)
    return _FakeClient(entity, messages)


async def _raw(session, message_id, **kwargs) -> TelegramRawMessage:
    # processed=True por defecto: un raw sano ya procesado. Los tests de
    # reparación pasan `processed=False`/`text=""` explícitamente.
    raw = TelegramRawMessage(
        channel_id=CHANNEL_ID,
        message_id=message_id,
        channel_name="Canal",
        text=kwargs.pop("text", "t"),
        processed=kwargs.pop("processed", True),
        **kwargs,
    )
    session.add(raw)
    await session.commit()
    await session.refresh(raw)
    return raw


def _processed_ids(proc: AsyncMock) -> set[int]:
    return {c.kwargs["message_id"] for c in proc.await_args_list}


class TestCatchup:
    async def test_canal_nuevo_se_siembra(self, telegram_mocks, session):
        """Sin historial en BD: se siembran los últimos mensajes."""
        client = _client([_msg(3), _msg(2), _msg(1)])
        await catchup._catchup_channel(client, "canal")
        assert _processed_ids(telegram_mocks) == {1, 2, 3}

    async def test_marca_de_agua_procesa_solo_nuevos(self, telegram_mocks, session):
        for mid in (1, 2, 3):
            await _raw(session, mid)
        client = _client([_msg(5), _msg(4), _msg(3), _msg(2), _msg(1)])
        await catchup._catchup_channel(client, "canal")
        assert _processed_ids(telegram_mocks) == {4, 5}

    async def test_hueco_por_debajo_de_la_marca(self, telegram_mocks, session):
        """El escaneo de huecos recupera ids que faltan aunque estén por
        debajo del último guardado (downtime + mensajes en vivo después)."""
        for mid in (1, 2, 5):
            await _raw(session, mid)
        client = _client([_msg(6), _msg(5), _msg(4), _msg(3), _msg(2), _msg(1)])
        await catchup._catchup_channel(client, "canal")
        assert _processed_ids(telegram_mocks) == {3, 4, 6}

    async def test_raw_vacio_se_reprocesa(self, telegram_mocks, session):
        """Un raw guardado sin contenido se borra y se reintenta."""
        await _raw(session, 1)
        await _raw(session, 2, text="")
        client = _client([_msg(2), _msg(1)])
        await catchup._catchup_channel(client, "canal")

        assert _processed_ids(telegram_mocks) == {2}
        # El raw viejo fue eliminado (el procesador mockeado no crea otro).
        rows = (
            await session.exec(
                select(TelegramRawMessage).where(TelegramRawMessage.message_id == 2)
            )
        ).all()
        assert rows == []

    async def test_raw_sin_procesar_se_reintenta(self, telegram_mocks, session):
        """`processed=False` (p. ej. un 429 de OpenAI en la extracción)
        marca el raw como reintentable aunque tenga texto."""
        await _raw(session, 1)
        await _raw(session, 2, processed=False)
        client = _client([_msg(2), _msg(1)])
        await catchup._catchup_channel(client, "canal")
        assert _processed_ids(telegram_mocks) == {2}

    async def test_raw_incompleto_con_pick_no_se_toca(self, telegram_mocks, session):
        """Raw vacío pero con ParsedPick vinculado: ya está representado,
        no se borra ni se reprocesa."""
        raw = await _raw(session, 2, text="")
        session.add(ParsedPick(raw_message_id=raw.id, es_apuesta=True))
        await _raw(session, 1)
        await session.commit()

        client = _client([_msg(2), _msg(1)])
        await catchup._catchup_channel(client, "canal")

        assert _processed_ids(telegram_mocks) == set()
        # El raw sigue en BD.
        assert (
            await session.exec(
                select(TelegramRawMessage).where(TelegramRawMessage.message_id == 2)
            )
        ).first() is not None

    async def test_corte_por_antiguedad(self, telegram_mocks, session):
        """Mensajes de más de 7 días no se recuperan."""
        client = _client([_msg(2), _msg(1, age_days=8)])
        await catchup._catchup_channel(client, "canal")
        assert _processed_ids(telegram_mocks) == {2}

    async def test_mensajes_de_servicio_se_ignoran(self, telegram_mocks, session):
        client = _client([_msg(2, action=object()), _msg(1)])
        await catchup._catchup_channel(client, "canal")
        assert _processed_ids(telegram_mocks) == {1}

    async def test_error_en_un_mensaje_no_aborta(
        self, telegram_mocks, session, monkeypatch
    ):
        proc = telegram_mocks
        proc.side_effect = [Exception("boom"), None]
        client = _client([_msg(2), _msg(1)])
        await catchup._catchup_channel(client, "canal")
        # El mensaje 2 falló pero el 1 se procesó igualmente.
        assert proc.await_count == 2

    async def test_canal_no_resuelto_no_falla(self, telegram_mocks):
        """Un id numérico que no está entre los diálogos se omite."""
        client = _client([_msg(1)])
        client._entity = None
        await catchup._catchup_channel(client, "12345")
        telegram_mocks.assert_not_awaited()

    async def test_run_catchup_aisla_errores_por_canal(
        self, telegram_mocks, session, monkeypatch
    ):
        """Un canal que explota no impide procesar el siguiente."""
        session.add(Channel(target="malo", activo=True))
        session.add(Channel(target="bueno", activo=True))
        session.add(Channel(target="inactivo", activo=False))
        await session.commit()

        calls = []

        async def fake_channel(client, target):
            calls.append(target)
            if target == "malo":
                raise RuntimeError("canal roto")

        monkeypatch.setattr(catchup, "_catchup_channel", fake_channel)
        await catchup.run_catchup(SimpleNamespace())
        # Solo los activos; "malo" lanza pero "bueno" se procesa igual.
        assert calls == ["malo", "bueno"]
