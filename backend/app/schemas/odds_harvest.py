"""DTOs de la cosecha manual de cuotas (Tampermonkey/WebView).

El userscript lee el DOM ya renderizado de la página de partido de
cuotasahora/oddsportal y envía lo que ve: filas planas
(mercado + selección + línea + cuotas) por bookmaker. El servidor
traduce, casa el evento con los picks pendientes y escribe los
snapshots con `provider="oddsportal"` — mismo contrato que una
fuente de backfill, pero el "fetch" lo hizo el navegador del usuario.
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import field_validator
from sqlmodel import SQLModel


class HarvestOddsRow(SQLModel):
    """Una cuota vista en la página: mercado + selección + precio."""

    # Nombre del mercado tal como lo muestra la página ("1X2",
    # "Over/Under", "Asian Handicap"...) — el servidor lo normaliza.
    market: str
    # Etiqueta de la opción ("1", "X", "2", "Over", "Más", "Yes"...).
    selection: str
    # Línea del mercado cuando aplica (2.5 en O/U, -1.5 en AH).
    line: Optional[float] = None
    # Cuota de cierre (la visible al cargar, partido ya jugado).
    closing: Optional[float] = None
    # Cuota de apertura si la página la muestra (oddsportal la lista).
    opening: Optional[float] = None
    # Casa de apuestas de la fila ("bet365", "Pinnacle"...).
    bookmaker: Optional[str] = None


class HarvestEventPayload(SQLModel):
    """Una página de partido cosechada completa."""

    # URL de donde se extrajo (lleva el match_id de oddsportal).
    url: str
    # "futbol" | "tenis" | "baloncesto" (se aceptan aliases ingleses).
    sport: str
    home: str
    away: str
    # Inicio del evento según la página (naive UTC o con tz).
    start: Optional[datetime] = None
    rows: list[HarvestOddsRow]

    @field_validator("start", mode="after")
    @classmethod
    def _start_naive_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        # Las columnas son naive-UTC: el JSON-LD llega con tz ("...Z").
        if v is not None and v.tzinfo is not None:
            return v.astimezone(timezone.utc).replace(tzinfo=None)
        return v


class HarvestPickInfo(SQLModel):
    """Resumen del pick pendiente para el panel del script."""

    id: int
    mercado: Optional[str] = None
    seleccion: Optional[str] = None
    linea: Optional[float] = None


class HarvestQueueEvent(SQLModel):
    """Un evento con picks pendientes que el script debe localizar."""

    sport: str
    date: str  # YYYY-MM-DD del evento según el pick
    home: str
    away: str
    # Texto de búsqueda sugerido ("Ipswich Liverpool").
    search: str
    picks: list[HarvestPickInfo]


class HarvestQueueRead(SQLModel):
    events: list[HarvestQueueEvent]


class HarvestIngestResult(SQLModel):
    """Resultado de procesar un evento cosechado."""

    event_ext_id: str
    accepted: bool  # False = el evento no casó con ningún pick pendiente
    matched_picks: list[int] = []
    unmatched_picks: list[int] = []
    rows_written: int = 0
    detail: str = ""
