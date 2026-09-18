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
from app.models.parsed_pick import ParsedPick
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


def calcular_ganancia(
    cantidad_apostada: float, cuota: float, acierto: Acierto
) -> float:
    """Beneficio neto de una apuesta individual."""
    if acierto == Acierto.TRUE:
        return round(cantidad_apostada * (cuota - 1), 2)
    if acierto == Acierto.FALSE:
        return round(-cantidad_apostada, 2)
    return 0.0  # Pending: todavía no genera ganancia ni pérdida


@dataclass
class StatsInput:
    """Entrada genérica para calcular estadísticas de apuestas."""

    id: int | None
    cantidad_apostada: float
    cuota: float
    acierto: Acierto


@dataclass
class InformanteStatsResult:
    total_apuestas: int
    total_aciertos: int
    ganancias: float
    porcentaje_aciertos: float
    yield_pct: float
    total_pendientes: int = 0
    ganancias_por_pick: dict[int, float] = field(default_factory=dict)


def _stats_inputs_to_result(
    inputs: list[StatsInput],
) -> InformanteStatsResult:
    """Calcula métricas agregadas de una lista de apuestas normalizadas.

    - `porcentaje_aciertos` e `yield_pct` solo consideran apuestas
      finalizadas (Acierto != Pending), igual que hacía el Node original.
    - `yield_pct` = ganancias / total apostado * 100.
    """
    total_apuestas = len(inputs)
    ganancias_por_pick = {
        input_.id: calcular_ganancia(
            input_.cantidad_apostada, input_.cuota, input_.acierto
        )
        for input_ in inputs
        if input_.id is not None
    }

    finalizadas = [i for i in inputs if i.acierto != Acierto.PENDING]
    total_aciertos = sum(1 for i in finalizadas if i.acierto == Acierto.TRUE)
    total_apostado = sum(i.cantidad_apostada for i in finalizadas)
    ganancias = round(
        sum(
            ganancias_por_pick[i.id]
            for i in finalizadas
            if i.id is not None and i.id in ganancias_por_pick
        ),
        2,
    )

    porcentaje_aciertos = (
        round((total_aciertos / len(finalizadas)) * 100, 2) if finalizadas else 0.0
    )
    yield_pct = (
        round((ganancias / total_apostado) * 100, 2) if total_apostado > 0 else 0.0
    )

    return InformanteStatsResult(
        total_apuestas=total_apuestas,
        total_aciertos=total_aciertos,
        ganancias=ganancias,
        porcentaje_aciertos=porcentaje_aciertos,
        yield_pct=yield_pct,
        # El % de aciertos e yield se calculan solo sobre las finalizadas:
        # sin este dato la card mostraba "3 picks, 1 acierto, 100%" y
        # parecía que el tipster lo había acertado todo.
        total_pendientes=total_apuestas - len(finalizadas),
        ganancias_por_pick=ganancias_por_pick,
    )


def to_stats_input(pick: Pick) -> StatsInput:
    """Convierte un `Pick` manual en entrada de estadísticas."""
    return StatsInput(
        id=pick.id,
        cantidad_apostada=pick.cantidad_apostada,
        cuota=pick.cuota,
        acierto=pick.acierto,
    )


def to_stats_input_from_parsed(parsed: ParsedPick) -> StatsInput:
    """Convierte un `ParsedPick` de Telegram en entrada de estadísticas.

    Si no hay cuota o stake, se asume 1 unidad de stake y cuota 1.0,
    de modo que la apuesta no aporta ganancia hasta tener esos datos.

    En combinadas se usa `cuota_efectiva` (la cuota real tras excluir
    patas anuladas); si quedó a None porque no se pudo recalcular, la
    cuota efectiva es 1.0 -> ganancia 0 hasta corrección manual (no se
    inventa el beneficio).
    """
    if parsed.anulada:
        acierto = Acierto.PENDING
    elif parsed.acierto is True:
        acierto = Acierto.TRUE
    elif parsed.acierto is False:
        acierto = Acierto.FALSE
    else:
        acierto = Acierto.PENDING
    if parsed.es_combinada:
        cuota = parsed.cuota_efectiva or 1.0
    else:
        cuota = parsed.cuota or 1.0
    return StatsInput(
        id=parsed.id,
        cantidad_apostada=parsed.stake or 1.0,
        cuota=cuota,
        acierto=acierto,
    )


def calcular_stats(picks: list[Pick]) -> InformanteStatsResult:
    """Calcula métricas agregadas de una lista de apuestas manuales."""
    return _stats_inputs_to_result([to_stats_input(p) for p in picks])


def _es_pick_simple(parsed: ParsedPick) -> bool:
    """True si el parsed_pick cuenta en las stats de picks simples.

    Quedan fuera los retos (sección propia), las combinadas (padre) y
    las patas (se liquidan dentro de su combinada, nunca sueltas).
    """
    return (
        parsed.es_apuesta
        and not parsed.es_reto
        and not parsed.es_combinada
        and parsed.combinada_id is None
    )


def calcular_stats_parsed(parsed_picks: list[ParsedPick]) -> InformanteStatsResult:
    """Calcula métricas agregadas de picks extraídos de Telegram.

    Los retos (`es_reto`) y las combinadas (`es_combinada` + patas) se
    excluyen: van en sus propias secciones y su stake/cuota atípicos
    distorsionarían el yield del canal.
    """
    return _stats_inputs_to_result(
        [to_stats_input_from_parsed(p) for p in parsed_picks if _es_pick_simple(p)]
    )


def calcular_stats_combinadas(
    parsed_picks: list[ParsedPick],
) -> InformanteStatsResult:
    """Métricas de las combinadas del canal (solo los padres).

    Sección propia, igual que los retos: las patas nunca cuentan
    sueltas — su resultado ya está agregado en el padre.
    """
    return _stats_inputs_to_result(
        [
            to_stats_input_from_parsed(p)
            for p in parsed_picks
            if p.es_apuesta and p.es_combinada
        ]
    )


def calcular_stats_combinado(
    picks: list[Pick], parsed_picks: list[ParsedPick]
) -> InformanteStatsResult:
    """Calcula métricas combinando apuestas manuales y picks de Telegram."""
    return _stats_inputs_to_result(
        [to_stats_input(p) for p in picks]
        + [to_stats_input_from_parsed(p) for p in parsed_picks if _es_pick_simple(p)]
    )


async def get_or_create_informante(
    session: AsyncSession, nombre: str, es_canal_telegram: bool = False
) -> Informante:
    """Busca un informante por nombre o lo crea si no existe.

    En Mongo, `Informante` era un string libre dentro de cada `Pick`; al
    normalizar a Postgres se convierte en su propia tabla, así que hay
    que resolver/crear la fila correspondiente al guardar una apuesta.

    `es_canal_telegram` solo lo pasa el pipeline de Telegram: marca el
    informante como canal real. Si el informante ya existía (p. ej. creado
    a mano antes de esta restricción) y llega un pick de su canal, se
    "asciende" a canal para unificar los datos.
    """
    result = await session.exec(select(Informante).where(Informante.nombre == nombre))
    informante = result.first()
    if informante is None:
        informante = Informante(nombre=nombre, es_canal_telegram=es_canal_telegram)
        session.add(informante)
        await session.flush()
        await session.refresh(informante)
    elif es_canal_telegram and not informante.es_canal_telegram:
        informante.es_canal_telegram = True
        session.add(informante)
        await session.flush()
    return informante
