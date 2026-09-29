"""Tests del procesador de mensajes de Telegram.

La primera parte cubre las heurísticas de deduplicación (funciones puras).
La segunda parte son tests de integración contra la base de datos en
memoria: comprobamos que, aunque el extractor falle, el mensaje crudo
siempre se persiste.
"""

from datetime import datetime, timedelta
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
    combinada_signature,
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


class TestCombinadaSignature:
    """Firma canónica del boleto: patas normalizadas sin orden."""

    def test_normaliza_orden_acentos_y_mayusculas(self):
        a = [
            SimpleNamespace(seleccion="Isak marca", mercado="goleador", linea=None),
            SimpleNamespace(
                seleccion="Menos de 9.5 córners", mercado="córners", linea=9.5
            ),
        ]
        b = [
            SimpleNamespace(
                seleccion="MENOS DE 9,5 CORNERS", mercado="Corners", linea=9.5
            ),
            SimpleNamespace(seleccion="MARCA ISAK", mercado="goleador", linea=None),
        ]
        assert combinada_signature(a) == combinada_signature(b)

    def test_linea_distinta_firma_distinta(self):
        a = [SimpleNamespace(seleccion="Más córners", mercado=None, linea=9.5)]
        b = [SimpleNamespace(seleccion="Más córners", mercado=None, linea=10.5)]
        assert combinada_signature(a) != combinada_signature(b)


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


# OCR real de un boleto EN VIVO de bet365 (caso AllSportsPicks raw 8171):
# el tipster publica la captura en directo — sin "Sencillas" ni importe,
# pero con la cabecera de stats y la línea "Mercado - Submercado".
LIVE_SLIP_OCR = (
    "España - La Liga\n"
    "Celta de Vigo 0 0 Racing Santander\n"
    "Estadísticas\n"
    "Estadísticas de jugador\n"
    "09:50\n"
    "Más de 7.0\n"
    "Total - Córners\n"
    "Celta de Vigo v Racing Santander\n"
    "1.50"
)


class TestLiveSlipMarker:
    """El formato de boleto en vivo de bet365 también es pareja válida."""

    def test_slip_en_vivo_con_stats_de_jugador_es_boleto(self):
        from app.services.telegram.processor import _looks_like_slip

        assert _looks_like_slip(LIVE_SLIP_OCR) is True

    def test_slip_en_vivo_descanso_es_boleto(self):
        from app.services.telegram.processor import _looks_like_slip

        ocr = (
            "Andorra - Primera División\n"
            "Sporting Club d'Escaldes 0 - 2 FC Santa Coloma\n"
            "Estadísticas\nLínea de tiempo\n45:00\nDESCANSO\n"
            "Menos de 4.5\nEncuentro - Goles\n1.61"
        )
        assert _looks_like_slip(ocr) is True

    def test_foto_sin_marcadores_no_es_boleto(self):
        from app.services.telegram.processor import _looks_like_slip

        assert _looks_like_slip("Gran día de fútbol hoy en la liga") is False


@pytest.mark.asyncio
class TestFindPairSlipBidirectional:
    """`_find_pair_slip` encuentra el boleto tanto si llega antes del
    texto como después (el tipster publica el pick y luego la captura)."""

    async def test_slip_posterior_al_texto_empareja(self, session):
        from datetime import datetime

        from app.services.telegram.processor import _find_pair_slip

        when = datetime(2026, 9, 20, 16, 45)
        session.add(_photo_raw(200, LIVE_SLIP_OCR, when + timedelta(minutes=2)))
        await session.commit()

        slip = await _find_pair_slip(session, 1, when, 199)
        assert slip is not None
        assert slip.message_id == 200

    async def test_slip_fuera_de_ventana_no_empareja(self, session):
        from datetime import datetime

        from app.services.telegram.processor import _find_pair_slip

        when = datetime(2026, 9, 20, 16, 45)
        session.add(_photo_raw(200, LIVE_SLIP_OCR, when + timedelta(minutes=30)))
        await session.commit()

        assert await _find_pair_slip(session, 1, when, 199) is None

    async def test_elige_el_slip_mas_cercano_en_tiempo(self, session):
        """Entre un slip 1 min antes y otro 5 min después, gana el más
        cercano: el posterior pertenece a otro pick."""
        from datetime import datetime

        from app.services.telegram.processor import _find_pair_slip

        when = datetime(2026, 9, 20, 16, 45)
        session.add(_photo_raw(200, LIVE_SLIP_OCR, when - timedelta(minutes=1)))
        session.add(_photo_raw(201, LIVE_SLIP_OCR, when + timedelta(minutes=5)))
        await session.commit()

        slip = await _find_pair_slip(session, 1, when, 199)
        assert slip is not None
        assert slip.message_id == 200


