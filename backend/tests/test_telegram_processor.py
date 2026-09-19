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
    _merge_pick_data,
    _text_similarity,
    _word_set_similarity,
    _word_subset,
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


class TestWordSubset:
    def test_nombre_parcial_es_subconjunto(self):
        # "Tom gana" (texto del tipster) ⊂ "Tom Gentzsch gana" (OCR).
        assert _word_subset("Tom gana", "Tom Gentzsch gana") is True

    def test_no_es_subconjunto_con_palabras_distintas(self):
        assert _word_subset("Tom gana", "Real Madrid gana") is False

    def test_una_sola_palabra_no_basta(self):
        # Una selección genérica de 1 palabra no debe hacer match con
        # cualquier pick del canal.
        assert _word_subset("Gana", "Tom Gentzsch gana") is False

    def test_ignora_stopwords(self):
        # Foto "Más de 3 tarjetas" ⊂ texto "Más 3 tarjetas en el partido".
        assert _word_subset("Más de 3 tarjetas", "Más 3 tarjetas en el partido") is True


class TestMergePickData:
    def test_fusiona_campos_que_faltan(self):
        target = ParsedPick(seleccion="Tom Gentzsch gana", cuota=1.53)
        source = ExtractedPick(es_apuesta=True, seleccion="Tom gana", stake=4.0)
        assert _merge_pick_data(target, source) is True
        assert target.stake == 4.0
        assert target.cuota == 1.53  # no se pisa lo que ya tiene

    def test_no_fusiona_si_no_falta_nada(self):
        target = ParsedPick(seleccion="X gana", cuota=1.5, stake=2.0)
        source = ExtractedPick(es_apuesta=True, seleccion="X gana", stake=4.0)
        assert _merge_pick_data(target, source) is False
        assert target.stake == 2.0


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
        assert informante.es_canal_telegram is True

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


# OCR típico de un slip abierto de bet365 (caso real Lady Bets 13610):
# trae rival y cuota, pero no stake ni "cuota"/"stake" explícitos.
SLIP_OCR = (
    "2.000,00€ Sencillas\n"
    "Zizou Bergs 1.50\n"
    "Ganará el encuentro\n"
    "Jurij Rodionov\n"
    "Zizou Bergs\n"
    "Imp:\n"
    "2.000,00€\n"
    "Ganancias\n"
    "3.000,00€\n"
    "Cerrar apuesta 2.000,00€"
)


def _photo_raw(message_id: int, ocr: str, when) -> TelegramRawMessage:
    return TelegramRawMessage(
        channel_id=1,
        message_id=message_id,
        channel_name="Canal",
        text="",
        media_path=f"media/{message_id}.jpg",
        extracted_text=ocr,
        processed=True,
        received_at=when,
    )


