"""Tests del procesador de mensajes de Telegram.

La primera parte cubre las heurísticas de deduplicación (funciones puras).
La segunda parte son tests de integración contra la base de datos en
memoria: comprobamos que, aunque el extractor falle, el mensaje crudo
siempre se persiste.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.informante import Informante
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.telegram.pick_extractor import ExtractedPick
from app.services.telegram.processor import (
    _text_similarity,
    _word_set_similarity,
    process_incoming_message,
)


class TestWordSetSimilarity:
    def test_mismas_palabras_en_distinto_orden_son_muy_similares(self):
        similarity = _word_set_similarity("Real Madrid gana", "GANA REAL MADRID")
        assert similarity >= 0.85

    def test_palabras_distintas_tienen_baja_similitud(self):
        similarity = _word_set_similarity(
            "Titouan Droguet gana", "Nicolai Budkov Kjaer gana"
        )
        assert similarity < 0.85

    def test_texto_vacio_no_es_similar(self):
        assert _word_set_similarity("", "Real Madrid gana") == 0.0


class TestTextSimilarity:
    def test_textos_identicos_son_totalmente_similares(self):
        assert _text_similarity("Real Madrid gana", "Real Madrid gana") == 1.0


@pytest.fixture
def fake_settings(monkeypatch):
    """Simula una Settings con OPENAI_API_KEY configurada."""
    settings = SimpleNamespace(openai_api_key="test-key")
    monkeypatch.setattr(
        "app.services.telegram.processor.get_settings",
        lambda: settings,
    )
    return settings


@pytest.mark.asyncio
class TestProcessIncomingMessage:
    async def test_extractor_exception_preserves_raw_message(
        self, session, fake_settings, monkeypatch
    ):
        """Si extract_pick lanza una excepción, el mensaje crudo se guarda igual."""
        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick",
            AsyncMock(side_effect=Exception("OpenAI timeout")),
        )

        await process_incoming_message(
            session=session,
            channel="Test Channel",
            channel_id=123456,
            message_id=1,
            text="Real Madrid gana cuota 2.5",
        )

        raw_result = await session.exec(
            select(TelegramRawMessage).where(TelegramRawMessage.message_id == 1)
        )
        raw = raw_result.scalars().one()
        assert raw.channel_id == 123456
        assert raw.text == "Real Madrid gana cuota 2.5"
        assert raw.processed is False

        parsed_result = await session.exec(
            select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
        )
        assert parsed_result.first() is None

    async def test_successful_pick_is_persisted(
        self, session, fake_settings, monkeypatch
    ):
        """Un pick válido genera raw (processed=True) y ParsedPick."""
        extracted = ExtractedPick(
            es_apuesta=True,
            seleccion="Real Madrid gana",
            mercado="ganador",
            cuota=2.5,
            metodo="rule",
            confianza=0.95,
        )
        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick",
            AsyncMock(return_value=extracted),
        )

        await process_incoming_message(
            session=session,
            channel="Test Channel",
            channel_id=123456,
            message_id=2,
            text="Real Madrid gana cuota 2.5",
        )

        raw_result = await session.exec(
            select(TelegramRawMessage).where(TelegramRawMessage.message_id == 2)
        )
        raw = raw_result.scalars().one()
        assert raw.processed is True

        parsed_result = await session.exec(
            select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
        )
        parsed = parsed_result.scalars().one()
        assert parsed.es_apuesta is True
        assert parsed.seleccion == "Real Madrid gana"
        assert parsed.informante_id is not None

        informante_result = await session.exec(
            select(Informante).where(Informante.id == parsed.informante_id)
        )
        informante = informante_result.scalars().one()
        assert informante.nombre == "Test Channel"

    async def test_rejected_message_is_persisted_as_not_bet(
        self, session, fake_settings, monkeypatch
    ):
        """Un mensaje no apuesta queda marcado como processed=True."""
        extracted = ExtractedPick(es_apuesta=False, metodo="rejected", confianza=0.95)
        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick",
            AsyncMock(return_value=extracted),
        )

        await process_incoming_message(
            session=session,
            channel="Test Channel",
            channel_id=123456,
            message_id=3,
            text="Promoción de la casa de apuestas",
        )

        raw_result = await session.exec(
            select(TelegramRawMessage).where(TelegramRawMessage.message_id == 3)
        )
        raw = raw_result.scalars().one()
        assert raw.processed is True

        parsed_result = await session.exec(
            select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
        )
        parsed = parsed_result.scalars().one()
        assert parsed.es_apuesta is False
        assert parsed.informante_id is not None
        assert parsed.informante == "Test Channel"
