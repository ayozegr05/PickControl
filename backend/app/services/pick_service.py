"""Lógica de negocio de `Pick` (apuesta): ganancia, % aciertos y Yield.

Extraído y corregido de la lógica que en el backend Node vivía mezclada
dentro de `router.get("/informante/:informante")` (`DbMongo/routes.js`,
líneas ~196-229) y duplicada en el frontend (`calcularGanancia` /
`calcularGananciaAcumulada` en `index.tsx` y `[informante].tsx`).

Nota de migración: el Node original calculaba, por error, la "ganancia"
de cada apuesta como `CantidadApostada * Cuota` (retorno bruto) en vez
del beneficio neto. Aquí se corrige para que coincida con la fórmula
correcta que ya usaba el frontend: `stake * (cuota - 1)` si acierta,
`-stake` si falla, `0` si está pendiente.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.informante import Informante
from app.models.pick import Acierto, Pick


def to_naive_utc(dt: datetime) -> datetime:
    """Normaliza un datetime a UTC "naive" (sin tzinfo).

    La columna `picks.fecha` es `TIMESTAMP WITHOUT TIME ZONE`; asyncpg
    rechaza datetimes tz-aware ahí. Si el payload llega con timezone
    (p. ej. `"...+00:00"` desde el cliente), hay que convertirlo a UTC
    y quitarle el tzinfo antes de guardarlo.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def calcular_ganancia(cantidad_apostada: float, cuota: float, acierto: Acierto) -> float:
    """Beneficio neto de una apuesta individual."""
    if acierto == Acierto.TRUE:
        return round(cantidad_apostada * (cuota - 1), 2)
    if acierto == Acierto.FALSE:
        return round(-cantidad_apostada, 2)
    return 0.0  # Pending: todavía no genera ganancia ni pérdida


@dataclass
class InformanteStatsResult:
    total_apuestas: int
    total_aciertos: int
    ganancias: float
    porcentaje_aciertos: float
    yield_pct: float
    ganancias_por_pick: dict[int, float] = field(default_factory=dict)


def calcular_stats(picks: list[Pick]) -> InformanteStatsResult:
    """Calcula las métricas agregadas de una lista de apuestas de un informante.

    - `porcentaje_aciertos` y `yield_pct` solo consideran apuestas
      finalizadas (Acierto != Pending), igual que hacía el Node original
      para el porcentaje de aciertos.
    - `yield_pct` = ganancias / total apostado * 100. Es una métrica
      nueva (no existía en el backend Node) pedida explícitamente para
      esta migración: mide la rentabilidad relativa al capital
      arriesgado, más útil que la ganancia absoluta para comparar
      informantes entre sí.
    """
    total_apuestas = len(picks)
    ganancias_por_pick = {
        pick.id: calcular_ganancia(pick.cantidad_apostada, pick.cuota, pick.acierto)
        for pick in picks
        if pick.id is not None
    }

    finalizadas = [p for p in picks if p.acierto != Acierto.PENDING]
    total_aciertos = sum(1 for p in finalizadas if p.acierto == Acierto.TRUE)
    total_apostado = sum(p.cantidad_apostada for p in finalizadas)
    ganancias = round(sum(ganancias_por_pick[p.id] for p in finalizadas if p.id is not None), 2)

    porcentaje_aciertos = round((total_aciertos / len(finalizadas)) * 100, 2) if finalizadas else 0.0
    yield_pct = round((ganancias / total_apostado) * 100, 2) if total_apostado > 0 else 0.0

    return InformanteStatsResult(
        total_apuestas=total_apuestas,
        total_aciertos=total_aciertos,
        ganancias=ganancias,
        porcentaje_aciertos=porcentaje_aciertos,
        yield_pct=yield_pct,
        ganancias_por_pick=ganancias_por_pick,
    )


async def get_or_create_informante(session: AsyncSession, nombre: str) -> Informante:
    """Busca un informante por nombre o lo crea si no existe.

    En Mongo, `Informante` era un string libre dentro de cada `Pick`; al
    normalizar a Postgres se convierte en su propia tabla, así que hay
    que resolver/crear la fila correspondiente al guardar una apuesta.
    """
    result = await session.exec(select(Informante).where(Informante.nombre == nombre))
    informante = result.first()
    if informante is None:
        informante = Informante(nombre=nombre)
        session.add(informante)
        await session.commit()
        await session.refresh(informante)
    return informante
