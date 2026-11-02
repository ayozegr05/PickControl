"""Tests de la auditoría diaria de falsos rechazos."""

from datetime import timedelta

from app.core.dates import utc_now
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.maintenance import reject_audit as ra
from app.services.telegram.pick_extractor import ExtractedPick


def _rejected(session, i: int, text: str = "mensaje") -> ParsedPick:
    raw = TelegramRawMessage(
        id=i,
        channel_id=-1001,
        message_id=i,
        channel_name="canal",
        text=text,
        received_at=utc_now() - timedelta(hours=6),
        processed=True,
    )
    session.add(raw)
    pick = ParsedPick(
        id=i,
        raw_message_id=i,
        es_apuesta=False,
        metodo="rejected",
    )
    session.add(pick)
    return pick


def _bet(**kw) -> ExtractedPick:
    base = dict(
        es_apuesta=True,
        deporte="futbol",
        evento="A vs B",
        mercado="ganador",
        seleccion="A gana",
        cuota=2.0,
        metodo="llm-groq",
        confianza=0.8,
    )
    base.update(kw)
    return ExtractedPick(**base)


class TestAuditRejectedPicks:
    async def test_falso_rechazo_se_promociona(self, session, monkeypatch):
        _rejected(session, 1, "Sevilla gana @2.0 stake 2")
        await session.commit()

        async def fake_extract(text, key, informante, fecha_referencia):
            return _bet()

        monkeypatch.setattr(ra, "extract_pick", fake_extract)
        report = await ra.audit_rejected_picks(session)
        await session.commit()

        assert report.muestreados == 1
        assert report.recuperados == 1
        pick = await session.get(ParsedPick, 1)
        assert pick.es_apuesta is True
        assert pick.metodo == "llm-groq"
        assert "[re-audit]" in pick.explicacion
        assert pick.fecha_evento is not None  # fallback a received_at

    async def test_sigue_rechazado_y_no_se_remuestrea(self, session, monkeypatch):
        _rejected(session, 1)
        await session.commit()

        async def fake_extract(text, key, informante, fecha_referencia):
            return ExtractedPick(es_apuesta=False)

        monkeypatch.setattr(ra, "extract_pick", fake_extract)
        r1 = await ra.audit_rejected_picks(session)
        assert r1.sigue_rechazado == 1
        # La marca reject-audit evita re-muestrearlo en la pasada de mañana.
        r2 = await ra.audit_rejected_picks(session)
        assert r2.muestreados == 0
        pick = await session.get(ParsedPick, 1)
        assert pick.es_apuesta is False

    async def test_duplicado_se_fusiona_sin_promocionar(self, session, monkeypatch):
        rejected = _rejected(session, 1, "texto distinto, nada que ver")
        existente = ParsedPick(
            id=99,
            raw_message_id=1,
            es_apuesta=True,
            metodo="llm",
            seleccion="A gana",
            cuota=1.9,
        )
        session.add(existente)
        await session.commit()

        async def fake_extract(text, key, informante, fecha_referencia):
            return _bet()

        async def fake_dup(session, channel, pick, received_at):
            return existente

        monkeypatch.setattr(ra, "extract_pick", fake_extract)
        monkeypatch.setattr(ra, "_find_duplicate_pick", fake_dup)
        report = await ra.audit_rejected_picks(session)

        assert report.duplicados == 1
        assert report.recuperados == 0
        assert rejected.es_apuesta is False

    async def test_error_de_extraccion_no_tumba(self, session, monkeypatch):
        _rejected(session, 1)
        await session.commit()

        async def fake_extract(text, key, informante, fecha_referencia):
            raise RuntimeError("cascada seca")

        monkeypatch.setattr(ra, "extract_pick", fake_extract)
        report = await ra.audit_rejected_picks(session)
        assert report.errores == 1
        assert report.muestreados == 1


class TestRunRejectAudit:
    async def test_push_solo_si_recupera(self, session, monkeypatch):
        _rejected(session, 1, "texto con apuesta real")
        _rejected(session, 2, "solo charla del canal")
        await session.commit()
        avisos = []

        async def fake_notify(count, detail):
            avisos.append((count, detail))

        picks = [_bet(), ExtractedPick(es_apuesta=False)]

        async def fake_extract(text, key, informante, fecha_referencia):
            return picks.pop(0)

        monkeypatch.setattr(ra, "notify_rejects_recovered", fake_notify)
        monkeypatch.setattr(ra, "extract_pick", fake_extract)
        monkeypatch.setattr(ra, "AsyncSessionLocal", lambda: _ctx(session))

        await ra.run_reject_audit()
        assert len(avisos) == 1
        assert avisos[0][0] == 1


def _ctx(session):
    return _Ctx(session)


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return False
