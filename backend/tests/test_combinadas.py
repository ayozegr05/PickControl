"""Tests de combinadas: extracción, persistencia self-FK, liquidación
en conjunto, exclusión de stats y comportamiento de la API.

Modelo: padre `es_combinada=True` + N patas con `combinada_id` (ver ADR
en la migración f1a2b3c4d5e6).
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.informante import Informante
from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage
from app.services.pick_service import (
    calcular_stats_combinadas,
    calcular_stats_parsed,
)
from app.services.results.verifier import settle_combinada
from app.services.telegram.pick_extractor import (
    ExtractedPick,
    _ensure_combinada_shape,
    _rule_extract_combinada,
)
from app.services.telegram.processor import process_incoming_message

# ---------------------------------------------------------------- extractor


class TestExtractCombinada:
    def test_bet_builder_mismo_partido(self):
        # Caso real (id=716): "CREA TU APUESTA" con 5 props del mismo partido.
        text = (
            "CREA TU APUESTA\n"
            "5 pronósticos\n"
            "Osasuna - Levante\n"
            "✔ 2 o más tiros a portería - Ante Budimir\n"
            "✔ Recibe una tarjeta - Unai Elgezabal\n"
            "✔ Total de córners - Más de 9,5\n"
            "✔ Ambos equipos marcan - Sí\n"
            "✔ Total de goles - Menos de 1,5\n"
            "Cuota total 61.00"
        )
        pick = _ensure_combinada_shape(_rule_extract_combinada(text, informante="Test"))
        assert pick.es_apuesta is True
        assert pick.mercado == "combinada"
        assert pick.cuota == 61.0
        assert len(pick.patas) == 5
        # Todas las patas heredan el evento único del boleto.
        assert all(p.evento == "Osasuna - Levante" for p in pick.patas)
        mercados = {p.mercado for p in pick.patas}
        assert "ambos marcan" in mercados
        assert "jugador" in mercados

    def test_acumulador_multi_partido(self):
        text = (
            "COMBINADA DEL DÍA @3.40\n"
            "- Alcaraz gana\n"
            "- Real Madrid gana\n"
            "Cuota 3.40 Stake 2"
        )
        pick = _ensure_combinada_shape(_rule_extract_combinada(text, informante="Test"))
        assert pick.es_apuesta is True
        assert len(pick.patas) == 2
        assert all(p.mercado == "ganador" for p in pick.patas)

    def test_una_sola_pata_degrada_a_simple(self):
        # Falso positivo tipo id=374: marcado como combinada pero con
        # una sola selección real -> NO es combinada.
        pick = ExtractedPick(
            es_apuesta=True,
            seleccion="Brunold gana +7.5 juegos",
            mercado="combinada",
            metodo="llm",
            confianza=0.75,
        )
        pick = _ensure_combinada_shape(pick)
        assert pick.patas == []
        assert pick.mercado != "combinada"

    def test_patas_del_llm_se_normalizan(self):
        pick = ExtractedPick(
            es_apuesta=True,
            seleccion="A gana + B gana",
            mercado="combinada",
            evento="Real Madrid - Barcelona",
            fecha_evento=datetime(2026, 9, 20, 21, 0),
            deporte="fútbol",
            metodo="llm",
            confianza=0.75,
            patas=[
                ExtractedPick(seleccion="A gana"),
                ExtractedPick(seleccion="B gana"),
            ],
        )
        pick = _ensure_combinada_shape(pick)
        assert len(pick.patas) == 2
        # Las patas heredan deporte/evento/fecha del padre y son apuestas.
        for pata in pick.patas:
            assert pata.es_apuesta is True
            assert pata.evento == "Real Madrid - Barcelona"
            assert pata.deporte == "fútbol"
            assert pata.fecha_evento == datetime(2026, 9, 20, 21, 0)

    def test_patas_reconstruidas_desde_seleccion_unida(self):
        # Red de seguridad: el LLM unió con " + " sin rellenar `patas`.
        pick = ExtractedPick(
            es_apuesta=True,
            seleccion="Alcaraz gana + Real Madrid gana",
            mercado="combinada",
            metodo="llm",
            confianza=0.75,
        )
        pick = _ensure_combinada_shape(pick)
        assert len(pick.patas) == 2
        assert pick.patas[0].seleccion == "Alcaraz gana"
        assert pick.patas[0].mercado == "ganador"


# ------------------------------------------------------------- persistencia


@pytest.fixture
def fake_settings(monkeypatch):
    settings = SimpleNamespace(openai_api_key="test-key")
    monkeypatch.setattr(
        "app.services.telegram.processor.get_settings",
        lambda: settings,
    )
    return settings


@pytest.mark.asyncio
class TestProcessorCombinada:
    async def test_crea_padre_y_patas_con_self_fk(
        self, session, fake_settings, monkeypatch
    ):
        extracted = ExtractedPick(
            es_apuesta=True,
            mercado="combinada",
            seleccion="Alcaraz gana + Real Madrid gana",
            cuota=3.4,
            stake=2.0,
            metodo="rule",
            confianza=0.8,
            patas=[
                ExtractedPick(
                    es_apuesta=True,
                    seleccion="Alcaraz gana",
                    mercado="ganador",
                    deporte="tenis",
                ),
                ExtractedPick(
                    es_apuesta=True,
                    seleccion="Real Madrid gana",
                    mercado="ganador",
                    deporte="fútbol",
                ),
            ],
        )
        monkeypatch.setattr(
            "app.services.telegram.processor.extract_pick",
            AsyncMock(return_value=extracted),
        )

        await process_incoming_message(
            session=session,
            channel="Test Channel",
            channel_id=1,
            message_id=10,
            text="COMBINADA: Alcaraz gana + Real Madrid gana",
        )

        parent = (
            (await session.exec(select(ParsedPick).where(ParsedPick.es_combinada)))
            .scalars()
            .one()
        )
        assert parent.mercado == "combinada"
        assert parent.cuota == 3.4
        assert parent.combinada_id is None

        legs = (
            (
                await session.exec(
                    select(ParsedPick)
                    .where(ParsedPick.combinada_id == parent.id)
                    .order_by(ParsedPick.orden)
                )
            )
            .scalars()
            .all()
        )
        assert len(legs) == 2
        assert [leg.orden for leg in legs] == [0, 1]
        assert legs[0].seleccion == "Alcaraz gana"
        assert legs[0].es_combinada is False
        assert legs[0].es_apuesta is True

    async def test_pick_simple_no_dedup_contra_pata(
        self, session, fake_settings, monkeypatch
    ):
        """Un simple futuro con el mismo texto que una pata no debe
        fusionarse con ella: las patas no son candidatas a dedup."""
        extracted = ExtractedPick(
            es_apuesta=True,
            mercado="combinada",
            seleccion="Real Madrid gana + Barça gana",
            metodo="rule",
            confianza=0.8,
            patas=[
                ExtractedPick(es_apuesta=True, seleccion="Real Madrid gana"),
                ExtractedPick(es_apuesta=True, seleccion="Barça gana"),
            ],
        )
        mock = AsyncMock(return_value=extracted)
        monkeypatch.setattr("app.services.telegram.processor.extract_pick", mock)
        await process_incoming_message(
            session=session,
            channel="Test Channel",
            channel_id=1,
            message_id=11,
            text="Combinada",
        )

        # Ahora llega el pick simple "Real Madrid gana": debe crearse
        # como fila propia, no fusionarse con la pata de la combinada.
        simple = ExtractedPick(
            es_apuesta=True,
            seleccion="Real Madrid gana",
            mercado="ganador",
            cuota=1.5,
            stake=3.0,
            metodo="rule",
            confianza=0.9,
        )
        mock.return_value = simple
        await process_incoming_message(
            session=session,
            channel="Test Channel",
            channel_id=1,
            message_id=12,
            text="Real Madrid gana cuota 1.5 stake 3",
        )

        simples = (
            (
                await session.exec(
                    select(ParsedPick).where(
                        ParsedPick.seleccion == "Real Madrid gana",
                        ParsedPick.combinada_id == None,  # noqa: E711
                        ParsedPick.es_combinada == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(simples) == 1
        assert simples[0].cuota == 1.5


# --------------------------------------------------------------- liquidación


async def _crear_combinada(session, legs: list[ParsedPick]) -> ParsedPick:
    """Persiste un padre combinada con las patas dadas (sin resultado)."""
    raw = TelegramRawMessage(
        channel_id=1,
        message_id=1,
        channel_name="Test",
        text="combinada",
        processed=True,
    )
    session.add(raw)
    await session.flush()
    parent = ParsedPick(
        raw_message_id=raw.id,
        es_apuesta=True,
        es_combinada=True,
        mercado="combinada",
        seleccion=" + ".join(leg.seleccion for leg in legs),
        cuota=10.0,
        stake=1.0,
    )
    session.add(parent)
    await session.flush()
    for orden, leg in enumerate(legs):
        leg.raw_message_id = raw.id
        leg.combinada_id = parent.id
        leg.orden = orden
        leg.es_apuesta = True
        session.add(leg)
    await session.flush()
    return parent


def _leg(seleccion: str, **kwargs) -> ParsedPick:
    return ParsedPick(es_apuesta=True, seleccion=seleccion, **kwargs)


@pytest.mark.asyncio
class TestSettleCombinada:
    async def test_todas_verdes_acierto(self, session):
        parent = await _crear_combinada(
            session,
            [_leg("A", acierto=True), _leg("B", acierto=True)],
        )
        assert await settle_combinada(session, parent) is True
        assert parent.acierto is True
        assert parent.anulada is False
        # Sin anuladas, la cuota efectiva es la total declarada.
        assert parent.cuota_efectiva == parent.cuota
        assert parent.verificado_por == "auto"

    async def test_una_pata_roja_tumba_la_combinada(self, session):
        parent = await _crear_combinada(
            session,
            [
                _leg("A", acierto=True),
                _leg("B", acierto=False),
                _leg("C"),  # pendiente: da igual, ya está muerta
            ],
        )
        assert await settle_combinada(session, parent) is True
        assert parent.acierto is False
        assert parent.anulada is False

    async def test_patas_pendientes_padre_pendiente(self, session):
        parent = await _crear_combinada(
            session,
            [_leg("A", acierto=True), _leg("B")],
        )
        # No hay cambio: el padre ya estaba pendiente.
        assert await settle_combinada(session, parent) is False
        assert parent.acierto is None
        assert parent.anulada is False

    async def test_pata_anulada_se_excluye(self, session):
        parent = await _crear_combinada(
            session,
            [
                _leg("A", acierto=True),
                _leg("B", anulada=True),
                _leg("C", acierto=True),
            ],
        )
        assert await settle_combinada(session, parent) is True
        assert parent.acierto is True

    async def test_todas_anuladas_combinada_anulada(self, session):
        parent = await _crear_combinada(
            session,
            [_leg("A", anulada=True), _leg("B", anulada=True)],
        )
        assert await settle_combinada(session, parent) is True
        assert parent.acierto is None
        assert parent.anulada is True

    async def test_cuota_efectiva_se_recalcula_con_cuotas_por_pata(self, session):
        parent = await _crear_combinada(
            session,
            [
                _leg("A", acierto=True, cuota=2.0),
                _leg("B", anulada=True, cuota=3.0),
                _leg("C", acierto=True, cuota=2.5),
            ],
        )
        assert await settle_combinada(session, parent) is True
        assert parent.acierto is True
        # 2.0 * 2.5 = 5.0 (la anulada se excluye del producto).
        assert parent.cuota_efectiva == 5.0

    async def test_cuota_efectiva_none_sin_cuotas_por_pata(self, session):
        parent = await _crear_combinada(
            session,
            [
                _leg("A", acierto=True),
                _leg("B", anulada=True),  # sin cuota -> no recalculable
            ],
        )
        assert await settle_combinada(session, parent) is True
        assert parent.acierto is True
        # No se inventa la ganancia.
        assert parent.cuota_efectiva is None

    async def test_override_manual_del_padre_se_respeta(self, session):
        parent = await _crear_combinada(
            session,
            [_leg("A", acierto=True), _leg("B", acierto=True)],
        )
        parent.acierto = False
        parent.verificado_por = "manual"
        session.add(parent)
        await session.flush()

        assert await settle_combinada(session, parent) is False
        assert parent.acierto is False  # no se pisa el override

    async def test_reabre_padre_auto_si_pata_vuelve_a_pendiente(self, session):
        parent = await _crear_combinada(
            session,
            [_leg("A", acierto=True), _leg("B", acierto=True)],
        )
        await settle_combinada(session, parent)
        assert parent.acierto is True

        # Corrección manual: la pata B vuelve a pendiente.
        legs = (
            (
                await session.exec(
                    select(ParsedPick).where(ParsedPick.combinada_id == parent.id)
                )
            )
            .scalars()
            .all()
        )
        legs[1].acierto = None
        legs[1].verificado_por = "manual"
        session.add(legs[1])
        await session.flush()

        assert await settle_combinada(session, parent) is True
        assert parent.acierto is None
        assert parent.anulada is False
        assert parent.verificado_por is None

    async def test_sin_patas_no_liquida(self, session):
        raw = TelegramRawMessage(
            channel_id=1, message_id=9, channel_name="T", text="x", processed=True
        )
        session.add(raw)
        await session.flush()
        parent = ParsedPick(
            raw_message_id=raw.id,
            es_apuesta=True,
            es_combinada=True,
            mercado="combinada",
        )
        session.add(parent)
        await session.flush()
        assert await settle_combinada(session, parent) is False


# --------------------------------------------------------------------- stats


class TestStatsCombinadas:
    def _combinada(self) -> tuple[ParsedPick, list[ParsedPick]]:
        parent = ParsedPick(
            id=100,
            es_apuesta=True,
            es_combinada=True,
            mercado="combinada",
            cuota=4.0,
            cuota_efectiva=4.0,
            stake=1.0,
            acierto=True,
        )
        legs = [
            ParsedPick(
                id=101, es_apuesta=True, combinada_id=100, orden=0, acierto=True
            ),
            ParsedPick(
                id=102, es_apuesta=True, combinada_id=100, orden=1, acierto=True
            ),
        ]
        return parent, legs

    def test_stats_simples_excluyen_padre_y_patas(self):
        simple = ParsedPick(id=1, es_apuesta=True, cuota=2.0, stake=1.0, acierto=True)
        parent, legs = self._combinada()
        stats = calcular_stats_parsed([simple, parent, *legs])
        assert stats.total_apuestas == 1  # solo el simple
        assert stats.ganancias == 1.0  # 1 * (2.0 - 1)

    def test_stats_combinadas_solo_padres(self):
        parent, legs = self._combinada()
        simple = ParsedPick(id=1, es_apuesta=True, cuota=2.0, stake=1.0, acierto=True)
        stats = calcular_stats_combinadas([simple, parent, *legs])
        assert stats.total_apuestas == 1  # solo el padre
        assert stats.total_aciertos == 1
        # Usa cuota_efectiva: 1 * (4.0 - 1) = 3.0
        assert stats.ganancias == 3.0


# ----------------------------------------------------------------------- API


async def _seed_combinada(session, informante: Informante) -> ParsedPick:
    """Padre + 2 patas persistidos contra un raw de un canal real."""
    raw = TelegramRawMessage(
        channel_id=1,
        message_id=99,
        channel_name=informante.nombre,
        text="combinada",
        processed=True,
    )
    session.add(raw)
    await session.flush()
    parent = ParsedPick(
        raw_message_id=raw.id,
        informante_id=informante.id,
        informante=informante.nombre,
        es_apuesta=True,
        es_combinada=True,
        mercado="combinada",
        seleccion="A gana + B gana",
        cuota=4.0,
        stake=1.0,
        fecha_evento=datetime(2026, 9, 20, 21, 0),
    )
    session.add(parent)
    await session.flush()
    for orden, sel in enumerate(("A gana", "B gana")):
        session.add(
            ParsedPick(
                raw_message_id=raw.id,
                informante_id=informante.id,
                informante=informante.nombre,
                combinada_id=parent.id,
                orden=orden,
                es_apuesta=True,
                seleccion=sel,
                mercado="ganador",
            )
        )
    await session.commit()
    return parent


@pytest.mark.asyncio
class TestApiCombinadas:
    async def test_listado_excluye_patas_y_anida(
        self, client, session, auth_headers, crear_canal
    ):
        canal = await crear_canal("CanalCombo")
        parent = await _seed_combinada(session, canal)

        resp = await client.get(
            "/api/v1/telegram/parsed-picks",
            params={"informante_id": canal.id, "solo_apuestas": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        picks = resp.json()
        # Nivel superior: solo el padre, nunca las patas sueltas.
        assert len(picks) == 1
        assert picks[0]["id"] == parent.id
        assert picks[0]["es_combinada"] is True
        assert len(picks[0]["patas"]) == 2
        assert picks[0]["patas"][0]["seleccion"] == "A gana"

    async def test_patch_pata_reliquida_padre(
        self, client, session, auth_headers, crear_canal
    ):
        canal = await crear_canal("CanalCombo2")
        parent = await _seed_combinada(session, canal)
        legs = (
            (
                await session.exec(
                    select(ParsedPick).where(ParsedPick.combinada_id == parent.id)
                )
            )
            .scalars()
            .all()
        )

        # Acertar las dos patas a mano -> el padre se liquida en verde.
        for leg in legs:
            resp = await client.patch(
                f"/api/v1/telegram/parsed-picks/{leg.id}",
                json={"acierto": True},
                headers=auth_headers,
            )
            assert resp.status_code == 200

        await session.refresh(parent)
        assert parent.acierto is True
        assert parent.verificado_por == "auto"

    async def test_patch_anula_pata_recalcula_padre(
        self, client, session, auth_headers, crear_canal
    ):
        canal = await crear_canal("CanalCombo3")
        parent = await _seed_combinada(session, canal)
        legs = (
            (
                await session.exec(
                    select(ParsedPick).where(ParsedPick.combinada_id == parent.id)
                )
            )
            .scalars()
            .all()
        )

        await client.patch(
            f"/api/v1/telegram/parsed-picks/{legs[0].id}",
            json={"acierto": True},
            headers=auth_headers,
        )
        resp = await client.patch(
            f"/api/v1/telegram/parsed-picks/{legs[1].id}",
            json={"anulada": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        await session.refresh(parent)
        # Una verde + una anulada -> combinada verde (anulada excluida).
        assert parent.acierto is True
        assert parent.anulada is False

    async def test_jugar_pata_crea_apuesta_sobre_el_padre(
        self, client, session, auth_headers, crear_canal
    ):
        canal = await crear_canal("CanalCombo4")
        parent = await _seed_combinada(session, canal)
        leg = (
            (
                await session.exec(
                    select(ParsedPick).where(ParsedPick.combinada_id == parent.id)
                )
            )
            .scalars()
            .first()
        )

        resp = await client.post(
            f"/api/v1/telegram/parsed-picks/{leg.id}/jugar",
            json={"cantidad_apostada": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # La apuesta del usuario enlaza con el PADRE, no con la pata.
        assert resp.json()["parsed_pick_id"] == parent.id

    async def test_informante_devuelve_combinadas_anidadas(
        self, client, session, auth_headers, crear_canal
    ):
        canal = await crear_canal("CanalCombo5")
        await _seed_combinada(session, canal)

        resp = await client.get(
            f"/api/v1/informante/{canal.nombre}", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["combinadas_total"] == 1
        padre = next(p for p in data["parsed_picks"] if p["es_combinada"])
        assert len(padre["patas"]) == 2
        # Las patas no salen como filas sueltas: solo está el padre.
        assert len(data["parsed_picks"]) == 1
