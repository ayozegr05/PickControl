"""Modelo para snapshots de cuotas de mercado (auditoría de cuotas).

Append-only, igual que `telegram_raw_messages`: cada fila es la cuota
de UNA opción de UN mercado de UN evento en un instante concreto.
Nunca se actualiza ni se borra — la curva de la cuota se reconstruye
ordenando por `captured_at`.

Los snapshots se guardan POR EVENTO, no por pick: si tres canales
publican el mismo partido, una sola captura alimenta la comparación de
los tres picks (que enlazan por `parsed_picks.odds_event_id`).
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.core.dates import utc_now


class OddsEvent(SQLModel, table=True):
    """Registro del evento en el proveedor de odds (1 fila por evento).

    Guarda lo que el snapshot necesita pero no puede llevar por fila:
    los nombres home/away (imprescindibles para casar la selección del
    pick con la opción "1"/"2" del mercado), el deporte y el inicio.
    """

    __tablename__ = "odds_events"

    event_ext_id: str = Field(primary_key=True, max_length=60)
    sport: str = Field(max_length=20)
    home_team: str = Field(max_length=160)
    away_team: str = Field(max_length=160)
    start: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)


class OddsSnapshot(SQLModel, table=True):
    """Una opción de un mercado de odds en un instante.

    - `provider`: quién sirvió la llamada ("allsportsapi2",
      "tennisapi1"...). Varios proveedores pueden compartir espacio de
      ids (la familia Sofascore), por eso `event_ext_id` va namespaced.
    - `event_ext_id`: "<familia>:<id>" — p. ej. "sofascore:17058707".
      Es la clave de unión con `parsed_picks.odds_event_id`.
    - `market_name`/`choice_group`/`choice_name`: tal cual vienen del
      proveedor ("Full time", choiceGroup "22.5", choice "Over").
      El mapeo pick.mercado -> mercado concreto se hace al comparar,
      no aquí: se guarda TODO lo que devuelve la API para que un
      mapeo futuro no necesite volver a llamar.
    - `cuota`/`cuota_apertura`: decimal, convertida desde el formato
      fraccional del proveedor ("6/5" -> 2.20). `cuota_apertura` es el
      precio de apertura que regala la API en cada respuesta.
    """

    __tablename__ = "odds_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(max_length=40, index=True)
    event_ext_id: str = Field(max_length=60, index=True)
    market_name: str = Field(max_length=80)
    choice_group: Optional[str] = Field(default=None, max_length=20)
    choice_name: str = Field(max_length=120)
    cuota: float
    cuota_apertura: Optional[float] = None
    is_live: bool = Field(default=False)
    suspended: bool = Field(default=False)
    captured_at: datetime = Field(default_factory=utc_now, index=True)

    # Traza de qué pick disparó la captura (auditoría/depuración). El
    # dato sirve igualmente a otros picks del mismo evento.
    parsed_pick_id: Optional[int] = Field(
        default=None, foreign_key="parsed_picks.id", index=True
    )
