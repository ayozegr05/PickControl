"""Schemas del registro de dispositivos para notificaciones push."""

from datetime import datetime

from pydantic import BaseModel, Field


class DeviceTokenCreate(BaseModel):
    """Registro de un dispositivo: token Expo Push + plataforma."""

    token: str = Field(min_length=10, max_length=120)
    platform: str = Field(default="unknown", max_length=20)


class DeviceTokenRead(BaseModel):
    """Dispositivo registrado del usuario."""

    id: int
    token: str
    platform: str
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}
