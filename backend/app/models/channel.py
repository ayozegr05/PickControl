"""Canales de Telegram monitorizados, configurables desde la app.

Sustituye a la lista estática `TELEGRAM_TARGET_CHANNEL` del `.env`:
el listener y el catch-up leen los canales activos de esta tabla, así
añadir/quitar canales no requiere redeploy ni reinicio.

- `target`: lo que introdujo el usuario (username, enlace t.me o id).
- `channel_id`: id marcado de Telethon (`-100<id>`), se rellena la
  primera vez que el cliente resuelve el canal. Es el formato que usa
  `event.chat_id` en los handlers y `channel_id` en los raws.
- `activo=False` deja de escuchar el canal pero conserva su historial:
  los raws y picks ya guardados nunca se borran (auditoría).
"""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from app.core.dates import utc_now


class Channel(SQLModel, table=True):
    """Canal de Telegram monitorizado por el listener."""

    __tablename__ = "channels"

    id: int | None = Field(default=None, primary_key=True)
    target: str = Field(max_length=255, unique=True)
    channel_id: int | None = Field(
        default=None, sa_column=sa.Column(sa.BigInteger, index=True)
    )
    name: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    activo: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)
