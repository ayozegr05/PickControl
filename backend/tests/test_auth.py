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


class TestRefresh:
    """El login biométrico canjea refresh tokens en /refresh en vez de
    reenviar la contraseña guardada en el móvil. Cada canje ROTA el
    token (el viejo muere) y un reuso implica robo → se revoca toda
    la familia de sesión."""

    async def _register(self, client: AsyncClient) -> dict:
        response = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        assert response.status_code == 201
        return response.json()

    async def test_register_devuelve_refresh_token(self, client: AsyncClient):
        data = await self._register(client)
        assert data["refresh_token"]

    async def test_refresh_canjea_por_token_nuevo(self, client: AsyncClient):
        refresh = (await self._register(client))["refresh_token"]

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["token"]
        assert data["refresh_token"] != refresh  # rotado
        assert data["user"]["email"] == REGISTER_PAYLOAD["email"]

    async def test_refresh_rota_el_token_anterior(self, client: AsyncClient):
        """El token consumido por un canje queda muerto."""
        t1 = (await self._register(client))["refresh_token"]
        rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": t1})
        assert rotated.status_code == 200

        reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": t1})
        assert reuse.status_code == 401

    async def test_refresh_reuso_revoca_la_familia(self, client: AsyncClient):
        """Si un token YA rotado vuelve a presentarse, es robo probable:
        se revoca la sesión entera — el refresh nuevo (el que tiene el
        usuario legítimo) también muere."""
        t1 = (await self._register(client))["refresh_token"]
        rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": t1})
        t2 = rotated.json()["refresh_token"]

        # El "ladrón" reusa el token viejo → familia entera revocada.
        reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": t1})
        assert reuse.status_code == 401

        legit = await client.post("/api/v1/auth/refresh", json={"refresh_token": t2})
        assert legit.status_code == 401

    async def test_refresh_jti_desconocido_rechazado(self, client: AsyncClient):
        """Un JWT bien firmado con type=refresh pero cuyo jti no está
        en la tabla no vale: el servidor solo confía en sesiones que
        emitió él."""
        from jose import jwt as jose_jwt

        from app.core.config import get_settings
        from app.core.dates import utc_now

        settings = get_settings()
        fake = jose_jwt.encode(
            {
                "sub": "1",
                "type": "refresh",
                "jti": "jti-inventado",
                "fam": "fam-inventada",
                "exp": utc_now().timestamp() + 3600,
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": fake}
        )
        assert response.status_code == 401

    async def test_refresh_token_acceso_rechazado(self, client: AsyncClient):
        """Un access token NO puede usarse como refresh token."""
        access = (await self._register(client))["token"]

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": access}
        )
        assert response.status_code == 401

    async def test_refresh_token_basura_rechazado(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "no-es-un-jwt"}
        )
        assert response.status_code == 401


class TestLogout:
    """POST /auth/logout revoca la familia de sesión del refresh token
    presentado — cerrar sesión mata la credencial del dispositivo."""

    async def test_logout_revoca_la_sesion(self, client: AsyncClient):
        registered = await client.post("/api/v1/auth/register", json=REGISTER_PAYLOAD)
        refresh = registered.json()["refresh_token"]

        logout = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": refresh}
        )
        assert logout.status_code == 200

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh}
        )
        assert response.status_code == 401

    async def test_logout_token_basura_rechazado(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": "no-es-un-jwt"}
        )
        assert response.status_code == 401