def _combinada_pick(cuota: float = 91.0) -> ExtractedPick:
    """Slip de 3 patas como el de Liverpool cuota 91 de DM7/AllSports."""
    patas = [
        ExtractedPick(es_apuesta=True, seleccion="Isak marca", mercado="goleador"),
        ExtractedPick(
            es_apuesta=True,
            seleccion="Menos de 9.5 córners",
            mercado="córners",
            linea=9.5,
        ),
        ExtractedPick(
            es_apuesta=True,
            seleccion="Mac Allister tarjeta",
            mercado="tarjetas jugador",
        ),
    ]
    return ExtractedPick(
        es_apuesta=True,
        seleccion=" + ".join(p.seleccion or "" for p in patas),
        mercado="combinada",
        cuota=cuota,
        metodo="llm",
        confianza=0.9,
        patas=patas,
    )


@pytest.mark.asyncio
class TestCombinadaRepostDedup:
    """El mismo slip republicado días después (fuera de la ventana ±6 h
    de la dedup por texto) se detecta por firma de patas + cuota: la
    copia nace ya `anulada · duplicado` — visible para auditoría pero
    fuera de stats y de la cola de verificación."""

    async def test_repost_dias_despues_marca_duplicado(
        self, session, fake_settings, monkeypatch
    ):
        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick",
            AsyncMock(return_value=_combinada_pick()),
        )
        await process_incoming_message(
            session=session,
            channel="Canal A",
            channel_id=1,
            message_id=1,
            text="slip",
            message_date=datetime(2026, 9, 11, 12, 0),
        )
        await process_incoming_message(
            session=session,
            channel="Canal A",
            channel_id=1,
            message_id=2,
            text="slip repost",
            message_date=datetime(2026, 9, 15, 12, 0),
        )

        parents = (
            (await session.exec(select(ParsedPick).where(ParsedPick.es_combinada)))
            .scalars()
            .all()
        )
        assert len(parents) == 2
        canonical = min(parents, key=lambda p: p.id)
        dup = max(parents, key=lambda p: p.id)
        assert canonical.anulada is False
        assert dup.anulada is True
        assert dup.motivo_anulada == "duplicado"
        assert dup.verificado_por == "auto"
        assert dup.verificado_at is not None

        dup_legs = (
            (
                await session.exec(
                    select(ParsedPick).where(ParsedPick.combinada_id == dup.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(dup_legs) == 3
        assert all(
            leg.anulada and leg.motivo_anulada == "duplicado" for leg in dup_legs
        )

    async def test_misma_firma_cuota_distinta_no_es_dup(
        self, session, fake_settings, monkeypatch
    ):
        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick",
            AsyncMock(side_effect=[_combinada_pick(91.0), _combinada_pick(45.0)]),
        )
        for i, day in enumerate((11, 15)):
            await process_incoming_message(
                session=session,
                channel="Canal A",
                channel_id=1,
                message_id=i + 1,
                text="slip",
                message_date=datetime(2026, 9, day, 12, 0),
            )
        parents = (
            (await session.exec(select(ParsedPick).where(ParsedPick.es_combinada)))
            .scalars()
            .all()
        )
        # Mismas patas pero cuota total distinta: dos apuestas distintas.
        assert len(parents) == 2
        assert all(not p.anulada for p in parents)

    async def test_misma_firma_en_otro_canal_cuenta(
        self, session, fake_settings, monkeypatch
    ):
        """La dedup es por canal: el mismo slip en dos canales cuenta en
        cada uno (stats independientes por tipster)."""
        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick",
            AsyncMock(return_value=_combinada_pick()),
        )
        await process_incoming_message(
            session=session,
            channel="Canal A",
            channel_id=1,
            message_id=1,
            text="slip",
            message_date=datetime(2026, 9, 11, 12, 0),
        )
        await process_incoming_message(
            session=session,
            channel="Canal B",
            channel_id=2,
            message_id=2,
            text="slip",
            message_date=datetime(2026, 9, 15, 12, 0),
        )
        parents = (
            (await session.exec(select(ParsedPick).where(ParsedPick.es_combinada)))
            .scalars()
            .all()
        )
        assert len(parents) == 2
        assert all(not p.anulada for p in parents)
