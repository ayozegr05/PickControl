"""Tests del barrido diario de pendientes fuera de la ventana de
verificación (`maintenance/backlog.py`, loop `verify_backlog`).

`AsyncSessionLocal` se sustituye por una factoría sobre SQLite en
memoria; `verify_pick` y `_get_providers` se mockean — lo que se
ejercita de verdad es el filtrado (solo pendientes viejos, sin padres
ni anuladas), la escritura en apply, el re-lanzado de combinadas y
la tolerancia a picks que explotan.
"""

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.services.maintenance.backlog as backlog
from app.core.dates import utc_now
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage


@pytest_asyncio.fixture
async def db(monkeypatch):
    """BD SQLite propia + `AsyncSessionLocal` redirigido al servicio."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(backlog, "AsyncSessionLocal", factory)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _make_pick(session, mid: int, days_old: int, **kw) -> ParsedPick:
    raw = TelegramRawMessage(
        channel_id=1,
        message_id=mid,
        channel_name="Test",
        text="pick",
        processed=True,
    )
    session.add(raw)
    await session.flush()
    pick = ParsedPick(
        raw_message_id=raw.id,
        es_apuesta=True,
        deporte="fútbol",
        fecha_evento=utc_now() - timedelta(days=days_old),
        **kw,
    )
    session.add(pick)
    await session.flush()
    return pick


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(backlog, "_get_providers", lambda: _async(["fake"]))
    return ["fake"]


async def _async(value):
    return value


def _fake_verify(result):
    async def _verify(pick, providers):
        return result

    return _verify


_NO_LOG = lambda *args: None  # noqa: E731


class TestRunBacklogVerification:
    async def test_solo_intenta_pendientes_viejos(self, db, providers, monkeypatch):
        viejo = await _make_pick(db, 1, 20)
        reciente = await _make_pick(db, 2, 2)
        await _make_pick(db, 3, 30, acierto=True)  # ya resuelto
        await _make_pick(db, 4, 30, anulada=True)  # anulado
        await _make_pick(db, 5, 30, es_combinada=True)  # padre
        await db.commit()
        monkeypatch.setattr(backlog, "verify_pick", _fake_verify((True, False)))

        stats = await backlog.run_backlog_verification(apply=True, delay=0, log=_NO_LOG)

        assert stats["intentados"] == 1
        assert stats["resueltos"] == 1
        await db.refresh(viejo)
        await db.refresh(reciente)
        assert viejo.acierto is True
        assert viejo.verificado_por == "auto"
        assert viejo.verificado_at is not None
        assert reciente.acierto is None  # dentro de ventana, no se tocó

    async def test_dry_run_no_escribe(self, db, providers, monkeypatch):
        viejo = await _make_pick(db, 1, 20)
        await db.commit()
        monkeypatch.setattr(backlog, "verify_pick", _fake_verify((True, False)))

        stats = await backlog.run_backlog_verification(
            apply=False, delay=0, log=_NO_LOG
        )

        assert stats["intentados"] == 1
        assert stats["resueltos"] == 1
        await db.refresh(viejo)
        assert viejo.acierto is None
        assert viejo.verificado_por is None

    async def test_pata_resuelta_liquida_al_padre(self, db, providers, monkeypatch):
        padre = await _make_pick(db, 1, 20, es_combinada=True)
        pata = await _make_pick(db, 2, 20, combinada_id=padre.id)
        await db.commit()
        monkeypatch.setattr(backlog, "verify_pick", _fake_verify((True, False)))

        stats = await backlog.run_backlog_verification(apply=True, delay=0, log=_NO_LOG)

        assert stats["padres_liquidados"] == 1
        await db.refresh(padre)
        await db.refresh(pata)
        assert pata.acierto is True
        assert padre.acierto is True

    async def test_excepcion_en_un_pick_no_aborta(self, db, providers, monkeypatch):
        await _make_pick(db, 1, 20)
        await _make_pick(db, 2, 21)
        await db.commit()
        calls = []

        async def flaky(pick, providers):
            calls.append(pick.id)
            if len(calls) == 1:
                raise RuntimeError("provider caído")
            return True, False

        monkeypatch.setattr(backlog, "verify_pick", flaky)

        stats = await backlog.run_backlog_verification(apply=True, delay=0, log=_NO_LOG)

        assert stats["intentados"] == 2
        assert stats["errores"] == 1
        assert stats["resueltos"] == 1

    async def test_sin_providers_no_intenta_nada(self, db, monkeypatch):
        monkeypatch.setattr(backlog, "_get_providers", lambda: _async([]))
        await _make_pick(db, 1, 20)
        await db.commit()

        stats = await backlog.run_backlog_verification(apply=True, delay=0, log=_NO_LOG)

        assert stats == {
            "intentados": 0,
            "resueltos": 0,
            "errores": 0,
            "padres_liquidados": 0,
        }

    async def test_older_than_none_barre_todo(self, db, providers, monkeypatch):
        await _make_pick(db, 1, 2)  # reciente, pero barrido total
        await db.commit()
        monkeypatch.setattr(backlog, "verify_pick", _fake_verify((None, False)))

        stats = await backlog.run_backlog_verification(
            apply=True, delay=0, older_than=None, log=_NO_LOG
        )

        assert stats["intentados"] == 1
        assert stats["resueltos"] == 0
