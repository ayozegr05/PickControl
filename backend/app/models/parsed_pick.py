"""Modelo para picks estructurados extraídos de mensajes de Telegram."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.core.dates import utc_now


class ParsedPickBase(SQLModel):
    """Campos extraídos por el parser híbrido (reglas + LLM)."""

    es_apuesta: bool = Field(default=False)
    apuesta: Optional[str] = None
    deporte: Optional[str] = None
    evento: Optional[str] = None
    mercado: Optional[str] = None
    seleccion: Optional[str] = None
    cuota: Optional[float] = None
    stake: Optional[float] = None
    casa: Optional[str] = None
    informante: Optional[str] = None
    explicacion: Optional[str] = None
    metodo: str = Field(default="unknown", max_length=20)
    confianza: float = Field(default=0.0)

    # Fecha/hora del evento deportivo (si se pudo extraer del mensaje).
    # Necesaria para poder buscar el resultado real en las APIs deportivas.
    fecha_evento: Optional[datetime] = None

    # Verificación del resultado: None = pendiente, True = acertó, False = falló.
    acierto: Optional[bool] = None
    # Quién verificó el resultado: "auto" (API de resultados) o "manual".
    verificado_por: Optional[str] = Field(default=None, max_length=20)


class ParsedPick(ParsedPickBase, table=True):
    """Pick estructurado ligado a un mensaje crudo de Telegram."""

    __tablename__ = "parsed_picks"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_message_id: int = Field(
        foreign_key="telegram_raw_messages.id",
        index=True,
        nullable=False,
    )
    created_at: datetime = Field(default_factory=utc_now)
