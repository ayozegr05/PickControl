"""Tests del registro de dispositivos (API /devices) y del servicio push.

El servicio abre su propia sesión vía `AsyncSessionLocal` — en tests se
sobreescribe con un context manager que devuelve la SQLite en memoria
del fixture. El HTTP a Expo se simula monkeypatchando `push._send_chunk`
y los hooks se comprueban sustituyendo las funciones `notify_*`.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlmodel import select

from app.core.dates import utc_now
from app.core.security import create_access_token
from app.models.device_token import DeviceToken
from app.models.parsed_pick import ParsedPick
from app.models.pick import Pick
from app.models.user import User, UserRole
from app.services.notifications import push as push_service
from app.services.results import base as results_base
from app.services.results import verifier
from app.services.telegram import processor
from app.services.telegram.pick_extractor import ExtractedPick

pytestmark = pytest.mark.asyncio

TOKEN_A = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
TOKEN_B = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"


def _session_cm(session):
    """`AsyncSessionLocal` falso que devuelve la sesión del test."""

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


async def _user(session) -> User:
    return (await session.exec(select(User))).first()


async def _token_row(session, **kwargs) -> DeviceToken:
    row = DeviceToken(**kwargs)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _pick(session, **kwargs) -> ParsedPick:
    # raw_message_id es NOT NULL; SQLite no fuerza la FK al raw.
    kwargs.setdefault("raw_message_id", 1)
    pick = ParsedPick(es_apuesta=True, **kwargs)
    session.add(pick)
    await session.commit()
    await session.refresh(pick)
    return pick


class TestDevicesApi:
    async def test_registra_token(self, client, auth_headers, session):
        resp = await client.post(
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "android"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["token"] == TOKEN_A
        assert body["platform"] == "android"
        assert body["enabled"] is True

        row = (
            await session.exec(select(DeviceToken).where(DeviceToken.token == TOKEN_A))
        ).first()
        assert row is not None
        assert row.user_id == (await _user(session)).id

    async def test_registro_idempotente(self, client, auth_headers, session):
        for _ in range(2):
            resp = await client.post(
                "/api/v1/devices",
                json={"token": TOKEN_A, "platform": "android"},
                headers=auth_headers,
            )
            assert resp.status_code == 201
        rows = (await session.exec(select(DeviceToken))).all()
        assert len(rows) == 1

    async def test_token_se_reasigna_al_nuevo_usuario(
        self, client, auth_headers, session
    ):
        await client.post(
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "android"},
            headers=auth_headers,
        )
        # Segundo usuario registra el MISMO token (el móvil cambió de cuenta).
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Otro",
                "email": "otro@example.com",
                "password": "password123",
            },
        )
        otro_headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        resp = await client.post(
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "ios"},
            headers=otro_headers,
        )
        assert resp.status_code == 201

        rows = (await session.exec(select(DeviceToken))).all()
        assert len(rows) == 1
        nuevo = (
            await session.exec(select(User).where(User.email == "otro@example.com"))
        ).first()
        assert rows[0].user_id == nuevo.id
        assert rows[0].platform == "ios"

    async def test_token_deshabilitado_se_reactiva(self, client, auth_headers, session):
        user = await _user(session)
        row = await _token_row(session, user_id=user.id, token=TOKEN_A, enabled=False)
        resp = await client.post(
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "android"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        await session.refresh(row)
        assert row.enabled is True

    async def test_delete_desactiva_conservando_fila(
        self, client, auth_headers, session
    ):
        await client.post(
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "android"},
            headers=auth_headers,
        )
        resp = await client.request(
            "DELETE",
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "android"},
            headers=auth_headers,
        )
        assert resp.status_code == 204
        row = (await session.exec(select(DeviceToken))).first()
        assert row.enabled is False  # se conserva como auditoría

    async def test_delete_token_desconocido_es_204(self, client, auth_headers):
        resp = await client.request(
            "DELETE",
            "/api/v1/devices",
            json={"token": TOKEN_B, "platform": "android"},
            headers=auth_headers,
        )
        assert resp.status_code == 204

    async def test_delete_no_toca_tokens_ajenos(self, client, auth_headers, session):
        await client.post(
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "android"},
            headers=auth_headers,
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "name": "Otro",
                "email": "otro@example.com",
                "password": "password123",
            },
        )
        otro_headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        resp = await client.request(
            "DELETE",
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "android"},
            headers=otro_headers,
        )
        assert resp.status_code == 204
        row = (await session.exec(select(DeviceToken))).first()
        assert row.enabled is True  # el token ajeno sigue activo

    async def test_sin_auth_rechazado(self, client):
        resp = await client.post(
            "/api/v1/devices",
            json={"token": TOKEN_A, "platform": "android"},
        )
        assert resp.status_code in (401, 403)

    async def test_token_demasiado_corto_422(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/devices",
            json={"token": "corto", "platform": "android"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestPushService:
    async def test_pick_nuevo_envia_solo_a_habilitados(self, session, monkeypatch):
        await _token_row(session, user_id=1, token=TOKEN_A, enabled=True)
        await _token_row(session, user_id=1, token=TOKEN_B, enabled=False)
        pick = await _pick(
            session,
            seleccion="Real Madrid gana",
            cuota=1.8,
            informante="CanalTest",
        )
        monkeypatch.setattr(push_service, "AsyncSessionLocal", _session_cm(session))
        sent: list[dict] = []

        async def fake_send(messages, access_token):
            sent.extend(messages)
            return [{"status": "ok"} for _ in messages]

        monkeypatch.setattr(push_service, "_send_chunk", fake_send)

        await push_service.notify_new_pick(pick.id)

        assert len(sent) == 1
        assert sent[0]["to"] == TOKEN_A
        assert sent[0]["data"]["type"] == "new_pick"
        assert sent[0]["data"]["pick_id"] == pick.id
        assert "Real Madrid gana" in sent[0]["body"]

    async def test_device_not_registered_deshabilita(self, session, monkeypatch):
        row = await _token_row(session, user_id=1, token=TOKEN_A)
        pick = await _pick(session, seleccion="X gana")
        monkeypatch.setattr(push_service, "AsyncSessionLocal", _session_cm(session))

        async def fake_send(messages, access_token):
            return [
                {
                    "status": "error",
                    "details": {"error": "DeviceNotRegistered"},
                }
                for _ in messages
            ]

        monkeypatch.setattr(push_service, "_send_chunk", fake_send)

        await push_service.notify_new_pick(pick.id)

        await session.refresh(row)
        assert row.enabled is False

    async def test_error_http_no_propaga(self, session, monkeypatch):
        await _token_row(session, user_id=1, token=TOKEN_A)
        pick = await _pick(session, seleccion="X gana")
        monkeypatch.setattr(push_service, "AsyncSessionLocal", _session_cm(session))

        async def fake_send(messages, access_token):
            raise httpx.HTTPError("Expo caído")

        monkeypatch.setattr(push_service, "_send_chunk", fake_send)

        # No debe lanzar: la notificación nunca rompe la ingesta.
        await push_service.notify_new_pick(pick.id)

    async def test_push_desactivado_no_envia(self, session, monkeypatch):
        await _token_row(session, user_id=1, token=TOKEN_A)
        pick = await _pick(session, seleccion="X gana")
        monkeypatch.setattr(push_service, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(push_service, "_enabled", lambda s: False)
        send = AsyncMock()
        monkeypatch.setattr(push_service, "_send_chunk", send)

        await push_service.notify_new_pick(pick.id)
        send.assert_not_called()

    async def test_settled_solo_a_quien_lo_jugo(self, session, monkeypatch):
        # Dos usuarios con token; solo el usuario 1 marcó "yo la jugué".
        user1 = User(name="u1", email="u1@x.com", password_hash="x")
        user2 = User(name="u2", email="u2@x.com", password_hash="x")
        session.add(user1)
        session.add(user2)
        await session.commit()
        await _token_row(session, user_id=user1.id, token=TOKEN_A)
        await _token_row(session, user_id=user2.id, token=TOKEN_B)
        pick = await _pick(
            session,
            seleccion="Tenista gana",
            acierto=True,
            cuota=2.0,
        )
        session.add(
            Pick(
                usuario_id=user1.id,
                informante_id=1,
                parsed_pick_id=pick.id,
                apuesta="Tenista gana",
                tipo_de_apuesta="ganador",
                casa="bet365",
                cantidad_apostada=5.0,
            )
        )
        await session.commit()

        monkeypatch.setattr(push_service, "AsyncSessionLocal", _session_cm(session))
        sent: list[dict] = []

        async def fake_send(messages, access_token):
            sent.extend(messages)
            return [{"status": "ok"} for _ in messages]

        monkeypatch.setattr(push_service, "_send_chunk", fake_send)

        await push_service.notify_settled_picks([pick.id])

        assert len(sent) == 1
        assert sent[0]["to"] == TOKEN_A
        assert sent[0]["data"]["type"] == "settled"
        assert "acertada" in sent[0]["title"]

    async def test_settled_sin_apuesta_de_usuario_no_envia(self, session, monkeypatch):
        await _token_row(session, user_id=1, token=TOKEN_A)
        pick = await _pick(session, seleccion="X gana", acierto=False)
        monkeypatch.setattr(push_service, "AsyncSessionLocal", _session_cm(session))
        send = AsyncMock()
        monkeypatch.setattr(push_service, "_send_chunk", send)

        await push_service.notify_settled_picks([pick.id])
        send.assert_not_called()


class TestRateLimitedPush:
    async def test_solo_reciben_admins(self, session, monkeypatch):
        admin = User(
            name="Admin",
            email="admin@x.com",
            password_hash="x",
            role=UserRole.ADMIN,
        )
        user = User(name="User", email="user@x.com", password_hash="x")
        session.add_all([admin, user])
        await session.commit()
        await _token_row(session, user_id=admin.id, token=TOKEN_A)
        await _token_row(session, user_id=user.id, token=TOKEN_B)

        monkeypatch.setattr(push_service, "AsyncSessionLocal", _session_cm(session))
        sent: list[dict] = []

        async def fake_send(messages, access_token):
            sent.extend(messages)
            return [{"status": "ok"} for _ in messages]

        monkeypatch.setattr(push_service, "_send_chunk", fake_send)

        await push_service.notify_provider_rate_limited("footapi7")

        assert len(sent) == 1
        assert sent[0]["to"] == TOKEN_A
        assert sent[0]["data"]["type"] == "provider_rate_limited"
        assert sent[0]["data"]["provider"] == "footapi7"
        assert "footapi7" in sent[0]["body"]

    async def test_sin_admins_no_envia(self, session, monkeypatch):
        user = User(name="User", email="user@x.com", password_hash="x")
        session.add(user)
        await session.commit()
        await _token_row(session, user_id=user.id, token=TOKEN_A)
        monkeypatch.setattr(push_service, "AsyncSessionLocal", _session_cm(session))
        send = AsyncMock()
        monkeypatch.setattr(push_service, "_send_chunk", send)

        await push_service.notify_provider_rate_limited("footapi7")
        send.assert_not_called()


class TestMarkRateLimited:
    async def test_dedup_mismo_dia_notifica_una_vez(self, monkeypatch, tmp_path):
        monkeypatch.setattr(results_base, "_STATE", None)
        monkeypatch.setattr(
            results_base, "_STATE_FILE", tmp_path / "provider_state.json"
        )
        notify = Mock()
        monkeypatch.setattr(results_base, "_notify_rate_limited", notify)

        results_base.mark_rate_limited("footapi7")
        results_base.mark_rate_limited("footapi7")

        notify.assert_called_once_with("footapi7")
        assert results_base.is_rate_limited("footapi7")

    async def test_otro_provider_si_notifica(self, monkeypatch, tmp_path):
        monkeypatch.setattr(results_base, "_STATE", None)
        monkeypatch.setattr(
            results_base, "_STATE_FILE", tmp_path / "provider_state.json"
        )
        notify = Mock()
        monkeypatch.setattr(results_base, "_notify_rate_limited", notify)

        results_base.mark_rate_limited("footapi7")
        results_base.mark_rate_limited("tennisapi1")

        assert notify.call_count == 2


class TestSystemProvidersApi:
    async def _admin_headers(self, session) -> dict[str, str]:
        admin = User(
            name="Admin",
            email="admin@x.com",
            password_hash="x",
            role=UserRole.ADMIN,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        token = create_access_token(
            {"sub": str(admin.id), "email": admin.email, "role": admin.role}
        )
        return {"Authorization": f"Bearer {token}"}

    async def test_admin_ve_estado_providers(self, client, session, monkeypatch):
        monkeypatch.setattr(
            results_base,
            "_STATE",
            {
                "rate_limited": {"footapi7": "2026-09-21"},
                "missed": {
                    "footapi7|2026-09-20|x - y": "2026-09-21T10:00:00",
                    "odds|allsportsapi2|tenis|2026-09-20|a vs b": "2026-09-21T10:00:00",
                },
            },
        )
        headers = await self._admin_headers(session)
        resp = await client.get("/api/v1/system/providers", headers=headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["rate_limited"] == {"footapi7": "2026-09-21"}
        assert body["missed_by_provider"] == {"footapi7": 1, "allsportsapi2": 1}

    async def test_usuario_normal_403(self, client, auth_headers):
        resp = await client.get("/api/v1/system/providers", headers=auth_headers)
        assert resp.status_code == 403

    async def test_sin_auth_rechazado(self, client):
        resp = await client.get("/api/v1/system/providers")
        assert resp.status_code in (401, 403)


class TestNewPickHook:
    async def test_pick_nuevo_dispara_push(self, session, monkeypatch):
        monkeypatch.setattr(
            processor,
            "get_settings",
            lambda: SimpleNamespace(openai_api_key="test-key"),
        )
        monkeypatch.setattr(
            processor,
            "extract_pick",
            AsyncMock(
                return_value=ExtractedPick(
                    es_apuesta=True,
                    seleccion="Real Madrid gana",
                    cuota=2.5,
                    metodo="rule",
                )
            ),
        )
        notified: list[int] = []

        async def fake_notify(pick_id):
            notified.append(pick_id)

        monkeypatch.setattr(processor, "notify_new_pick", fake_notify)

        await processor.process_incoming_message(
            session=session,
            channel="Test Channel",
            channel_id=123,
            message_id=1,
            text="Real Madrid gana cuota 2.5",
        )
        await asyncio.sleep(0)  # deja correr la create_task

        parsed = (await session.exec(select(ParsedPick))).one()
        assert notified == [parsed.id]

    async def test_no_pick_no_notifica(self, session, monkeypatch):
        monkeypatch.setattr(
            processor,
            "get_settings",
            lambda: SimpleNamespace(openai_api_key="test-key"),
        )
        monkeypatch.setattr(
            processor,
            "extract_pick",
            AsyncMock(return_value=ExtractedPick(es_apuesta=False)),
        )
        notify = AsyncMock()
        monkeypatch.setattr(processor, "notify_new_pick", notify)

        await processor.process_incoming_message(
            session=session,
            channel="Test Channel",
            channel_id=123,
            message_id=2,
            text="promo sin apuesta",
        )
        await asyncio.sleep(0)
        notify.assert_not_called()


class TestSettlementHook:
    async def test_verify_pending_notifica_liquidados(self, session, monkeypatch):
        pick = await _pick(
            session,
            seleccion="Real Madrid gana",
            deporte="futbol",
            fecha_evento=utc_now() - timedelta(days=1),
        )
        monkeypatch.setattr(verifier, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            verifier, "_get_providers", AsyncMock(return_value=[object()])
        )
        monkeypatch.setattr(
            verifier, "verify_pick", AsyncMock(return_value=(True, False))
        )
        notify = AsyncMock()
        monkeypatch.setattr(verifier, "notify_settled_picks", notify)

        verified = await verifier.verify_pending_picks()

        assert verified == 1
        notify.assert_awaited_once_with([pick.id])
        await session.refresh(pick)
        assert pick.acierto is True
        assert pick.verificado_por == "auto"
