"""Vista de sistema para administradores.

Expone el estado operativo de los providers de resultados/cuotas
(cuota agotada hoy, misses registrados) — información de depuración
que no debe ser visible para usuarios normales.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, literal
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_current_user
from app.core.dates import utc_now
from app.db.postgres import get_session
from app.models.parsed_pick import ParsedPick
from app.models.user import User, UserRole
from app.services.results import base as results_base

router = APIRouter(prefix="/system", tags=["system"])


class ProvidersStatus(BaseModel):
    """Snapshot de `provider_state.json`: quién está sin cuota hoy y
    cuántos "no encontrado" lleva registrados cada provider."""

    rate_limited: dict[str, str]
    missed_by_provider: dict[str, int]
    calls_today: dict[str, int]
    calls_by_day: dict[str, dict[str, int]]
    # Límite diario observado vía headers x-ratelimit (solo providers
    # que lo reportan; los gratuitos/ilimitados no aparecen).
    daily_limits: dict[str, int]


@router.get("/providers", response_model=ProvidersStatus)
async def providers_status(user: User = Depends(get_current_user)) -> ProvidersStatus:
    """Solo admins: el estado de cuotas es información interna."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores",
        )
    return ProvidersStatus(**results_base.providers_snapshot())


class PicksStatus(BaseModel):
    """Conteo de liquidación de picks para la pantalla de depuración.

    "Pendiente" = es_apuesta, sin acierto y no anulada. Se desglosa en
    simples (ni padre ni pata), patas (combinada_id != NULL) y padres
    combinada (es_combinada). `resueltas_hoy*` usa `verificado_at`, que
    solo existe desde la migración e1f2a3b4c5d6 — los liquidados
    históricos cuentan en `resueltas_total` pero no en los "hoy".
    """

    pendientes_simples: int
    pendientes_patas: int
    pendientes_combinadas: int
    # Desglose de pendientes verificables (es_apuesta, pendiente y no
    # padre de combinada — los padres esperan a sus patas, no a la API):
    # cuántos están dentro de la ventana de 14 días que el ciclo de 3 h
    # reintenta, cuántos son futuros/en juego, cuántos son backlog que
    # solo toca `verify_backlog.py`, y cuántos no tienen fecha_evento
    # (nunca verificables por API).
    pendientes_jugados_ventana: int
    pendientes_futuros: int
    pendientes_backlog: int
    pendientes_sin_fecha: int
    # Mismo conjunto agrupado por deporte (NULL -> "otros").
    pendientes_por_deporte: dict[str, int]
    resueltas_hoy: int
    # De las resueltas hoy: cuántas eran de eventos de hoy y cuántas
    # eran backlog (evento de un día anterior — incluye fecha_evento
    # NULL, que no se puede fechar y se asume antigua).
    resueltas_hoy_evento_hoy: int
    resueltas_hoy_evento_previo: int
    resueltas_total: int
    # Liquidaciones por día ("YYYY-MM-DD" -> n), últimos 14 días.
    resueltas_por_dia: dict[str, int]
    # Liquidaciones por hora UTC ("YYYY-MM-DD HH:00" -> n), últimas 16
    # entradas — aproxima cuántas resolvió cada pasada del verifier
    # (corre cada ~3 h).
    resueltas_por_pasada: dict[str, int]


@router.get("/picks", response_model=PicksStatus)
async def picks_status(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PicksStatus:
    """Solo admins: estado de la cola de verificación."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores",
        )

    pendiente = (ParsedPick.acierto == None) & (  # noqa: E711
        ParsedPick.anulada == False  # noqa: E712
    )
    ahora = utc_now()
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    # literal() evita que asyncpg parametrice 'hour' con placeholders
    # distintos en SELECT/GROUP BY/ORDER BY (GroupingError).
    pasada_bucket = func.date_trunc(literal("hour"), ParsedPick.verificado_at)

    # Conjunto verificable por API: simples y patas pendientes. Los
    # padres de combinada se liquidan por sus patas, no por provider.
    limite_jugado = ahora - timedelta(hours=3)
    limite_backlog = ahora - timedelta(days=14)
    verificable = [
        ParsedPick.es_apuesta == True,  # noqa: E712
        pendiente,
        ParsedPick.es_combinada == False,  # noqa: E712
    ]

    async def _count(*conds: object) -> int:
        q = select(func.count()).select_from(ParsedPick).where(*conds)
        return (await session.exec(q)).one()

    return PicksStatus(
        pendientes_simples=await _count(
            ParsedPick.es_apuesta == True,  # noqa: E712
            pendiente,
            ParsedPick.combinada_id == None,  # noqa: E711
            ParsedPick.es_combinada == False,  # noqa: E712
        ),
        pendientes_patas=await _count(
            pendiente, ParsedPick.combinada_id != None  # noqa: E711
        ),
        pendientes_combinadas=await _count(
            pendiente, ParsedPick.es_combinada == True  # noqa: E712
        ),
        pendientes_jugados_ventana=await _count(
            *verificable,
            ParsedPick.fecha_evento < limite_jugado,
            ParsedPick.fecha_evento >= limite_backlog,
        ),
        pendientes_futuros=await _count(
            *verificable, ParsedPick.fecha_evento >= limite_jugado
        ),
        pendientes_backlog=await _count(
            *verificable, ParsedPick.fecha_evento < limite_backlog
        ),
        pendientes_sin_fecha=await _count(
            *verificable, ParsedPick.fecha_evento == None  # noqa: E711
        ),
        pendientes_por_deporte={
            dep or "otros": n
            for dep, n in (
                await session.exec(
                    select(ParsedPick.deporte, func.count())
                    .where(*verificable)
                    .group_by(ParsedPick.deporte)
                    .order_by(func.count().desc())
                )
            ).all()
        },
        resueltas_hoy=await _count(ParsedPick.verificado_at >= hoy),
        resueltas_hoy_evento_hoy=await _count(
            ParsedPick.verificado_at >= hoy,
            ParsedPick.fecha_evento >= hoy,
        ),
        resueltas_hoy_evento_previo=await _count(
            ParsedPick.verificado_at >= hoy,
            (ParsedPick.fecha_evento == None)  # noqa: E711
            | (ParsedPick.fecha_evento < hoy),
        ),
        resueltas_total=await _count(
            (ParsedPick.acierto != None)  # noqa: E711
            | (ParsedPick.anulada == True)  # noqa: E712
        ),
        resueltas_por_dia={
            str(dia): n
            for dia, n in (
                await session.exec(
                    select(
                        func.date(ParsedPick.verificado_at),
                        func.count(),
                    )
                    .where(ParsedPick.verificado_at != None)  # noqa: E711
                    .group_by(func.date(ParsedPick.verificado_at))
                    .order_by(func.date(ParsedPick.verificado_at).desc())
                    .limit(14)
                )
            ).all()
        },
        resueltas_por_pasada={
            pasada.strftime("%Y-%m-%d %H:00"): n
            for pasada, n in (
                await session.exec(
                    select(pasada_bucket, func.count())
                    .where(ParsedPick.verificado_at != None)  # noqa: E711
                    .group_by(pasada_bucket)
                    .order_by(pasada_bucket.desc())
                    .limit(16)
                )
            ).all()
        },
    )
