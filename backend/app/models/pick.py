"""Entidad de tabla `picks` (equivalente a `apuestaSchema` en model.js)."""
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from app.models._enum_utils import enum_values


class Acierto(str, Enum):
    PENDING = "Pending"
    TRUE = "True"
    FALSE = "False"


class PickSource(str, Enum):
    MANUAL = "manual"
    TELEGRAM = "telegram"


class PickBase(SQLModel):
    apuesta: str
    tipo_de_apuesta: str = Field(max_length=50)
    acierto: Acierto = Field(
        default=Acierto.PENDING,
        sa_column=Column(SAEnum(Acierto, name="acierto", values_callable=enum_values), nullable=False),
    )
    casa: str = Field(max_length=100)
    cantidad_apostada: float = Field(default=0)
    cuota: float = Field(default=1)
    fecha: datetime = Field(default_factory=datetime.utcnow)
    source: PickSource = Field(
        default=PickSource.MANUAL,
        sa_column=Column(SAEnum(PickSource, name="picksource", values_callable=enum_values), nullable=False),
    )
    channel_id: Optional[str] = None
    message_id: Optional[str] = None


class Pick(PickBase, table=True):
    __tablename__ = "picks"

    id: Optional[int] = Field(default=None, primary_key=True)
    usuario_id: int = Field(foreign_key="users.id")
    informante_id: int = Field(foreign_key="informantes.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
