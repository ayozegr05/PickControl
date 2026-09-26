"""Endpoints de mensajes crudos y picks extraídos de Telegram."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_current_user
from app.core.dates import utc_now
from app.db.postgres import get_session
from app.models.informante import Informante
from app.models.parsed_pick import ParsedPick
from app.models.pick import Acierto, Pick, PickSource
from app.models.telegram_raw_message import TelegramRawMessage
from app.models.user import User
from app.schemas.odds import PickOddsRead
from app.schemas.pick import ParsedPickRead, PickRead
from app.services.odds.compare import compare_pick
from app.services.pick_service import calcular_ganancia, to_naive_utc
from app.services.results.verifier import (
    _MAX_VERIFICATION_AGE,
    cascade_user_settlements,
    settle_combinada,
)

router = APIRouter(tags=["telegram"])


class ParsedPickAciertoUpdate(BaseModel):
    """Payload para corregir manualmente el resultado de un pick extraído."""

    acierto: bool | None = None
    # Apuesta anulada/devuelta (p. ej. "push" en hándicap/over-under).
    # Si es True, `acierto` se ignora y se guarda como None.
    anulada: bool = False


@router.get("/telegram/raw-messages", response_model=list[TelegramRawMessage])
async def listar_mensajes_crudos(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[TelegramRawMessage]:
    """Devuelve los últimos mensajes crudos recibidos de Telegram."""
    result = await session.exec(
        select(TelegramRawMessage)
        .order_by(TelegramRawMessage.received_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.all())


@router.get("/telegram/parsed-picks", response_model=list[ParsedPickRead])
async def listar_picks_extraidos(
    informante_id: int | None = Query(default=None),
    per_channel: int | None = Query(default=None, ge=1, le=50),
    solo_apuestas: bool = Query(default=False),
    solo_pendientes: bool = Query(default=False),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> list[ParsedPickRead]:
    """Devuelve los picks extraídos de los mensajes de Telegram.

    Dos modos de uso:

    - ``per_channel=N``: vista resumen con los N picks más recientes de
      cada canal. Garantiza que todos los canales aparezcan aunque unos
      publiquen mucho más que otros (los de menor volumen no quedan
      fuera por un corte global).
    - ``informante_id=X`` + ``offset``/``limit``: lista paginada de un
      solo canal, para la vista de detalle de la pantalla de depuración.

    ``solo_apuestas`` filtra ``es_apuesta=True`` ANTES de aplicar el
    top-N o la paginación, para que el resumen no lo ocupen mensajes
    descartados recientes (resultados, promociones, etc.).

    ``solo_pendientes`` (implica ``solo_apuestas``) filtra además
    ``acierto IS NULL AND anulada=False``: la vista de revisión manual
    solo muestra lo que queda por liquidar. Una combinada padre
    pendiente aparece aunque tenga patas ya resueltas (están anidadas).
    """
    # Las patas de combinadas nunca salen como filas sueltas: solo
    # anidadas bajo su padre (`patas`), como componentes que son.
    filters = [ParsedPick.combinada_id.is_(None)]
    if solo_apuestas or solo_pendientes:
        filters.append(ParsedPick.es_apuesta.is_(True))
    if solo_pendientes:
        filters.append(ParsedPick.acierto.is_(None))
        filters.append(ParsedPick.anulada.is_(False))
    if informante_id is not None:
        filters.append(ParsedPick.informante_id == informante_id)

    if per_channel is not None:
        ranked = (
            select(
                ParsedPick.id.label("pick_id"),
                func.row_number()
                .over(
                    partition_by=ParsedPick.informante_id,
                    order_by=ParsedPick.created_at.desc(),
                )
                .label("rn"),
            )
            .where(*filters)
            .subquery()
        )
        stmt = (
            select(ParsedPick)
            .join(ranked, ParsedPick.id == ranked.c.pick_id)
            .where(ranked.c.rn <= per_channel)
            .order_by(ParsedPick.created_at.desc())
        )
    else:
        stmt = (
            select(ParsedPick)
            .where(*filters)
            .order_by(ParsedPick.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

    result = await session.exec(stmt)
    picks = list(result.all())

    # Patas de las combinadas devueltas, agrupadas por padre.
    parent_ids = [p.id for p in picks if p.es_combinada]
    patas_por_padre: dict[int, list[ParsedPick]] = {}
    if parent_ids:
        legs = (
            await session.exec(
                select(ParsedPick)
                .where(ParsedPick.combinada_id.in_(parent_ids))
                .order_by(ParsedPick.orden)
            )
        ).all()
        for leg in legs:
            patas_por_padre.setdefault(leg.combinada_id, []).append(leg)

    # `fuera_ventana`: solo tiene sentido en pendientes — marca los que
    # el verifier ya no reintenta (misma ventana que _should_attempt).
    ventana_min = utc_now() - _MAX_VERIFICATION_AGE

    def _fuera_ventana(p: ParsedPick) -> bool:
        return (
            p.acierto is None
            and not p.anulada
            and p.fecha_evento is not None
            and p.fecha_evento < ventana_min
        )

    return [
        ParsedPickRead(
            **p.model_dump(),
            fuera_ventana=_fuera_ventana(p),
            patas=[
                ParsedPickRead(**leg.model_dump(), fuera_ventana=_fuera_ventana(leg))
                for leg in patas_por_padre.get(p.id, [])
            ],
        )
        for p in picks
    ]


@router.post("/telegram/parsed-picks/anular-residuo")
async def anular_residuo_pendiente(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> dict[str, int]:
    """Anula en bloque los picks pendientes fuera de la ventana de
    verificación (>14 días desde `fecha_evento`).

    El verifier ya no los reintenta — quedaban en "pendiente" para
    siempre contaminando la cola. Marcarlos anulados es la corrección
    honesta: la apuesta no pudo comprobarse, se trata como void y sale
    de stats/pendientes. Las patas anuladas re-liquidan su combinada
    padre (puede liquidarla o dejarla pendiente según el resto).
    """
    cutoff = utc_now() - _MAX_VERIFICATION_AGE
    residuo = (
        await session.exec(
            select(ParsedPick)
            .where(ParsedPick.es_apuesta == True)  # noqa: E712
            .where(ParsedPick.acierto == None)  # noqa: E711
            .where(ParsedPick.anulada == False)  # noqa: E712
            # Los padres no se anulan aquí: tras anular sus patas,
            # `settle_combinada` los liquidará con lo que quede.
            .where(ParsedPick.es_combinada == False)  # noqa: E712
            .where(ParsedPick.fecha_evento != None)  # noqa: E711
            .where(ParsedPick.fecha_evento < cutoff)
        )
    ).all()

    now = utc_now()
    afectados = 0
    for pick in residuo:
        pick.anulada = True
        pick.verificado_por = "manual"
        pick.verificado_at = now
        session.add(pick)
        afectados += 1
    await session.flush()

    # Patas anuladas pueden desbloquear padres pendientes: se recalculan
    # todas las combinadas abiertas (pocas, operación barata).
    padres = (
        await session.exec(
            select(ParsedPick)
            .where(ParsedPick.es_combinada == True)  # noqa: E712
            .where(ParsedPick.acierto == None)  # noqa: E711
            .where(ParsedPick.anulada == False)  # noqa: E712
        )
    ).all()
    for parent in padres:
        await settle_combinada(session, parent)

    await cascade_user_settlements(session)
    await session.commit()
    return {"anuladas": afectados}


@router.get("/telegram/parsed-picks/{pick_id}/odds", response_model=PickOddsRead)
async def cuota_mercado_pick(
    pick_id: int,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> PickOddsRead:
    """Cuota publicada por el tipster vs cuota real de mercado.

    Devuelve la opción de la API comparable al pick (si la hay) con sus
    tres puntos temporales: apertura, cuota más cercana a la hora de
    publicación y cierre — la base de la auditoría de valor (CLV y
    detección de cuotas infladas).
    """
    pick = await session.get(ParsedPick, pick_id)
    if pick is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pick no encontrado"
        )
    comparison = await compare_pick(session, pick)
    return PickOddsRead(**vars(comparison))


@router.patch("/telegram/parsed-picks/{pick_id}", response_model=ParsedPick)
async def corregir_acierto_pick(
    pick_id: int,
    payload: ParsedPickAciertoUpdate,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(get_current_user),
) -> ParsedPick:
    """Corrige manualmente el resultado de un pick (Acertó/Falló/Anulada/Pendiente).

    Útil cuando la verificación automática no puede resolverlo (deporte
    o mercado no soportado) o si se ha equivocado.
    """
    pick = await session.get(ParsedPick, pick_id)
    if pick is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pick no encontrado"
        )

    resolved = payload.anulada or payload.acierto is not None
    pick.acierto = None if payload.anulada else payload.acierto
    pick.anulada = payload.anulada
    pick.verificado_por = "manual" if resolved else None
    pick.verificado_at = utc_now() if resolved else None

    session.add(pick)
    await session.flush()

    # Si es una pata, la corrección re-liquida la combinada entera
    # (p. ej. anular una pata recalcula, o reabrir una pata devuelve el
    # padre a pendiente). El override manual sobre el padre mismo ya
    # quedó marcado con verificado_por="manual" y no se toca.
    if pick.combinada_id is not None:
        parent = await session.get(ParsedPick, pick.combinada_id)
        if parent is not None:
            await settle_combinada(session, parent)

    # Cascada a apuestas de usuario enlazadas ("Yo también la jugué"):
    # si alguien jugó este pick, su apuesta hereda la corrección ahora.
    await cascade_user_settlements(session)

    await session.commit()
    await session.refresh(pick)
    return pick


class JugarPickCreate(BaseModel):
    """Datos de la apuesta real del usuario al marcar "yo también la jugué".

    `cuota` y `casa` son opcionales: si no se envían se copian las del
    tipster. Conviene enviarlas cuando el usuario consiguió una cuota
    distinta — es justo lo que permite comparar el yield publicado con
    el yield real alcanzable.
    """

    cantidad_apostada: float = Field(gt=0)
    cuota: float | None = Field(default=None, gt=1.0)
    casa: str | None = Field(default=None, max_length=100)


def _pick_to_read(pick: Pick, informante: Informante | None) -> PickRead:
    return PickRead(
        id=pick.id,
        apuesta=pick.apuesta,
        informante=informante.nombre if informante else "",
        tipo_de_apuesta=pick.tipo_de_apuesta,
        acierto=pick.acierto,
        casa=pick.casa,
        cantidad_apostada=pick.cantidad_apostada,
        cuota=pick.cuota,
        fecha=pick.fecha,
        source=pick.source,
        parsed_pick_id=pick.parsed_pick_id,
        ganancia=calcular_ganancia(pick.cantidad_apostada, pick.cuota, pick.acierto),
    )


@router.post("/telegram/parsed-picks/{pick_id}/jugar", response_model=PickRead)
async def jugar_pick(
    pick_id: int,
    payload: JugarPickCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PickRead:
    """Registra que el usuario también jugó un pick extraído de Telegram.

    Crea una apuesta (`picks`) copiando selección/mercado/cuota del pick
    y enlazándola con `parsed_pick_id` para trazabilidad. Si el usuario
    ya la registró, devuelve la existente en vez de crear un duplicado.
    """
    parsed = await session.get(ParsedPick, pick_id)
    if parsed is None or not parsed.es_apuesta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pick no encontrado",
        )
    if parsed.combinada_id is not None:
        # "Yo también la jugué" opera sobre la combinada entera, nunca
        # sobre una pata suelta: si llega el id de una pata se usa su padre.
        parent = await session.get(ParsedPick, parsed.combinada_id)
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Combinada no encontrada",
            )
        parsed = parent
    if parsed.informante_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El pick no tiene canal asociado",
        )

    existing = (
        await session.exec(
            select(Pick).where(
                Pick.parsed_pick_id == parsed.id,
                Pick.usuario_id == current_user.id,
            )
        )
    ).first()
    if existing is not None:
        informante = await session.get(Informante, existing.informante_id)
        return _pick_to_read(existing, informante)

    pick = Pick(
        apuesta=parsed.seleccion or parsed.apuesta or "(pick Telegram)",
        tipo_de_apuesta=(parsed.mercado or "Otro")[:50],
        casa=(payload.casa or parsed.casa or "Otra")[:100],
        # Si el pick del canal ya estaba liquidado al registrarse, la
        # apuesta hereda el resultado (anulada queda Pending: el enum
        # del usuario no tiene equivalente a void).
        acierto=(
            Acierto.TRUE
            if parsed.acierto is True
            else Acierto.FALSE if parsed.acierto is False else Acierto.PENDING
        ),
        cantidad_apostada=payload.cantidad_apostada,
        cuota=payload.cuota if payload.cuota is not None else (parsed.cuota or 1.0),
        fecha=to_naive_utc(parsed.fecha_evento) if parsed.fecha_evento else utc_now(),
        source=PickSource.TELEGRAM,
        parsed_pick_id=parsed.id,
        usuario_id=current_user.id,
        informante_id=parsed.informante_id,
    )
    session.add(pick)
    await session.commit()
    await session.refresh(pick)

    informante = await session.get(Informante, pick.informante_id)
    return _pick_to_read(pick, informante)
