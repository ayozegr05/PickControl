"""Tests del healthcheck de ingesta: contadores + gating de la alerta."""

from datetime import timedelta
from types import SimpleNamespace

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

        async def fake_notify(subject, push_body, email_body):
            avisos.append((subject, push_body, email_body))

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
        assert "OCR" in avisos[0][0]

        # Segunda pasada: cooldown activo → no repite el push.
        await ih.run_ingest_healthcheck()
        assert len(avisos) == 1

    async def test_silencio_total_avisa(self, session, monkeypatch):
        """Listener muerto: raws antiguos procesados OK, pero el más
        reciente lleva >18h → alerta aunque no haya fallos de OCR."""
        avisos = []

        async def fake_notify(subject, push_body, email_body):
            avisos.append((subject, email_body))

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
        assert "mensajes" in avisos[0][0].lower()

    async def test_no_avisa_bajo_umbral(self, session, monkeypatch):
        avisos = []

        async def fake_notify(subject, push_body, email_body):
            avisos.append(subject)

        monkeypatch.setattr(ih, "notify_ingest_down", fake_notify)
        monkeypatch.setattr(ih, "AsyncSessionLocal", lambda: _session_ctx(session))
        session.add(_raw(1, media_path="a.jpg", extracted_text=None))
        await session.commit()

        await ih.run_ingest_healthcheck()
        assert avisos == []


class TestBuildProblems:
    """Cada señal del healthcheck genera UN problema con causa,
    impacto y acción en lenguaje claro — el correo debe decir QUÉ
    falla, no solo que "la ingesta está caída"."""

    def test_fotos_sin_ocr_problema_ocr(self):
        h = ih.IngestHealth(raws_ventana=44, fotos_sin_ocr=25, raws_sin_procesar=0)
        problems = ih._build_problems(h)
        assert [p.kind for p in problems] == ["ocr"]
        p = problems[0]
        assert "25" in p.title
        # Causa: los lectores de imágenes no responden.
        assert "lector" in p.cause.lower() or "Gemini" in p.cause
        # Impacto honesto: picks que llegan como foto no se interpretan.
        assert "foto" in p.impact.lower()
        # Acción: se autorrecupera — los mensajes están guardados.
        assert "guardado" in p.action.lower() or "rescate" in p.action.lower()
        assert "no hace falta" in p.action.lower() or "ninguna" in p.action.lower()

    def test_raws_sin_procesar_problema_extraccion(self):
        h = ih.IngestHealth(raws_ventana=44, fotos_sin_ocr=0, raws_sin_procesar=10)
        problems = ih._build_problems(h)
        assert [p.kind for p in problems] == ["extraccion"]
        p = problems[0]
        assert "10" in p.title
        assert "extractor" in p.cause.lower() or "LLM" in p.cause
        assert "reproces" in p.action.lower() or "rescate" in p.action.lower()

    def test_silencio_problema_listener(self):
        h = ih.IngestHealth(
            raws_ventana=0,
            fotos_sin_ocr=0,
            raws_sin_procesar=0,
            ultimo_raw=utc_now() - timedelta(hours=30),
        )
        problems = ih._build_problems(h)
        assert [p.kind for p in problems] == ["listener"]
        p = problems[0]
        # El silencio SÍ requiere acción humana.
        assert "acción" in p.action.lower() or "revisar" in p.action.lower()

    def test_varios_problemas_a_la_vez(self):
        h = ih.IngestHealth(raws_ventana=44, fotos_sin_ocr=25, raws_sin_procesar=10)
        assert [p.kind for p in ih._build_problems(h)] == ["ocr", "extraccion"]

    def test_sin_problemas_lista_vacia(self):
        h = ih.IngestHealth(raws_ventana=44, fotos_sin_ocr=0, raws_sin_procesar=0)
        assert ih._build_problems(h) == []


class TestFormatAlert:
    """El correo debe dejar claro: qué ENTRA, qué FALLA, por qué y
    si hace falta actuar — más el estado de cada provider."""

    def _health(self, **kw):
        base = dict(raws_ventana=44, fotos_sin_ocr=25, raws_sin_procesar=0)
        base.update(kw)
        return ih.IngestHealth(**base)

    def test_subject_nombra_el_fallo_real(self):
        h = self._health()
        problems = ih._build_problems(h)
        subject, _, _ = ih._format_alert(h, problems, [])
        assert "OCR" in subject
        # Nunca el ambiguo "ingesta caída" cuando el listener vive.
        assert "caída" not in subject.lower()

    def test_subject_silencio_si_dice_ingesta_parada(self):
        h = self._health(
            raws_ventana=0,
            fotos_sin_ocr=0,
            ultimo_raw=utc_now() - timedelta(hours=30),
        )
        problems = ih._build_problems(h)
        subject, _, _ = ih._format_alert(h, problems, [])
        assert "mensajes" in subject.lower()

    def test_subject_varios_problemas(self):
        h = self._health(raws_sin_procesar=10)
        problems = ih._build_problems(h)
        subject, _, _ = ih._format_alert(h, problems, [])
        assert "OCR" in subject and "extracción" in subject.lower()

    def test_email_dice_que_los_mensajes_siguen_entrando(self):
        h = self._health()
        problems = ih._build_problems(h)
        _, _, email = ih._format_alert(h, problems, ["• OCR Gemini: aparcado"])
        # Lo primero: la entrada está viva — no es una caída total.
        assert "44" in email
        assert "entr" in email.lower()
        # Y trae el estado de providers.
        assert "OCR Gemini: aparcado" in email

    def test_email_silencio_no_dice_que_entra_nada(self):
        h = self._health(
            raws_ventana=0,
            fotos_sin_ocr=0,
            ultimo_raw=utc_now() - timedelta(hours=30),
        )
        problems = ih._build_problems(h)
        subject, _, email = ih._format_alert(h, problems, [])
        assert "44" not in email

    def test_push_es_resumen_de_una_linea(self):
        h = self._health()
        problems = ih._build_problems(h)
        _, push, _ = ih._format_alert(h, problems, [])
        assert "25" in push
        assert "44" in push  # "siguen entrando N"


class TestProviderStatus:
    """Línea de estado por pieza de la cadena: key/OK/aparcado."""

    def test_gemini_sin_key(self, monkeypatch):
        settings = SimpleNamespace(
            google_api_key=None, groq_api_key=None, openrouter_api_key=None
        )
        lines = ih._provider_status_lines(settings)
        assert any("Gemini" in ln and "sin key" in ln for ln in lines)

    def test_gemini_aparcado_por_cuota(self, monkeypatch):
        from app.services.telegram import ocr

        for m in ocr._GEMINI_MODELS:
            monkeypatch.setitem(
                ocr._GEMINI_QUOTA_UNTIL, m, utc_now() + timedelta(hours=8)
            )
        settings = SimpleNamespace(
            google_api_key="g", groq_api_key=None, openrouter_api_key=None
        )
        lines = ih._provider_status_lines(settings)
        assert any("Gemini" in ln and "aparcado" in ln for ln in lines)
        ocr._GEMINI_QUOTA_UNTIL.clear()

    def test_groq_ok_con_key(self):
        settings = SimpleNamespace(
            google_api_key=None, groq_api_key="g", openrouter_api_key=None
        )
        lines = ih._provider_status_lines(settings)
        assert any("Groq" in ln and "OK" in ln for ln in lines)


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
