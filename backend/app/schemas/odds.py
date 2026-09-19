"""DTOs de salida para la auditoría de cuotas (odds de mercado)."""

from sqlmodel import SQLModel


class PickOddsRead(SQLModel):
    """Comparación de la cuota del tipster con la cuota real de mercado.

    `mapeado=False` cuando el mercado del pick no tiene equivalente en
    el proveedor de odds (props de jugador, mercados no cubiertos):
    las cuotas quedan NULL en vez de inventarse.
    """

    mapeado: bool
    mercado_api: str | None = None
    opcion_api: str | None = None
    linea_api: str | None = None
    cuota_tipster: float | None = None
    cuota_apertura: float | None = None
    cuota_publicacion: float | None = None
    cuota_cierre: float | None = None
    capturas: int = 0
    cuota_disponible: bool | None = None
    clv_pct: float | None = None


class OddsStatsBloque(SQLModel):
    """Agregados de la auditoría de cuotas de un conjunto de picks.

    El veredicto global del tipster: un pick aislado no dice nada, el
    patrón sí. Cada porcentaje usa como denominador solo los picks
    donde la métrica era calculable.
    """

    # Picks evaluados (simples del canal, mismo filtro que las stats).
    picks: int = 0
    # Enlazados a un evento del proveedor de odds.
    con_evento: int = 0
    # Con opción de mercado comparable encontrada.
    mapeados: int = 0
    # mapeados / con_evento.
    pct_mapeado: float = 0.0
    # Con cuota de tipster y cuota al publicar: se pudo auditar si la
    # cuota anunciada existía de verdad en el mercado.
    auditables: int = 0
    # Cuota anunciada superior a la de mercado al publicar (inflada).
    cuotas_infladas: int = 0
    # cuotas_infladas / auditables.
    pct_cuota_inflada: float = 0.0
    # Con cuota de cierre disponible: CLV calculable.
    con_clv: int = 0
    # Media de clv_pct (None sin datos): positivo = el tipster bate al
    # mercado de cierre de forma sistemática (valor real).
    clv_medio: float | None = None
    # % de picks con clv_pct > 0 sobre con_clv.
    pct_bate_cierre: float = 0.0
