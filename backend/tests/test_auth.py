"""Tests de integración de los endpoints de autenticación (app/api/v1/auth.py).

A diferencia de test_pick_service.py, estos SÍ pasan por la app FastAPI
real (vía el fixture `client` de conftest.py) y por una base de datos,
aunque sea la SQLite en memoria de test, nunca la de desarrollo/producción.
"""

from httpx import AsyncClient

REGISTER_PAYLOAD = {
    "name": "Ana",
    "email": "ana@example.com",
    "password": "supersecreta",
}


class TestRegister:
    async def test_register_crea_usuario_y_devuelve_token(self, client: AsyncClient):
        response = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)

        assert response.status_code == 201
        data = response.json()
        assert data["token"]
        assert data["user"]["email"] == "ana@example.com"
        assert data["user"]["name"] == "Ana"
        # El hash de la contraseña nunca debe llegar al cliente.
        assert "password" not in data["user"]
        assert "password_hash" not in data["user"]

    async def test_register_email_duplicado_falla(self, client: AsyncClient):
        first = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert first.status_code == 201

        second = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert second.status_code == 400
        assert "ya está registrado" in second.json()["detail"]

    async def test_register_password_demasiado_corta_falla_validacion(
        self, client: AsyncClient
    ):
        response = await client.post(
            "/api/v1/auth/register",
            json={"name": "Ana", "email": "ana@example.com", "password": "123"},
        )
        assert response.status_code == 422


class TestLogin:
    async def test_login_con_credenciales_correctas(self, client: AsyncClient):
        await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)

        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": REGISTER_PAYLOAD["email"],
                "password": REGISTER_PAYLOAD["password"],
            },
        )
        assert response.status_code == 200
        assert response.json()["token"]

    async def test_login_con_password_incorrecta_falla(self, client: AsyncClient):
        await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": REGISTER_PAYLOAD["email"], "password": "otra-contraseña"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Credenciales inválidas"

    async def test_login_email_inexistente_falla(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "no-existe@example.com", "password": "cualquiera"},
        )
        assert response.status_code == 401
