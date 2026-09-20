"""Tokens de dispositivos para notificaciones push (Expo Push API).

Una fila por dispositivo registrado de un usuario: el token
`ExponentPushToken[...]` que emite `expo-notifications` en el móvil.
`enabled=False` marca tokens muertos (Expo responde
`DeviceNotRegistered`) — se conserva la fila por si el mismo token se
re-registra, y así no se vuelve a intentar un envío imposible.
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.core.dates import utc_now


class DeviceToken(SQLModel, table=True):
    __tablename__ = "device_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # El token Expo identifica el dispositivo de forma única; si otro
    # usuario lo registra, la fila se reasigna (el móvil cambió de cuenta).
    token: str = Field(max_length=120, unique=True, index=True)
    platform: str = Field(default="unknown", max_length=20)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
