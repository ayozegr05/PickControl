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

    def test_formato_corto_barras(self):
        # "24/8/26 19:30" — formato corto que imprimen algunas casas.
        found = garbage.slip_printed_date("Osasuna - Levante\n24/8/26 19:30\n")
        assert found == datetime(2026, 8, 24, 19, 30)

    def test_formato_corto_ano_largo(self):
        found = garbage.slip_printed_date("Partido 24/08/2026")
        assert found == datetime(2026, 8, 24)

    def test_formato_corto_invalido_no_casa(self):
        assert garbage.slip_printed_date("cuota 31/2/26") is None
        assert garbage.slip_printed_date("24/8") is None


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

    def test_slip_muy_antiguo_es_repost(self):
        """Slip de junio republicado en septiembre: repost aunque la
        distancia supere los 60 días (la guarda corta solo protege las
        correcciones de fecha, no el descarte)."""
        raw = _raw(datetime(2026, 12, 20, 10, 0), extracted_text=_SLIP_OCR)
        actions = garbage._analyze_raw(raw, [_pick()])
        assert len(actions) == 1
        assert actions[0].action == "flag"
        assert actions[0].reason == "slip_republicado"

    def test_slip_futuro_lejano_no_corrige(self):
        """Fecha impresa FUTURA a >60 días: ruido del OCR — no se
        corrige fecha_evento con ella."""
        far_future_slip = _SLIP_OCR.replace("septiembre", "diciembre")
        raw = _raw(datetime(2026, 9, 15, 19, 57), extracted_text=far_future_slip)
        pick = _pick(fecha_evento=datetime(2026, 9, 15, 19, 57))
        assert garbage._analyze_raw(raw, [pick]) == []

    def test_sin_fecha_impresa_no_actua(self):
        raw = _raw(datetime(2026, 9, 19, 10, 21), extracted_text="Solo cuotas")
        assert garbage._analyze_raw(raw, [_pick()]) == []


