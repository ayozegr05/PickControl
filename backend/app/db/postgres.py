"""Conexión asíncrona a PostgreSQL usando SQLModel + asyncpg.

Sustituye a `DbMongo/db.js` (Mongoose) del backend Node original.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url, echo=settings.node_env == "development"
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Crea las tablas a partir de los modelos, sin pasar por Alembic.

    Solo se usa en tests (contra SQLite en memoria). El esquema real en
    desarrollo/producción se gestiona con `alembic upgrade head`
    (ver `app/db/migrations/`), nunca con esta función.
    """
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependencia de FastAPI para inyectar una sesión de base de datos."""
    async with AsyncSessionLocal() as session:
        yield session
