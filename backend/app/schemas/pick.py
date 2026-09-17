"""DTOs de entrada/salida para el recurso `Pick` (apuesta)."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.pick import Acierto, PickSource


class PickCreate(SQLModel):
    """Payload de creación (equivalente al body de POST /apuestas)."""

    apuesta: str
    informante: str = Field(
        description=(
            "Nombre del informante; debe ser un canal de Telegram "
            "monitorizado existente (no se crean informantes nuevos)"
        )
    )
    tipo_de_apuesta: str
    casa: str
    acierto: Acierto = Acierto.PENDING
    cantidad_apostada: float = 0
    cuota: float = 1


class PickUpdate(SQLModel):
    """Payload de actualización parcial (equivalente al body de PUT /apuesta/:id)."""

    acierto: Optional[Acierto] = None
    fecha: Optional[datetime] = None


class PickRead(SQLModel):
    """Representación pública de una apuesta, incluyendo el nombre del informante
    (en vez del `informante_id` interno) y la ganancia calculada."""

    id: int
    apuesta: str
    informante: str
    tipo_de_apuesta: str
    acierto: Acierto
    casa: str
    cantidad_apostada: float
    cuota: float
    fecha: datetime
    source: PickSource
    ganancia: Optional[float] = None
    evento: Optional[str] = None
    # Si la apuesta nació de un pick de Telegram ("Yo también la jugué"),
    # id del parsed_pick de origen; None en apuestas dadas de alta a mano.
    parsed_pick_id: Optional[int] = None
    # Solo para picks de Telegram: apuesta de "reto" del tipster (va en su
    # propia sección, fuera de las apuestas diarias y de las stats).
    es_reto: bool = False
