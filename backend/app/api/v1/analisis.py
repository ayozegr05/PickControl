"""Análisis global: auditoría de tipsters (lo publicado vs lo jugado).

`GET /analisis` agrega, por canal y por deporte, las estadísticas de los
picks extraídos de Telegram (lo que el tipster publica) contra las
apuestas reales del usuario (`picks`), incluida la cuota media publicada
vs la cuota media conseguida sobre los picks enlazados con "Yo también
la jugué".
"""

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.postgres import get_session
from app.models.informante import Informante
from app.models.parsed_pick import ParsedPick
from app.models.pick import Pick
from app.schemas.analisis import (
    AnalisisCanal,
    AnalisisDeporte,
    AnalisisGlobal,
    StatsBloque,
)
from app.services.pick_service import (
    InformanteStatsResult,
    calcular_stats,
    calcular_stats_parsed,
)

router = APIRouter(tags=["analisis"])


def _to_bloque(stats: InformanteStatsResult) -> StatsBloque:
    return StatsBloque(
        total=stats.total_apuestas,
        aciertos=stats.total_aciertos,
        ganancias=stats.ganancias,
        porcentaje=stats.porcentaje_aciertos,
        yield_pct=stats.yield_pct,
        pendientes=stats.total_pendientes,
    )


def _media(valores: list[float | None]) -> float | None:
    nums = [v for v in valores if v is not None]
    return round(sum(nums) / len(nums), 2) if nums else None


@router.get("/analisis", response_model=AnalisisGlobal)
async def analisis_global(
    session: AsyncSession = Depends(get_session),
) -> AnalisisGlobal:
    """Comparativa global tipster vs usuario, por canal y por deporte."""
    informantes = (
        await session.exec(
            select(Informante).where(Informante.es_canal_telegram.is_(True))
        )
    ).all()
    picks = (await session.exec(select(Pick))).all()
    parsed_picks = (await session.exec(select(ParsedPick))).all()

    parsed_by_id = {p.id: p for p in parsed_picks}
    parsed_by_informante: dict[int, list[ParsedPick]] = {}
    for parsed in parsed_picks:
        if parsed.informante_id is not None:
            parsed_by_informante.setdefault(parsed.informante_id, []).append(parsed)

    picks_by_informante: dict[int, list[Pick]] = {}
    for pick in picks:
        picks_by_informante.setdefault(pick.informante_id, []).append(pick)

    canales: list[AnalisisCanal] = []
    for informante in informantes:
        telegram = parsed_by_informante.get(informante.id, [])
        mias = picks_by_informante.get(informante.id, [])

        jugados = [p for p in mias if p.parsed_pick_id is not None]
        canales.append(
            AnalisisCanal(
                informante_id=informante.id,
                informante=informante.nombre,
                tipster=_to_bloque(calcular_stats_parsed(telegram)),
                yo=_to_bloque(calcular_stats(mias)),
                jugadas=len(jugados),
                cuota_media_tipster=_media(
                    [
                        parsed_by_id[p.parsed_pick_id].cuota
                        for p in jugados
                        if p.parsed_pick_id in parsed_by_id
                    ]
                ),
                cuota_media_mia=_media([p.cuota for p in jugados]),
            )
        )

    # Por deporte: los picks del tipster se agrupan por `deporte`; las
    # apuestas del usuario heredan el deporte del pick enlazado (las
    # manuales sin enlace caen en "sin deporte").
    deportes_tipster: dict[str, list[ParsedPick]] = {}
    for parsed in parsed_picks:
        deportes_tipster.setdefault(parsed.deporte or "sin deporte", []).append(parsed)
    deportes_yo: dict[str, list[Pick]] = {}
    for pick in picks:
        deporte = "sin deporte"
        if pick.parsed_pick_id is not None and pick.parsed_pick_id in parsed_by_id:
            deporte = parsed_by_id[pick.parsed_pick_id].deporte or "sin deporte"
        deportes_yo.setdefault(deporte, []).append(pick)

    deportes: list[AnalisisDeporte] = []
    for nombre in sorted(set(deportes_tipster) | set(deportes_yo)):
        deportes.append(
            AnalisisDeporte(
                deporte=nombre,
                tipster=_to_bloque(
                    calcular_stats_parsed(deportes_tipster.get(nombre, []))
                ),
                yo=_to_bloque(calcular_stats(deportes_yo.get(nombre, []))),
            )
        )

    # Ranking de canales por yield del tipster (como /informantes).
    canales.sort(key=lambda c: c.tipster.yield_pct, reverse=True)

    return AnalisisGlobal(
        canales=canales,
        deportes=deportes,
        totales_tipster=_to_bloque(calcular_stats_parsed(parsed_picks)),
        totales_yo=_to_bloque(calcular_stats(picks)),
    )
