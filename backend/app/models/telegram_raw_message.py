"""Modelo para guardar mensajes crudos recibidos desde Telegram.

Estos mensajes se almacenan antes del procesado por LLM. El flujo
previsto es: escucha -> guarda crudo -> procesa con LLM -> genera pick.
"""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from app.core.dates import utc_now


class TelegramRawMessage(SQLModel, table=True):
    """Mensaje crudo de un canal de Telegram."""

    __tablename__ = "telegram_raw_messages"

    id: int | None = Field(default=None, primary_key=True)
    channel_id: int = Field(
        sa_column=sa.Column(sa.BigInteger, index=True, nullable=False)
    )
    message_id: int = Field(
        sa_column=sa.Column(sa.BigInteger, index=True, nullable=False)
    )
    channel_name: str = Field(max_length=255)
    text: str = Field(sa_column=sa.Column(sa.Text, nullable=False))
    # Si el mensaje contiene una imagen, ruta del fichero descargado.
    media_path: str | None = Field(default=None, max_length=500)
    # Texto extraído de la imagen con OCR (p. ej. OpenAI).
    extracted_text: str | None = Field(default=None, sa_column=sa.Column(sa.Text))
    received_at: datetime = Field(default_factory=utc_now)
    processed: bool = Field(default=False)
    informante_id: int | None = Field(
        default=None, foreign_key="informantes.id", index=True
    )