@pytest.mark.asyncio
class TestPhotoTextPairing:
    """Emparejado boleto (OCR) + texto del pick en ventana de 10 min."""

    async def test_texto_tras_boleto_extrae_con_ocr_combinado(
        self, session, fake_settings, monkeypatch
    ):
        """En vivo (foto primero): el texto se extrae junto al OCR del
        slip, así que el pick sale con rival y cuota de la foto."""
        from datetime import datetime

        when = datetime(2026, 9, 19, 10, 13)
        session.add(_photo_raw(100, SLIP_OCR, when))
        await session.commit()

        captured = {}

        async def fake_extract(text, *args, **kwargs):
            captured["text"] = text
            return ExtractedPick(
                es_apuesta=True,
                seleccion="Bergs gana",
                evento="Zizou Bergs vs Jurij Rodionov",
                cuota=1.5,
                stake=4.0,
                metodo="llm",
                confianza=0.9,
            )

        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick", fake_extract
        )

        await process_incoming_message(
            session=session,
            channel="Canal",
            channel_id=1,
            message_id=101,
            text="TENIS - Copa davis\nBergs gana\nSTAKE 4",
            message_date=datetime(2026, 9, 19, 10, 15),
        )

        # El extractor vio el OCR del slip concatenado al texto.
        assert "Jurij Rodionov" in captured["text"]
        assert "Bergs gana" in captured["text"]

        apuestas = (
            (
                await session.exec(
                    select(ParsedPick).where(
                        ParsedPick.es_apuesta == True  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(apuestas) == 1
        assert apuestas[0].evento == "Zizou Bergs vs Jurij Rodionov"
        assert apuestas[0].cuota == 1.5
        assert apuestas[0].stake == 4.0

    async def test_texto_enriquece_pick_que_la_foto_ya_creo(
        self, session, fake_settings, monkeypatch
    ):
        """Caso Dm7 Gratuito: la foto sola ya creó un pick (evento
        perfecto pero sin cuota/stake); el texto lo enriquece en lugar
        de crear un duplicado."""
        from datetime import datetime

        when = datetime(2026, 9, 19, 6, 36)
        photo = _photo_raw(100, SLIP_OCR, when)
        session.add(photo)
        await session.flush()
        session.add(
            ParsedPick(
                raw_message_id=photo.id,
                es_apuesta=True,
                seleccion="Menos 3,5 Goles",
                evento="Celta de Vigo - Racing de Santander",
            )
        )
        await session.commit()

        async def fake_extract(text, *args, **kwargs):
            return ExtractedPick(
                es_apuesta=True,
                seleccion="Menos de 3,5 goles",
                evento="Celta de Vigo - Racing de Santander",
                cuota=1.5,
                stake=4.0,
                metodo="rule",
                confianza=0.85,
            )

        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick", fake_extract
        )

        await process_incoming_message(
            session=session,
            channel="Canal",
            channel_id=1,
            message_id=101,
            text="Menos de 3,5 goles\nCUOTA 1.50\nSTAKE 4",
            message_date=datetime(2026, 9, 19, 6, 38),
        )

        apuestas = (
            (
                await session.exec(
                    select(ParsedPick).where(
                        ParsedPick.es_apuesta == True  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        # Un solo pick: el de la foto enriquecido con cuota y stake.
        assert len(apuestas) == 1
        assert apuestas[0].cuota == 1.5
        assert apuestas[0].stake == 4.0
        assert "+par" in (apuestas[0].metodo or "")

    async def test_boleto_enriquece_pick_del_texto_ya_guardado(
        self, session, fake_settings, monkeypatch
    ):
        """Caso catch-up (nuevo→viejo): el texto ya se procesó con un
        pick incompleto; cuando llega la foto después, enriquece ese
        pick mirando hacia adelante."""
        from datetime import datetime

        when_text = datetime(2026, 9, 19, 10, 15)
        text_raw = TelegramRawMessage(
            channel_id=1,
            message_id=101,
            channel_name="Canal",
            text="TENIS - Copa davis\nBergs gana\nSTAKE 4",
            processed=True,
            received_at=when_text,
        )
        session.add(text_raw)
        await session.flush()
        session.add(
            ParsedPick(
                raw_message_id=text_raw.id,
                es_apuesta=True,
                seleccion="Bergs gana",
                evento="TENIS - Copa davis",
                stake=4.0,
            )
        )
        await session.commit()

        async def fake_extract(text, *args, **kwargs):
            return ExtractedPick(
                es_apuesta=True,
                seleccion="Bergs gana",
                evento="Zizou Bergs vs Jurij Rodionov",
                cuota=1.5,
                stake=4.0,
                metodo="llm",
                confianza=0.9,
            )

        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick", fake_extract
        )

        await process_incoming_message(
            session=session,
            channel="Canal",
            channel_id=1,
            message_id=100,
            text="",
            media_path="media/100.jpg",
            extracted_text=SLIP_OCR,
            message_date=datetime(2026, 9, 19, 10, 13),
        )

        apuestas = (
            (
                await session.exec(
                    select(ParsedPick).where(
                        ParsedPick.es_apuesta == True  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(apuestas) == 1
        assert apuestas[0].evento == "Zizou Bergs vs Jurij Rodionov"
        assert apuestas[0].cuota == 1.5

    async def test_foto_sin_pinta_de_boleto_no_empareja(
        self, session, fake_settings, monkeypatch
    ):
        """Una captura de chat (sin marcadores de slip) no se fusiona
        con el pick del texto."""
        from datetime import datetime

        when = datetime(2026, 9, 19, 10, 13)
        session.add(_photo_raw(100, "buen pick ayer cracks 😎", when))
        await session.commit()

        captured = {}

        async def fake_extract(text, *args, **kwargs):
            captured["text"] = text
            return ExtractedPick(
                es_apuesta=True,
                seleccion="Bergs gana",
                stake=4.0,
                metodo="rule",
                confianza=0.85,
            )

        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick", fake_extract
        )

        await process_incoming_message(
            session=session,
            channel="Canal",
            channel_id=1,
            message_id=101,
            text="Bergs gana\nCuota 1.50 Stake 4",
            message_date=datetime(2026, 9, 19, 10, 15),
        )

        assert "buen pick ayer" not in captured["text"]

    async def test_boleto_liquidado_no_empareja(
        self, session, fake_settings, monkeypatch
    ):
        """El repost del verde (sello GANADOR) no es pareja válida: su
        rival/cuota pertenecen al pick de ayer."""
        from datetime import datetime

        settled = "SELLO: GANADOR\n" + SLIP_OCR
        when = datetime(2026, 9, 19, 10, 4)
        session.add(_photo_raw(99, settled, when))
        await session.commit()

        captured = {}

        async def fake_extract(text, *args, **kwargs):
            captured["text"] = text
            return ExtractedPick(
                es_apuesta=True,
                seleccion="Bergs gana",
                stake=4.0,
                metodo="rule",
                confianza=0.85,
            )

        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick", fake_extract
        )

        await process_incoming_message(
            session=session,
            channel="Canal",
            channel_id=1,
            message_id=101,
            text="Bergs gana\nCuota 1.50 Stake 4",
            message_date=datetime(2026, 9, 19, 10, 15),
        )

        assert "Jurij Rodionov" not in captured["text"]
