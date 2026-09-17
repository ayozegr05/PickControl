"""Tests de POST /telegram/parsed-picks/{id}/jugar ("Yo también la jugué")."""

from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.parsed_pick import ParsedPick
from app.models.telegram_raw_message import TelegramRawMessage


async def _seed_parsed_pick(session: AsyncSession, informante_id: int) -> ParsedPick:
    raw = TelegramRawMessage(
        channel_id=1,
        message_id=10,
        channel_name="ElTipster",
        text="Alcaraz gana @1.90 stake 2",
    )
    session.add(raw)
    await session.flush()
    pick = ParsedPick(
        raw_message_id=raw.id,
        informante_id=informante_id,
        es_apuesta=True,
        apuesta="Alcaraz gana",
        deporte="tenis",
        evento="Alcaraz - Sinner",
        mercado="ganador",
        seleccion="Alcaraz gana",
        cuota=1.90,
        stake=2.0,
        casa="Bet365",
    )
    session.add(pick)
    await session.commit()
    await session.refresh(pick)
    return pick


class TestJugarPick:
    async def test_crea_apuesta_copiando_el_pick(
        self, client: AsyncClient, session: AsyncSession, auth_headers, crear_canal
    ):
        canal = await crear_canal("ElTipster")
        parsed = await _seed_parsed_pick(session, canal.id)

        response = await client.post(
            f"/api/v1/telegram/parsed-picks/{parsed.id}/jugar",
            json={"cantidad_apostada": 5.0},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["parsed_pick_id"] == parsed.id
        assert data["source"] == "telegram"
        assert data["acierto"] == "Pending"
        # cuota y casa se heredan del tipster si no se envían
        assert data["cuota"] == 1.90
        assert data["casa"] == "Bet365"
        assert data["cantidad_apostada"] == 5.0
        assert data["informante"] == "ElTipster"
        # la apuesta aparece en el listado del usuario
        listado = await client.get("/api/v1/apuestas", headers=auth_headers)
        assert any(p["id"] == data["id"] for p in listado.json())

    async def test_override_cuota_y_casa_reales(
        self, client: AsyncClient, session: AsyncSession, auth_headers, crear_canal
    ):
        """La cuota que consiguió el usuario puede diferir de la del tipster."""
        canal = await crear_canal("ElTipster")
        parsed = await _seed_parsed_pick(session, canal.id)

        response = await client.post(
            f"/api/v1/telegram/parsed-picks/{parsed.id}/jugar",
            json={"cantidad_apostada": 10.0, "cuota": 1.80, "casa": "Marathon"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["cuota"] == 1.80
        assert data["casa"] == "Marathon"

    async def test_idempotente_no_duplica(
        self, client: AsyncClient, session: AsyncSession, auth_headers, crear_canal
    ):
        """Un doble tap devuelve la apuesta ya creada, no un duplicado."""
        canal = await crear_canal("ElTipster")
        parsed = await _seed_parsed_pick(session, canal.id)

        first = await client.post(
            f"/api/v1/telegram/parsed-picks/{parsed.id}/jugar",
            json={"cantidad_apostada": 5.0},
            headers=auth_headers,
        )
        second = await client.post(
            f"/api/v1/telegram/parsed-picks/{parsed.id}/jugar",
            json={"cantidad_apostada": 5.0},
            headers=auth_headers,
        )
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

        listado = await client.get("/api/v1/apuestas", headers=auth_headers)
        assert len(listado.json()) == 1

    async def test_pick_inexistente_404(self, client: AsyncClient, auth_headers):
        response = await client.post(
            "/api/v1/telegram/parsed-picks/9999/jugar",
            json={"cantidad_apostada": 5.0},
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_mensaje_no_apuesta_404(
        self, client: AsyncClient, session: AsyncSession, auth_headers, crear_canal
    ):
        canal = await crear_canal("ElTipster")
        parsed = await _seed_parsed_pick(session, canal.id)
        parsed.es_apuesta = False
        session.add(parsed)
        await session.commit()

        response = await client.post(
            f"/api/v1/telegram/parsed-picks/{parsed.id}/jugar",
            json={"cantidad_apostada": 5.0},
            headers=auth_headers,
        )
        assert response.status_code == 404

    async def test_sin_token_401(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/telegram/parsed-picks/1/jugar",
            json={"cantidad_apostada": 5.0},
        )
        assert response.status_code == 401

    async def test_cantidad_invalida_422(
        self, client: AsyncClient, session: AsyncSession, auth_headers, crear_canal
    ):
        canal = await crear_canal("ElTipster")
        parsed = await _seed_parsed_pick(session, canal.id)

        response = await client.post(
            f"/api/v1/telegram/parsed-picks/{parsed.id}/jugar",
            json={"cantidad_apostada": 0},
            headers=auth_headers,
        )
        assert response.status_code == 422
