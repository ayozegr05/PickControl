"""Tests del rescate de `evento` (app/services/odds/event_rescue.py).

Cascada: reglas sobre texto guardado -> mini-LLM -> re-OCR dirigido
(propia imagen o la del par foto-boleto) -> nada.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.dates import utc_now
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.odds import event_rescue

pytestmark = pytest.mark.asyncio


def _settings(openai: str | None = "k") -> SimpleNamespace:
    return SimpleNamespace(openai_api_key=openai)


_RAW_SEQ = 0


async def _raw(session, **kwargs) -> TelegramRawMessage:
    global _RAW_SEQ
    _RAW_SEQ += 1
    kwargs.setdefault("channel_id", 1)
    kwargs.setdefault("message_id", _RAW_SEQ)
    kwargs.setdefault("channel_name", "Test")
    kwargs.setdefault("text", "")
    kwargs.setdefault("processed", True)
    raw = TelegramRawMessage(**kwargs)
    session.add(raw)
    await session.flush()
    return raw


async def _pick(session, raw: TelegramRawMessage, **kwargs) -> ParsedPick:
    kwargs.setdefault("raw_message_id", raw.id)
    kwargs.setdefault("es_apuesta", True)
    kwargs.setdefault("deporte", "fútbol")
    kwargs.setdefault("evento", "9,5 córners en total")
    kwargs.setdefault("fecha_evento", utc_now() - timedelta(days=1))
    pick = ParsedPick(**kwargs)
    session.add(pick)
    await session.flush()
    return pick


class TestReglasTexto:
    async def test_evento_en_texto_del_raw(self, session):
        raw = await _raw(
            session, text="DM7 FREE\nIpswich vs Liverpool\nMenos de 9,5 córners @1.91"
        )
        pick = await _pick(session, raw)
        found = await event_rescue.rescue_evento(session, pick, _settings())
        assert found is not None
        assert "Ipswich" in found and "Liverpool" in found

    async def test_evento_en_ocr_guardado(self, session):
        raw = await _raw(
            session,
            text="pick de hoy",
            extracted_text="BOLETO\nComo v Parma\nTotal corners 9.5",
        )
        pick = await _pick(session, raw)
        found = await event_rescue.rescue_evento(session, pick, _settings(None))
        assert found is not None and "Como" in found and "Parma" in found

    async def test_evento_en_el_raw_del_par(self, session):
        """La imagen/OCR del cruce vive en el mensaje vecino (par
        foto-boleto a ±10 min): el rescate también lo mira."""
        pair = await _raw(
            session,
            message_id=101,
            text="",
            extracted_text="SLIP\nIpswich v Liverpool\nCorners 9.5",
            media_path="media/x.jpg",
        )
        raw = await _raw(session, message_id=100, text="menos de 9,5 córners")
        raw.received_at = pair.received_at  # misma ventana del par
        pick = await _pick(session, raw)
        found = await event_rescue.rescue_evento(session, pick, _settings(None))
        assert found is not None and "Ipswich" in found


class TestCascada:
    async def test_llm_solo_si_reglas_fallan(self, session, monkeypatch):
        raw = await _raw(session, text="el pick de hoy, sin cruce legible")
        pick = await _pick(session, raw)
        llm = AsyncMock(return_value="Sevilla vs Betis")
        monkeypatch.setattr(event_rescue, "_evento_via_llm", llm)
        found = await event_rescue.rescue_evento(session, pick, _settings())
        assert found == "Sevilla vs Betis"
        llm.assert_awaited_once()

    async def test_llm_no_se_llama_si_reglas_bastan(self, session, monkeypatch):
        raw = await _raw(session, text="Osasuna vs Getafe @2.10")
        pick = await _pick(session, raw)
        llm = AsyncMock(return_value=None)
        monkeypatch.setattr(event_rescue, "_evento_via_llm", llm)
        found = await event_rescue.rescue_evento(session, pick, _settings())
        assert found is not None
        llm.assert_not_awaited()

    async def test_imagen_solo_si_llm_falla(self, session, monkeypatch):
        raw = await _raw(session, text="sin cruce", media_path="media/slip.jpg")
        pick = await _pick(session, raw)
        monkeypatch.setattr(
            event_rescue, "_evento_via_llm", AsyncMock(return_value=None)
        )
        imagen = AsyncMock(return_value="Como - Parma")
        monkeypatch.setattr(event_rescue, "_evento_via_imagen", imagen)
        found = await event_rescue.rescue_evento(session, pick, _settings())
        assert found == "Como - Parma"
        imagen.assert_awaited_once()

    async def test_nada_en_ningun_sitio_devuelve_none(self, session, monkeypatch):
        raw = await _raw(session, text="🔥 pick de hoy, confiad en mi")
        pick = await _pick(session, raw)
        monkeypatch.setattr(
            event_rescue, "_evento_via_llm", AsyncMock(return_value=None)
        )
        found = await event_rescue.rescue_evento(session, pick, _settings())
        assert found is None
