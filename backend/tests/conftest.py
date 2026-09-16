"""Fixtures compartidas para toda la suite de tests.

Importante: los tests NUNCA tocan la base de datos de desarrollo/producción
configurada en `.env`. Usan una base SQLite en memoria, creada y destruida
en cada test, sobreescribiendo la dependencia `get_session` de FastAPI.
"""

import os

# JWT_SECRET es obligatorio en Settings (app/core/config.py) y no tiene
# valor por defecto. Hay que fijarlo ANTES de importar nada de `app`,
# porque `app.core.security` llama a `get_settings()` al importarse.
os.environ.setdefault("JWT_SECRET", "test-secret-key-solo-para-pytest")
# Desactiva el rate limiting durante los tests.
os.environ.setdefault("NODE_ENV", "test")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.postgres import get_session
from app.main import app
from app.models.informante import Informante


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Sesión de base de datos SQLite en memoria, con el esquema recién creado."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async_session_factory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_factory() as db_session:
        yield db_session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncClient:
    """Cliente HTTP async contra la app FastAPI real, con la BD sobreescrita."""

    async def _override_get_session():
        yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Registra un usuario de test y devuelve cabeceras `Authorization: Bearer`.

    Cada test que lo use arranca con una BD limpia, así que el email no
    colisiona nunca con el de otro test.
    """
    payload = {
        "name": "Tester",
        "email": "tester@example.com",
        "password": "password123",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def crear_canal(session: AsyncSession):
    """Fábrica de informantes-canal de Telegram para los tests.

    POST /apuestas ya no crea informantes: la apuesta manual solo puede
    vincularse a un canal real, así que los tests que crean apuestas
    deben sembrar el canal antes.
    """

    async def _crear(nombre: str) -> Informante:
        informante = Informante(nombre=nombre, es_canal_telegram=True)
        session.add(informante)
        await session.commit()
        await session.refresh(informante)
        return informante

    return _crear
