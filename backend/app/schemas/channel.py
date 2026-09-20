"""Schemas de los canales de Telegram monitorizados."""

from datetime import datetime

from pydantic import BaseModel, Field


class ChannelRead(BaseModel):
    """Canal configurado en la tabla `channels`."""

    id: int
    target: str
    channel_id: int | None
    name: str | None
    username: str | None
    activo: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ChannelCreate(BaseModel):
    """Alta de canal: enlace t.me, @username o id numérico."""

    target: str = Field(min_length=1, max_length=255)


class ChannelUpdate(BaseModel):
    """Activar/desactivar la monitorización de un canal."""

    activo: bool


class AvailableChannelRead(BaseModel):
    """Canal visible para la cuenta de Telegram autenticada (picker)."""

    channel_id: int
    name: str
    username: str | None
    monitorizado: bool
