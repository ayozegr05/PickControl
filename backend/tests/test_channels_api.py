"""Tests del CRUD de canales (`api/v1/channels.py`) y del registry
(`services/telegram/channels.py`).

Telethon se mockean por completo: el "cliente" es un fake con
`iter_dialogs`/`get_entity`, y `refresh_channel_cache` usa la sesión
SQLite del fixture vía `AsyncSessionLocal` sobreescrito.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.v1 import channels as api_channels
from app.models.channel import Channel
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram import channels as channels_service


class _FakeTlChannel:
    """Sustituto del tipo TL `Channel` (broadcast/megagroup)."""

    def __init__(self, id: int, title: str, username: str | None = None):
        self.id = id
        self.title = title
        self.username = username
        self.marked_id = -1000000000 - id


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
    """TelegramClient fake: siempre conectado."""

    def __init__(self, dialogs=None):
        self._dialogs = dialogs or []

    def is_connected(self):
        return True

    def iter_dialogs(self):
        return _AsyncIter(self._dialogs)


@pytest.fixture
def telegram_api_mocks(monkeypatch, session: AsyncSession):
    """Conecta el router de canales con el cliente y la BD de test."""
    client = _FakeClient()
    monkeypatch.setattr(api_channels, "get_telegram_client", lambda: client)
    monkeypatch.setattr(api_channels, "run_catchup_for_target", AsyncMock())
    # El isinstance contra el tipo TL real se sustituye por el fake.
    monkeypatch.setattr(api_channels, "TlChannel", _FakeTlChannel)
    monkeypatch.setattr(
        api_channels.utils,
        "get_peer_id",
        lambda e, add_mark=True: e.marked_id,
    )

    # refresh_channel_cache abre su propia sesión: la redirigimos a la
    # SQLite en memoria del test.
    @asynccontextmanager
    async def _session_cm():
        yield session

    monkeypatch.setattr(channels_service, "AsyncSessionLocal", _session_cm)
    monkeypatch.setattr(
        channels_service.utils,
        "get_peer_id",
        lambda e, add_mark=True: e.marked_id,
    )
    channels_service._active_channel_ids = set()
    channels_service._channel_names = {}
    return client


async def _channel(session, **kwargs) -> Channel:
    ch = Channel(target=kwargs.pop("target", "canal"), **kwargs)
    session.add(ch)
    await session.commit()
    await session.refresh(ch)
    return ch


class TestListarCanales:
    async def test_lista_vacia(self, client, auth_headers):
        resp = await client.get("/api/v1/channels", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_lista_con_canales(self, client, auth_headers, session):
        await _channel(session, target="c1", name="Canal 1", channel_id=-1001)
        await _channel(
            session, target="c2", name="Canal 2", activo=False, channel_id=-1002
        )
        resp = await client.get("/api/v1/channels", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert [c["name"] for c in data] == ["Canal 1", "Canal 2"]
        assert data[1]["activo"] is False


class TestCrearCanal:
    async def test_crea_y_lanza_catchup(self, client, auth_headers, telegram_api_mocks):
        entity = _FakeTlChannel(id=55, title="Nuevo Canal", username="nuevo")
        resolve = AsyncMock(return_value=(entity, entity.marked_id))
        # El endpoint usa su propia referencia importada.
        import app.api.v1.channels as api_mod

        original = api_mod.resolve_channel_target
        api_mod.resolve_channel_target = resolve
        try:
            resp = await client.post(
                "/api/v1/channels",
                json={"target": "https://t.me/nuevo"},
                headers=auth_headers,
            )
        finally:
            api_mod.resolve_channel_target = original

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Nuevo Canal"
        assert data["username"] == "nuevo"
        assert data["channel_id"] == entity.marked_id
        assert data["activo"] is True
        resolve.assert_awaited_once()
        # El catch-up se lanza con asyncio.create_task: el mock se llama
        # pero la corrutina corre aparte.
        api_channels.run_catchup_for_target.assert_called_once()

    async def test_reactiva_si_ya_existe(
        self, client, auth_headers, session, telegram_api_mocks
    ):
        existing = await _channel(
            session, target="viejo", activo=False, channel_id=-1005
        )
        entity = _FakeTlChannel(id=5, title="Viejo", username="viejo")
        # Mismo channel_id marcado -> match por id, no por target.
        entity.marked_id = -1005
        resolve = AsyncMock(return_value=(entity, -1005))

        import app.api.v1.channels as api_mod

        original = api_mod.resolve_channel_target
        api_mod.resolve_channel_target = resolve
        try:
            resp = await client.post(
                "/api/v1/channels",
                json={"target": "@viejo"},
                headers=auth_headers,
            )
        finally:
            api_mod.resolve_channel_target = original

        assert resp.status_code == 201
        assert resp.json()["id"] == existing.id
        assert resp.json()["activo"] is True

    async def test_canal_no_encontrado_404(
        self, client, auth_headers, telegram_api_mocks
    ):
        import app.api.v1.channels as api_mod

        original = api_mod.resolve_channel_target
        api_mod.resolve_channel_target = AsyncMock(return_value=(None, None))
        try:
            resp = await client.post(
                "/api/v1/channels",
                json={"target": "noexiste"},
                headers=auth_headers,
            )
        finally:
            api_mod.resolve_channel_target = original
        assert resp.status_code == 404

    async def test_enlace_privado_400(self, client, auth_headers, telegram_api_mocks):
        resp = await client.post(
            "/api/v1/channels",
            json={"target": "t.me/+abcDEF123"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_listener_desconectado_503(self, client, auth_headers, monkeypatch):
        offline = _FakeClient()
        offline.is_connected = lambda: False
        monkeypatch.setattr(api_channels, "get_telegram_client", lambda: offline)
        resp = await client.post(
            "/api/v1/channels",
            json={"target": "canal"},
            headers=auth_headers,
        )
        assert resp.status_code == 503


class TestActualizarYBorrar:
    async def test_desactivar(self, client, auth_headers, session, telegram_api_mocks):
        ch = await _channel(session, target="c", channel_id=-1007)
        resp = await client.patch(
            f"/api/v1/channels/{ch.id}",
            json={"activo": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["activo"] is False
        # El caché ya no contiene el canal.
        assert -1007 not in channels_service.active_channel_ids()

    async def test_borrar_conserva_historial(
        self, client, auth_headers, session, telegram_api_mocks
    ):
        ch = await _channel(session, target="c", channel_id=-1008)
        raw = TelegramRawMessage(
            channel_id=-1008, message_id=1, channel_name="c", text="t"
        )
        session.add(raw)
        await session.commit()

        resp = await client.delete(f"/api/v1/channels/{ch.id}", headers=auth_headers)
        assert resp.status_code == 204
        assert await session.get(Channel, ch.id) is None
        # El raw sigue en la BD: el borrado no toca la auditoría.
        assert (await session.exec(select(TelegramRawMessage))).first() is not None

    async def test_borrar_inexistente_404(self, client, auth_headers):
        resp = await client.delete("/api/v1/channels/999", headers=auth_headers)
        assert resp.status_code == 404


class TestDisponibles:
    async def test_lista_dialogs_marcando_monitorizados(
        self, client, auth_headers, session, telegram_api_mocks
    ):
        monitored = _FakeTlChannel(id=1, title="Ya", username="ya")
        other = _FakeTlChannel(id=2, title="Nuevo", username="nuevo")
        not_channel = SimpleNamespace(marked_id=999)  # usuario/chat normal
        telegram_api_mocks._dialogs = [
            SimpleNamespace(entity=monitored),
            SimpleNamespace(entity=other),
            SimpleNamespace(entity=not_channel),
        ]
        await _channel(
            session, target="ya", channel_id=monitored.marked_id, activo=True
        )

        resp = await client.get("/api/v1/channels/disponibles", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        by_name = {d["name"]: d for d in data}
        assert by_name["Ya"]["monitorizado"] is True
        assert by_name["Nuevo"]["monitorizado"] is False


class TestRegistry:
    async def test_seed_desde_env(self, session, monkeypatch):
        monkeypatch.setattr(
            channels_service,
            "get_settings",
            lambda: SimpleNamespace(telegram_target_channel="a, b"),
        )

        @asynccontextmanager
        async def _session_cm():
            yield session

        monkeypatch.setattr(channels_service, "AsyncSessionLocal", _session_cm)
        await channels_service.seed_channels_from_env()
        rows = (await session.exec(select(Channel))).all()
        assert {r.target for r in rows} == {"a", "b"}

        # No re-siembra si la tabla ya tiene filas.
        await channels_service.seed_channels_from_env()
        assert len((await session.exec(select(Channel))).all()) == 2

    async def test_refresh_resuelve_y_cachea(self, session, monkeypatch):
        entity = _FakeTlChannel(id=9, title="Resuelto", username="res")

        class _Client(_FakeClient):
            async def get_entity(self, target):
                return entity

        @asynccontextmanager
        async def _session_cm():
            yield session

        monkeypatch.setattr(channels_service, "AsyncSessionLocal", _session_cm)
        monkeypatch.setattr(
            channels_service.utils,
            "get_peer_id",
            lambda e, add_mark=True: e.marked_id,
        )
        session.add(Channel(target="res", activo=True))
        session.add(Channel(target="off", activo=False, channel_id=-1))
        await session.commit()

        ids = await channels_service.refresh_channel_cache(_Client())
        assert ids == {entity.marked_id}
        assert channels_service.channel_name_for(entity.marked_id) == "Resuelto"
