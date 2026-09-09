"""DTOs de entrada/salida para el recurso `Informante`."""
from sqlmodel import SQLModel

from app.models.informante import InformanteBase
from app.schemas.pick import PickRead


class InformanteRead(InformanteBase):
    """Representación pública de un informante."""

    id: int


class InformanteStats(SQLModel):
    """Equivalente a la respuesta de GET /informante/:informante en Node."""

    informante: str
    total_apuestas: int
    total_aciertos: int
    ganancias: float
    porcentaje_aciertos: float
    apuestas: list[PickRead] = []
