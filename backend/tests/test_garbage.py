"""Tests de `maintenance/garbage.py` — detector de slips republicados
y extracciones basura, con dry-run/apply sobre SQLite en memoria.
"""

from datetime import datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.services.maintenance.garbage as garbage
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage

_SLIP_OCR = (
    "Deportivo de La Coruña - Sevilla\n"
    "miércoles, 16 de septiembre de 2026, 19:00\n"
    "Cuota @91.00  Stake 80.00 €  Ganancias potenciales 7280.00 €"
)


def _raw(
    received_at: datetime,
    extracted_text: str | None = None,
    text: str = "",
) -> TelegramRawMessage:
    return TelegramRawMessage(
        id=1,
        channel_id=-1002463340479,
        message_id=79626,
        channel_name="AllSportsPicks",
        text=text,
        extracted_text=extracted_text,
        received_at=received_at,
    )


def _pick(pick_id: int = 10, fecha_evento: datetime | None = None) -> ParsedPick:
    return ParsedPick(
        id=pick_id,
        raw_message_id=1,
        es_apuesta=True,
        evento="Deportivo de La Coruña - Sevilla",
        seleccion="Menos de 9.5 córners",
        fecha_evento=fecha_evento,
    )


class TestSlipPrintedDate:
    def test_formato_largo_con_hora(self):
        assert garbage.slip_printed_date(_SLIP_OCR) == datetime(2026, 9, 16, 19, 0)

    def test_formato_largo_sin_hora(self):
        found = garbage.slip_printed_date("Partido: 3 de octubre de 2026")
        assert found == datetime(2026, 10, 3)

    def test_sin_fecha_devuelve_none(self):
        assert garbage.slip_printed_date("Barcelona - Madrid @2.10") is None
        assert garbage.slip_printed_date(None) is None
        assert garbage.slip_printed_date("") is None

    def test_fecha_sin_ano_no_casa(self):
        # "16 de septiembre" a secas es ambiguo: no se usa.
        assert garbage.slip_printed_date("el 16 de septiembre a las 19:00") is None


class TestAnalyzeRaw:
    def test_slip_de_dia_anterior_es_repost(self):
        """Slip impreso el 16, mensaje el 19 -> republicado/marketing."""
        raw = _raw(datetime(2026, 9, 19, 10, 21), extracted_text=_SLIP_OCR)
        actions = garbage._analyze_raw(raw, [_pick()])
        assert len(actions) == 1
        assert actions[0].action == "flag"
        assert actions[0].reason == "slip_republicado"

    def test_slip_futuro_corrige_fecha_evento(self):
        """Mensaje previo al partido: la fecha impresa corrige la del pick."""
        raw = _raw(datetime(2026, 9, 15, 19, 57), extracted_text=_SLIP_OCR)
        pick = _pick(fecha_evento=datetime(2026, 9, 15, 19, 57))
        actions = garbage._analyze_raw(raw, [pick])
        assert len(actions) == 1
        assert actions[0].action == "fix_date"
        assert actions[0].new_fecha_evento == datetime(2026, 9, 16, 19, 0)

    def test_slip_futuro_fecha_ya_correcta_no_actua(self):
        raw = _raw(datetime(2026, 9, 15, 19, 57), extracted_text=_SLIP_OCR)
        pick = _pick(fecha_evento=datetime(2026, 9, 16, 19, 0))
        assert garbage._analyze_raw(raw, [pick]) == []

    def test_mismo_dia_tras_inicio_es_revision(self):
        """Partido a las 19:00 y mensaje a las 20:00 del mismo día:
        posible live-bet — se reporta, nunca se descarta solo."""
        raw = _raw(datetime(2026, 9, 16, 20, 0), extracted_text=_SLIP_OCR)
        actions = garbage._analyze_raw(raw, [_pick()])
        assert len(actions) == 1
        assert actions[0].action == "review"

    def test_slip_lejano_ignorado(self):
        """Fecha impresa a >60 días del mensaje: ruido del OCR."""
        raw = _raw(datetime(2026, 12, 20, 10, 0), extracted_text=_SLIP_OCR)
        assert garbage._analyze_raw(raw, [_pick()]) == []

    def test_sin_fecha_impresa_no_actua(self):
        raw = _raw(datetime(2026, 9, 19, 10, 21), extracted_text="Solo cuotas")
        assert garbage._analyze_raw(raw, [_pick()]) == []


