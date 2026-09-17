"""DTOs de entrada/salida para el recurso `Informante`."""

from sqlmodel import SQLModel

from app.models.informante import InformanteBase
from app.schemas.pick import PickRead


class InformanteRead(InformanteBase):
    """Representación pública de un informante."""

    id: int


class InformanteStats(SQLModel):
    """Equivalente a la respuesta de GET /informante/:informante en Node,
    con el campo `yield_pct` añadido (métrica nueva, no existía en Node)
    y la integración de picks provenientes de Telegram."""

    informante: str
    total_apuestas: int
    total_aciertos: int
    ganancias: float
    porcentaje_aciertos: float
    yield_pct: float
    total_pendientes: int = 0
    apuestas: list[PickRead] = []

    # Picks extraídos automáticamente de Telegram.
    parsed_picks: list[PickRead] = []
    parsed_total_apuestas: int = 0
    parsed_total_aciertos: int = 0
    parsed_ganancias: float = 0.0
    parsed_porcentaje_aciertos: float = 0.0
    parsed_yield_pct: float = 0.0
    parsed_total_pendientes: int = 0


class InformanteSummary(SQLModel):
    """Resumen agregado de un informante para listados y rankings.

    Incluye métricas de apuestas manuales, picks de Telegram y el total
    combinado, para poder comparar la rentabilidad de cada tipster."""

    informante: str

    # Apuestas manuales.
    manual_total: int = 0
    manual_aciertos: int = 0
    manual_ganancias: float = 0.0
    manual_porcentaje: float = 0.0
    manual_yield: float = 0.0
    manual_pendientes: int = 0

    # Picks de Telegram.
    parsed_total: int = 0
    parsed_aciertos: int = 0
    parsed_ganancias: float = 0.0
    parsed_porcentaje: float = 0.0
    parsed_yield: float = 0.0
    parsed_pendientes: int = 0

    # Totales combinados.
    total: int = 0
    aciertos: int = 0
    ganancias: float = 0.0
    porcentaje: float = 0.0
    yield_pct: float = 0.0
    pendientes: int = 0
