"""Tests del rescate de mantenimiento (`services/maintenance/rescue.py`).

`AsyncSessionLocal` se sobreescribe con la sesión SQLite del fixture —
mismo patrón que `test_notifications.py`. OpenAI se corta en
`extract_text_from_image` / `extract_pick` y `get_settings` se
sustituye por un stub con `openai_api_key` presente.
"""

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.core.dates import utc_now
from app.models.odds_snapshot import OddsSnapshot
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.maintenance import rescue
from app.services.odds.historical_backfill import count_pending_backfill
from app.services.telegram.pick_extractor import ExtractedPick

pytestmark = pytest.mark.asyncio


def _session_cm(session):
    """`AsyncSessionLocal` falso que devuelve la sesión del test."""

    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


def _settings_stub(monkeypatch, max_age_days: float = 30.0):
    monkeypatch.setattr(
        rescue,
        "get_settings",
        lambda: SimpleNamespace(
            openai_api_key="test-key", rescue_max_age_days=max_age_days
        ),
    )


_MSG_ID = [0]


async def _raw(session, **kwargs) -> TelegramRawMessage:
    kwargs.setdefault("channel_id", 1)
    kwargs.setdefault("channel_name", "Canal Test")
    kwargs.setdefault("text", "")
    kwargs.setdefault("received_at", utc_now())
    _MSG_ID[0] += 1
    kwargs.setdefault("message_id", _MSG_ID[0])
    raw = TelegramRawMessage(**kwargs)
    session.add(raw)
    await session.commit()
    await session.refresh(raw)
    return raw


class TestRetryPendingOcr:
    async def test_ocr_ok_marca_reproceso(self, session, monkeypatch, tmp_path):
        media = tmp_path / "foto.jpg"
        media.write_bytes(b"fake")
        raw = await _raw(session, media_path=str(media), processed=True)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "extract_text_from_image",
            AsyncMock(return_value="SLIP OCR"),
        )

        resumen = await rescue.retry_pending_ocr()

        assert resumen == {
            "pendientes": 1,
            "sin_fichero": 0,
            "hechos": 1,
            "fallos": 0,
        }
        await session.refresh(raw)
        assert raw.extracted_text == "SLIP OCR"
        # Sin pick asociado -> queda para el reproceso.
        assert raw.processed is False

    async def test_fichero_ausente_se_salta_sin_error(self, session, monkeypatch):
        raw = await _raw(session, media_path="media/telegram/no_existe_xyz.jpg")
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        ocr = AsyncMock(return_value="X")
        monkeypatch.setattr(rescue, "extract_text_from_image", ocr)

        resumen = await rescue.retry_pending_ocr()

        # No está en pendientes (sin fichero) ni falla: simplemente se
        # salta — si el fichero reaparece, una pasada futura lo procesa.
        assert resumen["pendientes"] == 0
        ocr.assert_not_called()
        await session.refresh(raw)
        assert raw.extracted_text is None

    async def test_ocr_vacio_cuenta_como_fallo(self, session, monkeypatch, tmp_path):
        media = tmp_path / "foto.jpg"
        media.write_bytes(b"fake")
        await _raw(session, media_path=str(media))
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue, "extract_text_from_image", AsyncMock(return_value="  ")
        )

        resumen = await rescue.retry_pending_ocr()
        assert resumen["fallos"] == 1

    async def test_foto_antigua_queda_fuera_de_ventana(
        self, session, monkeypatch, tmp_path
    ):
        media = tmp_path / "foto_vieja.jpg"
        media.write_bytes(b"fake")
        raw = await _raw(
            session,
            media_path=str(media),
            received_at=utc_now() - timedelta(days=60),
        )
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        ocr = AsyncMock(return_value="X")
        monkeypatch.setattr(rescue, "extract_text_from_image", ocr)

        resumen = await rescue.retry_pending_ocr()

        assert resumen["pendientes"] == 0
        ocr.assert_not_called()
        await session.refresh(raw)
        assert raw.extracted_text is None

        # Con la ventana desactivada (--max-age-days 0) sí se reintenta.
        resumen = await rescue.retry_pending_ocr(max_age_days=0)
        assert resumen["hechos"] == 1
        await session.refresh(raw)
        assert raw.extracted_text == "X"


