"""Tests de integración del CRUD de apuestas (`app/api/v1/picks.py`)."""

from httpx import AsyncClient

PICK_CREATE = {
    "apuesta": "Barcelona gana la Liga",
    "informante": "ElTipster",
    "tipo_de_apuesta": "1x2",
    "casa": "Bet365",
    "cantidad_apostada": 10.0,
    "cuota": 2.5,
}


class TestListarPicks:
    async def test_listar_sin_autenticacion(self, client: AsyncClient):
        response = await client.get("/api/v1/apuestas")
        assert response.status_code == 200
        assert response.json() == []

    async def test_listar_despues_de_crear(
        self, client: AsyncClient, auth_headers, crear_canal
    ):
        await crear_canal(PICK_CREATE["informante"])
        create = await client.post(
            "/api/v1/apuestas", json=PICK_CREATE, headers=auth_headers
        )
        assert create.status_code == 201

        response = await client.get("/api/v1/apuestas")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["apuesta"] == PICK_CREATE["apuesta"]
        assert data[0]["ganancia"] == 0.0  # pending


class TestCrearPick:
    async def test_crear_con_token_valido(
        self, client: AsyncClient, auth_headers, crear_canal
    ):
        await crear_canal(PICK_CREATE["informante"])
        response = await client.post(
            "/api/v1/apuestas", json=PICK_CREATE, headers=auth_headers
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] is not None
        assert data["informante"] == "ElTipster"
        assert data["ganancia"] == 0.0
        # source por defecto
        assert data["source"] == "manual"

    async def test_crear_sin_token_falla(self, client: AsyncClient):
        response = await client.post("/api/v1/apuestas", json=PICK_CREATE)
        assert response.status_code == 401

    async def test_crear_con_informante_inexistente_falla(
        self, client: AsyncClient, auth_headers
    ):
        """Las apuestas manuales solo pueden vincularse a canales reales:
        un nombre libre que no existe como canal devuelve 404."""
        response = await client.post(
            "/api/v1/apuestas", json=PICK_CREATE, headers=auth_headers
        )
        assert response.status_code == 404

    async def test_crear_con_informante_no_canal_falla(
        self, client: AsyncClient, auth_headers, session
    ):
        """Un informante existente pero que NO es canal de Telegram
        (p. ej. creado a mano en otra época) tampoco es válido."""
        from app.models.informante import Informante

        session.add(Informante(nombre="ElTipster", es_canal_telegram=False))
        await session.commit()

        response = await client.post(
            "/api/v1/apuestas", json=PICK_CREATE, headers=auth_headers
        )
        assert response.status_code == 404


class TestActualizarPick:
    async def test_actualizar_propia_apuesta(
        self, client: AsyncClient, auth_headers, crear_canal
    ):
        await crear_canal(PICK_CREATE["informante"])
        create = await client.post(
            "/api/v1/apuestas", json=PICK_CREATE, headers=auth_headers
        )
        pick_id = create.json()["id"]

        update = await client.put(
            f"/api/v1/apuesta/{pick_id}",
            json={"acierto": "True"},
            headers=auth_headers,
        )
        assert update.status_code == 200
        data = update.json()
        assert data["acierto"] == "True"
        # stake 10, cuota 2.5 -> ganancia neta 15.0
        assert data["ganancia"] == 15.0

    async def test_actualizar_apuesta_ajena_prohibido(
        self, client: AsyncClient, auth_headers, crear_canal
    ):
        await crear_canal(PICK_CREATE["informante"])
        # Usuario A crea una apuesta.
        create = await client.post(
            "/api/v1/apuestas", json=PICK_CREATE, headers=auth_headers
        )
        pick_id = create.json()["id"]

        # Usuario B se registra y autentica.
        payload_b = {"name": "Otro", "email": "otro@example.com", "password": "otra123"}
        await client.post("/api/v1/auth/register", json=payload_b)
        login_b = await client.post(
            "/api/v1/auth/login",
            json={
                "email": payload_b["email"],
                "password": payload_b["password"],
            },
        )
        headers_b = {"Authorization": f"Bearer {login_b.json()['token']}"}

        update = await client.put(
            f"/api/v1/apuesta/{pick_id}",
            json={"acierto": "True"},
            headers=headers_b,
        )
        assert update.status_code == 403

    async def test_actualizar_pick_inexistente(self, client: AsyncClient, auth_headers):
        response = await client.put(
            "/api/v1/apuesta/9999",
            json={"acierto": "True"},
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestEliminarPick:
    async def test_eliminar_propia_apuesta(
        self, client: AsyncClient, auth_headers, crear_canal
    ):
        await crear_canal(PICK_CREATE["informante"])
        create = await client.post(
            "/api/v1/apuestas", json=PICK_CREATE, headers=auth_headers
        )
        pick_id = create.json()["id"]

        delete = await client.delete(
            f"/api/v1/apuestas/{pick_id}", headers=auth_headers
        )
        assert delete.status_code == 204

        listar = await client.get("/api/v1/apuestas")
        assert listar.json() == []

    async def test_eliminar_pick_inexistente(self, client: AsyncClient, auth_headers):
        response = await client.delete("/api/v1/apuestas/9999", headers=auth_headers)
        assert response.status_code == 404
