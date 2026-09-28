"""DTOs de entrada/salida para el recurso `Pick` (apuesta)."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.parsed_pick import ParsedPickBase
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


class CombinadaPata(SQLModel):
    """Una pata (selección) de una combinada, con su propio resultado.

    Cada pata se verifica por separado; el padre se liquida en conjunto.
    """

    id: int
    orden: Optional[int] = None
    seleccion: Optional[str] = None
    evento: Optional[str] = None
    mercado: Optional[str] = None
    linea: Optional[float] = None
    cuota: Optional[float] = None
    fecha_evento: Optional[datetime] = None
    acierto: Optional[bool] = None
    anulada: bool = False


class PickRead(SQLModel):
    """Representación pública de una apuesta, incluyendo el nombre del informante
    (en vez del `informante_id` interno) y la ganancia calculada."""

    id: int
    apuesta: str
    informante: str
    tipo_de_apuesta: str
    acierto: Acierto
    casa: str
    # En picks de Telegram pueden ser None (el tipster no siempre publica
    # cuota o stake): nunca se inventan valores por defecto.
    cantidad_apostada: Optional[float] = None
    cuota: Optional[float] = None
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
    # Solo para picks de Telegram: combinada del tipster (sección propia,
    # fuera de las stats de simples). `patas` lleva cada selección con su
    # resultado; `cuota_efectiva` es la cuota real tras excluir anuladas
    # (None si no se pudo recalcular — no se inventa la ganancia).
    es_combinada: bool = False
    cuota_efectiva: Optional[float] = None
    patas: Optional[list[CombinadaPata]] = None
    # Solo picks de Telegram: apuesta anulada/devuelta (void). Sin este
    # flag la anulada serializaba como Pending y la UI la mostraba con
    # "❓" igual que una pendiente real.
    anulada: bool = False


class ParsedPickRead(ParsedPickBase):
    """`ParsedPick` para listados: igual que la fila de BD pero con las
    patas anidadas cuando `es_combinada=True`.

    Las patas nunca salen como filas sueltas del listado: solo anidadas
    bajo su padre (`combinada_id IS NULL` en el nivel superior)."""

    id: int
    raw_message_id: int
    informante_id: Optional[int] = None
    created_at: datetime
    patas: list["ParsedPickRead"] = []
    # True solo en pendientes cuyo evento ya salió de la ventana de
    # verificación automática: el verifier ya no los reintenta.
    fuera_ventana: bool = False