class TestReprocessPendingRaws:
    async def test_raw_pendiente_crea_pick(self, session, monkeypatch):
        raw = await _raw(
            session,
            text="Real Madrid gana cuota 2.0",
            processed=False,
        )
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(
            rescue,
            "extract_pick",
            AsyncMock(
                return_value=ExtractedPick(
                    es_apuesta=True,
                    seleccion="Real Madrid gana",
                    cuota=2.0,
                    metodo="rule",
                )
            ),
        )
        monkeypatch.setattr(
            rescue, "_find_duplicate_pick", AsyncMock(return_value=None)
        )

        resumen = await rescue.reprocess_pending_raws()

        assert resumen["procesados"] == 1
        assert resumen["picks"] == 1
        await session.refresh(raw)
        assert raw.processed is True
        pick = (
            await session.exec(
                select(ParsedPick).where(ParsedPick.raw_message_id == raw.id)
            )
        ).first()
        assert pick is not None
        assert pick.seleccion == "Real Madrid gana"
        # Sin fecha en el texto: se usa received_at.
        assert pick.fecha_evento == raw.received_at

    async def test_sin_texto_queda_pendiente(self, session, monkeypatch):
        raw = await _raw(session, text="", processed=False)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        extract = AsyncMock()
        monkeypatch.setattr(rescue, "extract_pick", extract)

        resumen = await rescue.reprocess_pending_raws()

        assert resumen["procesados"] == 0
        extract.assert_not_called()
        await session.refresh(raw)
        assert raw.processed is False

    async def test_no_es_pick_cierra_raw(self, session, monkeypatch):
        """Si el extractor dice "no es pick" el raw se cierra — si no se
        reenviaría al LLM en cada ciclo para siempre."""
        raw = await _raw(session, text="mensaje de chat sin pick", processed=False)
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        monkeypatch.setattr(rescue, "extract_pick", AsyncMock(return_value=None))

        resumen = await rescue.reprocess_pending_raws()

        assert resumen["sin_pick"] == 1
        assert resumen["procesados"] == 0
        await session.refresh(raw)
        assert raw.processed is True

    async def test_raw_antiguo_se_descarta_sin_llm(self, session, monkeypatch):
        """Fuera de la ventana de rescate: processed=True sin llamar al
        LLM — la fila queda en BD pero deja de acumularse en la cola."""
        raw = await _raw(
            session,
            text="pick viejo",
            processed=False,
            received_at=utc_now() - timedelta(days=60),
        )
        _settings_stub(monkeypatch)
        monkeypatch.setattr(rescue, "AsyncSessionLocal", _session_cm(session))
        extract = AsyncMock()
        monkeypatch.setattr(rescue, "extract_pick", extract)

        resumen = await rescue.reprocess_pending_raws()

        assert resumen["antiguos"] == 1
        assert resumen["pendientes"] == 0
        extract.assert_not_called()
        await session.refresh(raw)
        assert raw.processed is True

        # El descarte es permanente: ni sin ventana vuelve a intentarse.
        resumen = await rescue.reprocess_pending_raws(max_age_days=0)
        assert resumen["pendientes"] == 0
        extract.assert_not_called()


class TestBackfillPendingCount:
    async def test_cuenta_solo_pendientes_reales(self, session, monkeypatch):
        # Pick elegible sin backfill.
        await self._pick(session, odds_event_id=None)
        # Pick ya procesado (evento con filas oddspapi).
        done_pick = await self._pick(session, odds_event_id="oddspapi:99")
        session.add(
            OddsSnapshot(
                provider="oddspapi",
                event_ext_id="oddspapi:99",
                market_name="Full time",
                choice_name="1",
                cuota=1.9,
                captured_at=utc_now(),
            )
        )
        # Deporte no soportado por OddsPapi (baloncesto): no cuenta.
        await self._pick(session, deporte="baloncesto")
        await session.commit()

        assert await count_pending_backfill(session) == 1
        _ = done_pick

    async def test_nofixture_miss_excluye(self, session, monkeypatch, tmp_path):
        import app.services.odds.historical_backfill as hb
        from app.services.results import base as results_base

        monkeypatch.setattr(results_base, "_STATE", None)
        monkeypatch.setattr(
            results_base, "_STATE_FILE", tmp_path / "provider_state.json"
        )
        pick = await self._pick(session, odds_event_id=None)
        await session.commit()
        assert await count_pending_backfill(session) == 1

        results_base.mark_missed(hb._NOFIXTURE_MISS_KEY.format(pick.id))
        assert await count_pending_backfill(session) == 0

    async def _pick(self, session, **kwargs) -> ParsedPick:
        kwargs.setdefault("raw_message_id", 1)
        kwargs.setdefault("es_apuesta", True)
        kwargs.setdefault("es_combinada", False)
        kwargs.setdefault("deporte", "futbol")
        kwargs.setdefault("fecha_evento", utc_now() - timedelta(days=1))
        pick = ParsedPick(seleccion="X gana", **kwargs)
        session.add(pick)
        await session.flush()
        return pick
