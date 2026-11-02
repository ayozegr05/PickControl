"""Configuración de Alembic para el motor async (asyncpg) del proyecto.

Usa el patrón oficial de Alembic para SQLAlchemy async (ver
https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic),
porque `DATABASE_URL` usa el driver `postgresql+asyncpg://`, que no es
compatible con el `engine_from_config` síncrono que genera `alembic init`
por defecto.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from app.core.config import get_settings

# Importa los modelos para que queden registrados en SQLModel.metadata
# y Alembic pueda detectarlos al autogenerar migraciones.
from app.models.channel import Channel  # noqa: F401
from app.models.device_token import DeviceToken  # noqa: F401
from app.models.informante import Informante  # noqa: F401
from app.models.odds_snapshot import OddsEvent, OddsSnapshot  # noqa: F401
from app.models.parsed_pick import ParsedPick  # noqa: F401
from app.models.pick import Pick  # noqa: F401
from app.models.telegram_raw_message import TelegramRawMessage  # noqa: F401
from app.models.user import User  # noqa: F401

config = context.config

# La URL real viene de Settings (.env), no del alembic.ini versionado,
# para no tener que duplicar/hardcodear la cadena de conexión.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Genera el SQL de las migraciones sin conectarse a la base de datos
    (`alembic upgrade head --sql`)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
