"""Entidad de tabla `informantes`.

En el modelo Mongo original, `Informante` era solo un string libre dentro
de cada `Pick`. Al normalizar para PostgreSQL, se convierte en su propia
tabla para evitar datos duplicados/inconsistentes y permitir relaciones
(FK) desde `picks`.
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.core.dates import utc_now


class InformanteBase(SQLModel):
    nombre: str = Field(index=True, unique=True, max_length=150)


class Informante(InformanteBase, table=True):
    __tablename__ = "informantes"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)