class TestSlipLiquidadoYTeaser:
    _WON_SLIP_CHECKS = (
        "CREAR APUESTA  61.00\n"
        "✓ Ante Budimir: 2+ remates a puerta\n"
        "✓ Ruben Garcia: 2+ remates a puerta\n"
        "✓ Ivan Romero será Amonestado\n"
        "Osasuna\nLevante\nImp: 100,00€\n6100,00€  Ganancias\n"
    )
    _WON_SLIP_SEAL = (
        "CREA TU APUESTA 5 pronósticos\nGANAD@S\n"
        "Osasuna - Levante\n24/8/26 19:30\nGanancias 1.220,00 €\n"
    )

    def test_slip_con_checkmarks_es_liquidado(self):
        # Slip "verde" con ✓ por selección + premio pagado — dice
        # "Crear apuesta" porque así se llama el mercado bet-builder.
        raw = _raw(datetime(2026, 9, 21, 18, 15), extracted_text=self._WON_SLIP_CHECKS)
        actions = garbage._analyze_raw(raw, [_pick()])
        assert [a.reason for a in actions] == ["slip_liquidado"]

    def test_sello_ganad_arroba_es_liquidado(self):
        raw = _raw(datetime(2026, 9, 21, 18, 46), extracted_text=self._WON_SLIP_SEAL)
        actions = garbage._analyze_raw(raw, [_pick()])
        assert [a.reason for a in actions] == ["slip_liquidado"]

    def test_teaser_sin_slip_ni_mercados(self):
        raw = _raw(
            datetime(2026, 9, 21, 18, 58),
            text="‼️ DOBLE CREAR APUESTA 2X1 ⚽ CUOTA 81 BAYERN - CITY "
            "+ CUOTA 71 JUVENTUS - BENFICA 🚀💣",
        )
        actions = garbage._analyze_raw(raw, [_pick()])
        assert [a.reason for a in actions] == ["teaser_sin_patas"]

    def test_texto_crear_apuesta_con_mercados_no_es_teaser(self):
        # Texto que sí detalla la apuesta: no es un anuncio vacío.
        raw = _raw(
            datetime(2026, 9, 21, 18, 58),
            text="Mi crear apuesta de hoy: Bayern gana y más de 2.5 goles",
        )
        assert garbage._analyze_raw(raw, [_pick()]) == []

    def test_teaser_en_imagen_de_anuncio(self):
        # El anuncio llega como captura de texto (OCR), no como mensaje —
        # sigue sin patas ni mercados: teaser igualmente.
        raw = _raw(
            datetime(2026, 9, 21, 18, 58),
            extracted_text="!! DOBLE CREAR APUESTA 2X1 ⚽ CUOTA 81 "
            "BAYERN - CITY + CUOTA 71 JUVENTUS - BENFICA",
        )
        actions = garbage._analyze_raw(raw, [_pick()])
        assert [a.reason for a in actions] == ["teaser_sin_patas"]

    def test_varios_boletos_en_una_captura_es_multi_slip(self):
        # Pantallazo "mis apuestas" de un suscriptor: varios boletos
        # con su cabecera CREAR APUESTA + cuota — historial, no pick.
        raw = _raw(
            datetime(2026, 8, 31, 13, 35),
            extracted_text=(
                "400,00€ Crear apuesta\nCREAR APUESTA 1.22\n"
                "Celta de Vigo\nAthletic Club\nCREAR APUESTA 1.12\n"
                "Lazio\nGenoa\nCREAR APUESTA 1.05\nMónaco\n"
            ),
        )
        actions = garbage._analyze_raw(raw, [_pick()])
        assert [a.reason for a in actions] == ["multi_slip"]

    def test_un_solo_slip_no_es_multi(self):
        # Slip normal: botón "Crear apuesta" sin cuota + UNA cabecera.
        raw = _raw(
            datetime(2026, 9, 13, 13, 35),
            extracted_text=(
                "1.000,00€ Crear apuesta\nCREAR APUESTA 1.50\n"
                "Sakellaridis - Ganará el encuentro\n"
                "1° set - Más de 7.5 juegos\nCerrar apuesta"
            ),
        )
        assert garbage._analyze_raw(raw, [_pick()]) == []

    def test_teaser_con_slip_adjunto_no_flag(self):
        # Foto + texto "crear apuesta": el OCR del slip lista las patas
        # con vocabulario de mercado ("Menos de...") — no es teaser.
        raw = _raw(
            datetime(2026, 9, 21, 18, 58),
            extracted_text=_SLIP_OCR,
            text="CREAR APUESTA CUOTA 91 BARCELONA - PSG",
        )
        actions = garbage._analyze_raw(raw, [_pick()])
        assert all(a.reason != "teaser_sin_patas" for a in actions)


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

    async def test_padre_huerfano_se_descarta_con_sus_patas(self, db):
        """Combinada con todas las patas ya descartadas: el padre no
        tiene evento propio — debe morir con ellas."""
        async with db() as session:
            raw = _raw(datetime(2026, 9, 24, 10, 0), text="pack premium")
            session.add(raw)
            await session.flush()
            parent = ParsedPick(
                raw_message_id=raw.id,
                es_apuesta=True,
                es_combinada=True,
                seleccion="PORTUGAL GALES + NORUEGA DINAMARCA",
                fecha_evento=datetime(2026, 9, 24, 10, 0),
            )
            session.add(parent)
            await session.flush()
            for sel in ("PORTUGAL GALES", "NORUEGA DINAMARCA"):
                session.add(
                    ParsedPick(
                        raw_message_id=raw.id,
                        es_apuesta=False,  # patas ya descartadas
                        combinada_id=parent.id,
                        seleccion=sel,
                        fecha_evento=datetime(2026, 9, 24, 10, 0),
                    )
                )
            await session.commit()
            parent_id = parent.id
        report = await garbage.analyze_garbage()
        parent_actions = [a for a in report.actions if a.pick_id == parent_id]
        assert [a.reason for a in parent_actions] == ["combinada_sin_patas"]

    async def test_padre_con_pata_viva_sobrevive(self, db):
        """Padre con al menos una pata abierta no se toca."""
        async with db() as session:
            raw = _raw(datetime(2026, 9, 24, 10, 0), text="combinada")
            session.add(raw)
            await session.flush()
            parent = ParsedPick(
                raw_message_id=raw.id,
                es_apuesta=True,
                es_combinada=True,
                seleccion="Madrid gana + Over 2.5",
                fecha_evento=datetime(2026, 9, 24, 10, 0),
            )
            session.add(parent)
            await session.flush()
            session.add(
                ParsedPick(
                    raw_message_id=raw.id,
                    es_apuesta=True,
                    combinada_id=parent.id,
                    seleccion="Madrid gana",
                    evento="Real Madrid - Girona",
                    fecha_evento=datetime(2026, 9, 24, 10, 0),
                )
            )
            await session.commit()
            parent_id = parent.id
        report = await garbage.analyze_garbage()
        assert not any(
            a.pick_id == parent_id and a.reason == "combinada_sin_patas"
            for a in report.actions
        )

    async def test_pick_resuelto_limpio_no_se_toca(self, db):
        # Pick resuelto de un slip normal (fecha impresa el mismo día,
        # antes del partido): la auditoría no propone nada.
        async with db() as session:
            raw = _raw(datetime(2026, 9, 16, 10, 21), extracted_text=_SLIP_OCR)
            session.add(raw)
            await session.flush()
            pick = _pick(fecha_evento=datetime(2026, 9, 16, 10, 21))
            pick.raw_message_id = raw.id
            pick.acierto = False  # ya liquidado
            pick.verificado_por = "auto"
            session.add(pick)
            await session.commit()
        report = await garbage.analyze_garbage()
        assert report.actions == []

    async def test_pick_resuelto_de_slip_liquidado_se_descarta(self, db):
        """Auditoría post-liquidación: un pick que ya contó como
        acierto pero viene de un slip ganado republicado también sale
        de stats con `es_apuesta=False`."""
        async with db() as session:
            raw = _raw(
                datetime(2026, 9, 21, 18, 15),
                extracted_text=TestSlipLiquidadoYTeaser._WON_SLIP_CHECKS,
            )
            session.add(raw)
            await session.flush()
            pick = _pick(fecha_evento=datetime(2026, 9, 21, 18, 15))
            pick.raw_message_id = raw.id
            pick.acierto = True  # coló como acierto
            pick.verificado_por = "auto"
            session.add(pick)
            await session.commit()
            pick_id = pick.id
        report = await garbage.analyze_garbage()
        assert any(
            a.pick_id == pick_id and a.reason == "slip_liquidado"
            for a in report.actions
        )
