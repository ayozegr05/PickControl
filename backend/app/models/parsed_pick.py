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

    # Apuesta de "reto" del tipster (p. ej. "RETO X3 GRATIS"): va en su
    # propia sección y no se mezcla con las apuestas diarias ni con las
    # estadísticas del canal.
    es_reto: bool = Field(default=False)

    # Combinada (self-FK, ver ADR en la migración f1a2b3c4d5e6):
    # - En el PADRE `es_combinada=True` y la fila lleva la cuota total
    #   declarada, el stake y el texto unido de las patas en `seleccion`
    #   (así el dedup por similitud de texto sigue funcionando).
    # - En cada PATA `combinada_id` apunta al padre y `orden` conserva
    #   la posición en el boleto. La pata es un pick normal: el
    #   verificador la resuelve de forma independiente.
    # - `cuota_efectiva` es la cuota real de cobro tras excluir patas
    #   anuladas; solo se calcula si todas las patas tienen cuota (si
    #   no, NULL: no se inventa la ganancia).
    es_combinada: bool = Field(default=False)
    combinada_id: Optional[int] = Field(
        default=None, foreign_key="parsed_picks.id", index=True
    )
    orden: Optional[int] = None
    cuota_efectiva: Optional[float] = None

    # Enlace al evento en el proveedor de odds para auditar la cuota
    # publicada contra la de mercado ("<familia>:<id>", p. ej.
    # "sofascore:17058707"). Se resuelve una vez por el snapshotter y
    # se reutiliza en todos los snapshots del evento. NULL si el evento
    # no se localizó o el deporte no tiene proveedor de odds.
    odds_event_id: Optional[str] = Field(default=None, max_length=60)

    # Verificación del resultado: None = pendiente, True = acertó, False = falló.
    acierto: Optional[bool] = None
    # Apuesta anulada/devuelta (p. ej. "push" en hándicap/over-under con
    # línea entera). No es lo mismo que "pendiente": aquí SÍ se verificó,
    # pero el resultado no cuenta como acierto ni como fallo.
    anulada: bool = Field(default=False)
    # Quién verificó el resultado: "auto" (cascada de providers),
    # "manual" (corrección del usuario pick a pick) o "expired"
    # (barrido de residuos: salió de la ventana de 14 días sin
    # resolverse — anulada en bloque, no revisada una a una).
    verificado_por: Optional[str] = Field(default=None, max_length=20)
    # Provider que aportó el dato decisivo cuando verificado_por="auto"
    # ("espn", "footapi7", "football24h"...). En mercados de stats es el
    # provider de estadísticas; en marcador/BTTS el del resultado.
    # NULL en liquidaciones manuales/expired y en las históricas
    # anteriores a la migración.
    verificado_provider: Optional[str] = Field(default=None, max_length=40)
    # Por qué se anuló (solo con anulada=True): "push" (empate técnico
    # en línea entera / resultado sin empate empatado), "aplazado"
    # (partido cancelado/aplazado/walkover o fixture reordenado),
    # "jugador_fuera" (prop de jugador cuyo jugador no disputó
    # minutos) o "expired" (barrido de residuos). NULL en anuladas
    # históricas previas a la columna — motivo no registrado.
    motivo_anulada: Optional[str] = Field(default=None, max_length=20)
    # Cuándo se liquidó (auto o manual). NULL mientras siga pendiente;
    # si una corrección reabre el pick, vuelve a NULL.
    verificado_at: Optional[datetime] = None

    # Barrido correctivo diario de anuladas automáticas sospechosas
    # (motivo NULL del sistema viejo, o "aplazado" — el motivo con más
    # falsos positivos: provider que reporta cancelado un partido que
    # sí se jugó, o que tarda en subir el resultado). Se reintentan en
    # días +1/+3/+6 desde `verificado_at`, máximo 3 intentos.
    # `anulada_rechecks` cuenta los ya hechos; `anulada_last_recheck`
    # es el timestamp del último (auditoría).
    anulada_rechecks: int = Field(default=0)
    anulada_last_recheck: Optional[datetime] = None


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
