"""DTOs de salida para el análisis global (auditoría de tipsters)."""

from typing import Optional

from sqlmodel import SQLModel


class StatsBloque(SQLModel):
    """Métricas agregadas de un grupo de apuestas/picks."""

    total: int
    aciertos: int
    ganancias: float
    porcentaje: float
    yield_pct: float
    pendientes: int


class AnalisisCanal(SQLModel):
    """Comparativa de un canal: lo publicado por el tipster vs lo jugado
    por el usuario."""

    informante_id: int
    informante: str
    tipster: StatsBloque
    yo: StatsBloque
    # Cuántos picks del tipster registró el usuario como jugados
    # ("Yo también la jugué").
    jugadas: int
    # Cuota media publicada por el tipster y conseguida por el usuario
    # sobre esos mismos picks jugados — mide la pérdida de cuota real.
    cuota_media_tipster: Optional[float] = None
    cuota_media_mia: Optional[float] = None
    # Combinadas del canal (solo padres): sección propia, fuera de las
    # stats de picks simples.
    combinadas: Optional[StatsBloque] = None


class AnalisisDeporte(SQLModel):
    """Comparativa agregada por deporte (deporte del pick del tipster)."""

    deporte: str
    tipster: StatsBloque
    yo: StatsBloque


class AnalisisGlobal(SQLModel):
    """Respuesta de GET /analisis."""

    canales: list[AnalisisCanal]
    deportes: list[AnalisisDeporte]
    totales_tipster: StatsBloque
    totales_yo: StatsBloque
    # Combinadas agregadas de todos los canales (solo padres).
    totales_combinadas: Optional[StatsBloque] = None
