"""Tests de integración de `app/api/deps.py` (`get_current_user`)."""

from httpx import AsyncClient
from sqlmodel import select

from app.models.user import User

PICK = {
    "apuesta": "Test",
    "informante": "Tipster",
    "tipo_de_apuesta": "1x2",
    "casa": "Bet365",
    "cantidad_apostada": 10,
    "cuota": 2.0,
}


class TestAuthDependency:
    async def test_endpoint_sin_token_falla(self, client: AsyncClient):
        response = await client.post("/api/v1/apuestas", json=PICK)
        assert response.status_code == 401

    async def test_endpoint_con_token_invalido_falla(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/apuestas",
            json=PICK,
            headers={"Authorization": "Bearer invalidtoken"},
        )
        assert response.status_code == 401

    async def test_endpoint_con_token_sin_sub_falla(self, client: AsyncClient):
        from app.core.security import create_access_token

        token = create_access_token({"email": "a@b.com"})  # sin "sub"
        response = await client.post(
            "/api/v1/apuestas",
            json=PICK,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401

    async def test_usuario_inactivo_no_puede_crear(self, client: AsyncClient, session):
        # Registramos un usuario.
        payload = {
            "name": "Inactivo",
            "email": "inactivo@test.com",
            "password": "pass123",
        }
        await client.post("/api/v1/auth/register", json=payload)

        # Lo localizamos en la BD de test y lo desactivamos.
        user = (
            await session.exec(select(User).where(User.email == payload["email"]))
        ).first()
        user.is_active = False
        session.add(user)
        await session.commit()

        # Aunque consiguiéramos un token (login estaría bloqueado por is_active),
        # forjamos uno directo para probar la dependencia.
        from app.core.security import create_access_token

        token = create_access_token({"sub": str(user.id), "email": user.email})
        response = await client.post(
            "/api/v1/apuestas",
            json=PICK,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