class TestAnalyzePickFields:
    def test_evento_vacio_es_basura(self):
        pick = _pick()
        pick.evento = None
        action = garbage._analyze_pick_fields(pick)
        assert action is not None
        assert action.action == "flag"
        assert action.reason == "evento_vacio"

    def test_marketing_en_seleccion(self):
        pick = _pick()
        pick.seleccion = "Tendréis menos de 1 hora para acceder"
        action = garbage._analyze_pick_fields(pick)
        assert action is not None
        assert action.reason == "marketing"

    def test_pick_normal_sin_accion(self):
        assert garbage._analyze_pick_fields(_pick()) is None


@pytest_asyncio.fixture
async def db(monkeypatch):
    """SQLite en memoria compartida (StaticPool: una sola conexión para
    todas las sesiones que abre `garbage`)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(garbage, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


async def _seed_repost(factory) -> int:
    """Raw con slip del 16-sep republicado el 19-sep + pick pendiente."""
    async with factory() as session:
        raw = _raw(datetime(2026, 9, 19, 10, 21), extracted_text=_SLIP_OCR)
        session.add(raw)
        await session.flush()
        pick = _pick(fecha_evento=datetime(2026, 9, 19, 10, 21))
        pick.raw_message_id = raw.id
        session.add(pick)
        await session.commit()
        return pick.id


class TestDryRunYApply:
    async def test_dry_run_no_escribe(self, db):
        pick_id = await _seed_repost(db)
        report = await garbage.analyze_garbage()
        assert [a.action for a in report.actions] == ["flag"]
        async with db() as session:
            pick = await session.get(ParsedPick, pick_id)
            assert pick.es_apuesta is True  # intacto

    async def test_apply_marca_sin_borrar(self, db):
        pick_id = await _seed_repost(db)
        report = await garbage.analyze_garbage()
        applied = await garbage.apply_garbage(report)
        assert applied == {"flagged": 1, "fixed_dates": 0}
        async with db() as session:
            pick = await session.get(ParsedPick, pick_id)
            assert pick is not None  # la fila sigue ahí (auditoría)
            assert pick.es_apuesta is False

    async def test_apply_corrige_fecha_evento(self, db):
        async with db() as session:
            raw = _raw(datetime(2026, 9, 15, 19, 57), extracted_text=_SLIP_OCR)
            session.add(raw)
            await session.flush()
            pick = _pick(fecha_evento=datetime(2026, 9, 15, 19, 57))
            pick.raw_message_id = raw.id
            session.add(pick)
            await session.commit()
            pick_id = pick.id
        report = await garbage.analyze_garbage()
        assert [a.action for a in report.actions] == ["fix_date"]
        applied = await garbage.apply_garbage(report)
        assert applied["fixed_dates"] == 1
        async with db() as session:
            pick = await session.get(ParsedPick, pick_id)
            assert pick.fecha_evento == datetime(2026, 9, 16, 19, 0)
            assert pick.es_apuesta is True

    async def test_picks_ya_resueltos_no_se_tocan(self, db):
        async with db() as session:
            raw = _raw(datetime(2026, 9, 19, 10, 21), extracted_text=_SLIP_OCR)
            session.add(raw)
            await session.flush()
            pick = _pick(fecha_evento=datetime(2026, 9, 19, 10, 21))
            pick.raw_message_id = raw.id
            pick.acierto = False  # ya liquidado
            pick.verificado_por = "auto"
            session.add(pick)
            await session.commit()
        report = await garbage.analyze_garbage()
        assert report.actions == []
