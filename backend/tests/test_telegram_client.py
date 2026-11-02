"""Tests de `client.py`: singleton del cliente Telethon y validación de
credenciales."""

from types import SimpleNamespace

import pytest

from app.services.telegram import client as client_module


@pytest.fixture(autouse=True)
def reset_singleton():
    """El cliente es un singleton global: cada test parte de cero."""
    client_module.reset_telegram_client()
    yield
    client_module.reset_telegram_client()


def _settings(**overrides):
    base = {
        "telegram_api_id": "12345",
        "telegram_api_hash": "hash",
        "telegram_session_name": "sesion_test",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestGetTelegramClient:
    def test_sin_credenciales_lanza_runtime_error(self, monkeypatch):
        monkeypatch.setattr(
            client_module,
            "get_settings",
            lambda: _settings(telegram_api_id=None),
        )
        with pytest.raises(RuntimeError, match="TELEGRAM_API_ID"):
            client_module.get_telegram_client()

        monkeypatch.setattr(
            client_module,
            "get_settings",
            lambda: _settings(telegram_api_hash=None),
        )
        with pytest.raises(RuntimeError):
            client_module.get_telegram_client()

    def test_singleton_y_argumentos(self, monkeypatch):
        created = []

        class FakeClient:
            def __init__(self, session_name, api_id, api_hash):
                self.args = (session_name, api_id, api_hash)
                created.append(self)

        monkeypatch.setattr(client_module, "get_settings", lambda: _settings())
        monkeypatch.setattr(client_module, "TelegramClient", FakeClient)

        first = client_module.get_telegram_client()
        second = client_module.get_telegram_client()

        assert first is second
        assert len(created) == 1
        # api_id llega como int aunque el settings lo guarde como str.
        assert first.args == ("sesion_test", 12345, "hash")

    def test_reset_descarta_la_instancia(self, monkeypatch):
        monkeypatch.setattr(client_module, "get_settings", lambda: _settings())
        monkeypatch.setattr(client_module, "TelegramClient", lambda *a: object())
        first = client_module.get_telegram_client()
        client_module.reset_telegram_client()
        assert client_module.get_telegram_client() is not first
