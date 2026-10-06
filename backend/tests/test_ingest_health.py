"""Tests del healthcheck de ingesta: contadores + gating de la alerta."""

from datetime import timedelta

from app.core.dates import utc_now
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.maintenance import ingest_health as ih


def _raw(i: int, **kw) -> TelegramRawMessage:
    base = dict(
        id=i,
        channel_id=-1001,
        message_id=i,
        channel_name="canal",
        text="texto",
        # 30 min atrás: dentro de ventana (3h) y fuera de la gracia
        # (10 min) — cuenta como señal.
        received_at=utc_now() - timedelta(minutes=30),
        processed=True,
    )
    base.update(kw)
    return TelegramRawMessage(**base)


class TestCheckIngestHealth:
    async def test_cuenta_senales_en_ventana(self, session):
        session.add_all(
            [
                # Foto sin OCR en ventana → cuenta
                _raw(1, media_path="a.jpg", extracted_text=None, processed=True),
                _raw(2, media_path="b.jpg", extracted_text="", processed=True),
                # Foto con OCR → no cuenta como fallo
                _raw(3, media_path="c.jpg", extracted_text="texto"),
                # Sin procesar en ventana → cuenta
                _raw(4, processed=False),
                # Fuera de ventana (hace 5h) → no cuenta nada
                _raw(
                    5,
                    media_path="d.jpg",
                    extracted_text=None,
                    processed=False,
                    received_at=utc_now() - timedelta(hours=5),
                ),
                # Recién llegada (2 min): en gracia → no es fallo,
                # pero sí cuenta en el volumen de la ventana.
                _raw(
                    6,
                    media_path="e.jpg",
                    extracted_text=None,
                    processed=False,
                    received_at=utc_now() - timedelta(minutes=2),
                ),
            ]
        )
        await session.commit()
        h = await ih.check_ingest_health(session)
        assert h.raws_ventana == 5
        assert h.fotos_sin_ocr == 2
        assert h.raws_sin_procesar == 1

    async def test_bd_vacia_ceros(self, session):
        h = await ih.check_ingest_health(session)
        assert h.fotos_sin_ocr == 0
        assert h.raws_sin_procesar == 0


class TestRunHealthcheck:
    async def test_avisa_cuando_supera_umbral(self, session, monkeypatch, tmp_path):
        # Healthcheck escribe su cooldown en provider_state.json —
        # el fixture de conftest ya lo aísla por test.
        avisos = []

        async def fake_notify(detail, raws):
            avisos.append((detail, raws))

        monkeypatch.setattr(ih, "notify_ingest_down", fake_notify)
        monkeypatch.setattr(ih, "AsyncSessionLocal", lambda: _session_ctx(session))
        session.add_all(
            [
                _raw(i, media_path=f"{i}.jpg", extracted_text=None)
                for i in range(1, ih._OCR_FAIL_THRESHOLD + 1)
            ]
        )
        await session.commit()

        await ih.run_ingest_healthcheck()
        assert len(avisos) == 1
        assert "fotos sin OCR" in avisos[0][0]

        # Segunda pasada: cooldown activo → no repite el push.
        await ih.run_ingest_healthcheck()
        assert len(avisos) == 1

    async def test_silencio_total_avisa(self, session, monkeypatch):
        """Listener muerto: raws antiguos procesados OK, pero el más
        reciente lleva >18h → alerta aunque no haya fallos de OCR."""
        avisos = []

        async def fake_notify(detail, raws):
            avisos.append(detail)

        monkeypatch.setattr(ih, "notify_ingest_down", fake_notify)
        monkeypatch.setattr(ih, "AsyncSessionLocal", lambda: _session_ctx(session))
        # Todo procesado y con OCR, pero de hace 30h — silencio total.
        session.add(
            _raw(
                1,
                media_path="a.jpg",
                extracted_text="texto",
                received_at=utc_now() - timedelta(hours=30),
            )
        )
        await session.commit()

        await ih.run_ingest_healthcheck()
        assert len(avisos) == 1
        assert "sin mensajes nuevos" in avisos[0]

    async def test_no_avisa_bajo_umbral(self, session, monkeypatch):
        avisos = []

        async def fake_notify(detail, raws):
            avisos.append(detail)

        monkeypatch.setattr(ih, "notify_ingest_down", fake_notify)
        monkeypatch.setattr(ih, "AsyncSessionLocal", lambda: _session_ctx(session))
        session.add(_raw(1, media_path="a.jpg", extracted_text=None))
        await session.commit()

        await ih.run_ingest_healthcheck()
        assert avisos == []


def _session_ctx(session):
    """Wrapper `AsyncSessionLocal()`-compatible sobre la sesión del test."""
    return _Ctx(session)


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return False
