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

    # Valor numérico de la línea, cuando el mercado la tiene:
    # - Hándicap asiático: con signo, p. ej. +1.5 / -1.0 (se aplica al
    #   equipo de `seleccion`).
    # - Over/Under: magnitud de la línea de goles, p. ej. 2.5 (la
    #   dirección over/under se guarda en `mercado`/`seleccion`).
    linea: Optional[float] = None

    # Verificación del resultado: None = pendiente, True = acertó, False = falló.
    acierto: Optional[bool] = None
    # Apuesta anulada/devuelta (p. ej. "push" en hándicap/over-under con
    # línea entera). No es lo mismo que "pendiente": aquí SÍ se verificó,
    # pero el resultado no cuenta como acierto ni como fallo.
    anulada: bool = Field(default=False)
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
    informante_id: Optional[int] = Field(
        default=None,
        foreign_key="informantes.id",
        index=True,
    )
    created_at: datetime = Field(default_factory=utc_now)
