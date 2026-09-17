"""Tests de GET /analisis (auditoría global tipster vs usuario)."""

from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.parsed_pick import ParsedPick
from app.models.pick import Acierto, Pick, PickSource
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User


async def _seed(
    session: AsyncSession, informante_id: int, usuario_id: int
) -> ParsedPick:
    raw = TelegramRawMessage(
        channel_id=1, message_id=1, channel_name="ElTipster", text="pick"
    )
    session.add(raw)
    await session.flush()

    # Pick del tipster resuelto: acierto a cuota 2.0, stake 1.
    parsed = ParsedPick(
        raw_message_id=raw.id,
        informante_id=informante_id,
        es_apuesta=True,
        apuesta="Alcaraz gana",
        deporte="tenis",
        mercado="ganador",
        seleccion="Alcaraz gana",
        cuota=2.0,
        stake=1.0,
        acierto=True,
    )
    session.add(parsed)
    await session.flush()

    # El usuario la jugó a cuota 1.8 (peor que la publicada).
    session.add(
        Pick(
            apuesta="Alcaraz gana",
            tipo_de_apuesta="ganador",
            casa="Bet365",
            acierto=Acierto.TRUE,
            cantidad_apostada=10.0,
            cuota=1.8,
            source=PickSource.TELEGRAM,
            parsed_pick_id=parsed.id,
            usuario_id=usuario_id,
            informante_id=informante_id,
        )
    )
    await session.commit()
    await session.refresh(parsed)
    return parsed


class TestAnalisisGlobal:
    async def test_estructura_basica_vacia(self, client: AsyncClient):
        response = await client.get("/api/v1/analisis")
        assert response.status_code == 200
        data = response.json()
        assert data["canales"] == []
        assert data["deportes"] == []
        assert data["totales_tipster"]["total"] == 0

    async def test_comparativa_canal_y_cuotas(
        self, client: AsyncClient, session: AsyncSession, crear_canal
    ):
        canal = await crear_canal("ElTipster")
        # usuario de test (registrado por la API para tener id real)
        await client.post(
            "/api/v1/auth/register",
            json={
                "name": "T",
                "email": "a@b.com",
                "password": "password123",
            },
        )
        usuario = (
            await session.exec(select(User).where(User.email == "a@b.com"))
        ).first()
        await _seed(session, canal.id, usuario.id)

        data = (await client.get("/api/v1/analisis")).json()

        assert len(data["canales"]) == 1
        c = data["canales"][0]
        assert c["informante"] == "ElTipster"
        # tipster: 1 pick, acierto, yield 100% (ganó 1u con 1u)
        assert c["tipster"]["total"] == 1
        assert c["tipster"]["aciertos"] == 1
        assert c["tipster"]["yield_pct"] == 100.0
        # yo: 1 apuesta de 10 a cuota 1.8 -> ganancia 8, yield 80%
        assert c["yo"]["total"] == 1
        assert c["yo"]["ganancias"] == 8.0
        assert c["yo"]["yield_pct"] == 80.0
        assert c["jugadas"] == 1
        assert c["cuota_media_tipster"] == 2.0
        assert c["cuota_media_mia"] == 1.8

        # deporte tenis presente en ambos lados
        tenis = next(d for d in data["deportes"] if d["deporte"] == "tenis")
        assert tenis["tipster"]["total"] == 1
        assert tenis["yo"]["total"] == 1

        assert data["totales_tipster"]["total"] == 1
        assert data["totales_yo"]["total"] == 1
