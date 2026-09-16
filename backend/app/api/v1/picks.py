"""Endpoints de apuestas (`picks`).

Migrado de `backend/DbMongo/routes.js`:
- GET  /apuestas         (líneas 94-106)
- POST /apuestas          (líneas 108-146)
- PUT  /apuesta/:id        (líneas 148-182)
- DELETE /apuestas/:id     (líneas 239-255)

Hardening de seguridad respecto al Node original: en `routes.js`, PUT y
DELETE no exigían token (cualquiera con el id podía editar/borrar la
apuesta de otro usuario). Aquí ambos requieren `get_current_user` y,
además, verifican que la apuesta pertenezca al usuario autenticado (o
que sea `admin`). Esto es un cambio de contrato deliberado: el frontend
deberá enviar `Authorization: Bearer <token>` también en PUT/DELETE
cuando se conecte a esta API (pendiente en la Fase 3).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_current_user
from app.db.postgres import get_session
from app.models.informante import Informante
from app.models.pick import Pick
from app.models.user import User, UserRole
from app.schemas.pick import PickCreate, PickRead, PickUpdate
from app.services.pick_service import (
    calcular_ganancia,
    to_naive_utc,
)

router = APIRouter(tags=["picks"])


def _ensure_owner_or_admin(pick: Pick, current_user: User) -> None:
    """Solo el dueño de la apuesta o un admin pueden modificarla/borrarla."""
    if current_user.role != UserRole.ADMIN and pick.usuario_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permiso para modificar esta apuesta",
        )


async def _to_pick_read(session: AsyncSession, pick: Pick) -> PickRead:
    informante = await session.get(Informante, pick.informante_id)
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
        ganancia=calcular_ganancia(pick.cantidad_apostada, pick.cuota, pick.acierto),
    )


@router.get("/apuestas", response_model=list[PickRead])
async def listar_apuestas(
    session: AsyncSession = Depends(get_session),
) -> list[PickRead]:
    """Equivalente a `router.get("/apuestas", ...)`: devuelve todas las apuestas."""
    picks = (await session.exec(select(Pick))).all()
    return [await _to_pick_read(session, pick) for pick in picks]


@router.post("/apuestas", response_model=PickRead, status_code=status.HTTP_201_CREATED)
async def crear_apuesta(
    payload: PickCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PickRead:
    """Equivalente a `router.post("/apuestas", ...)`. Requiere autenticación,
    igual que en el Node original (verificaba el JWT manualmente).

    A diferencia del Node original, el informante NO se crea si no existe:
    las apuestas manuales solo pueden vincularse a canales de Telegram
    reales ya monitorizados (`es_canal_telegram`), para que el ranking y
    el detalle del tipster siempre hablen del mismo canal."""
    informante = (
        await session.exec(
            select(Informante).where(
                Informante.nombre == payload.informante,
                Informante.es_canal_telegram.is_(True),
            )
        )
    ).first()
    if informante is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Informante no encontrado: solo puedes registrar apuestas "
                "sobre canales de Telegram monitorizados."
            ),
        )

    pick = Pick(
        apuesta=payload.apuesta,
        tipo_de_apuesta=payload.tipo_de_apuesta,
        casa=payload.casa,
        acierto=payload.acierto,
        cantidad_apostada=payload.cantidad_apostada,
        cuota=payload.cuota,
        usuario_id=current_user.id,
        informante_id=informante.id,
    )
    session.add(pick)
    await session.commit()
    await session.refresh(pick)

    return await _to_pick_read(session, pick)


@router.put("/apuesta/{pick_id}", response_model=PickRead)
async def actualizar_apuesta(
    pick_id: int,
    payload: PickUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PickRead:
    """Equivalente a `router.put("/apuesta/:id", ...)`. Requiere ser el
    dueño de la apuesta o admin (ver nota de hardening arriba)."""
    pick = await session.get(Pick, pick_id)
    if pick is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Apuesta no encontrada"
        )
    _ensure_owner_or_admin(pick, current_user)

    if payload.acierto is not None:
        pick.acierto = payload.acierto
    if payload.fecha is not None:
        pick.fecha = to_naive_utc(payload.fecha)

    session.add(pick)
    await session.commit()
    await session.refresh(pick)

    return await _to_pick_read(session, pick)


@router.delete("/apuestas/{pick_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_apuesta(
    pick_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Equivalente a `router.delete("/apuestas/:id", ...)`. Requiere ser el
    dueño de la apuesta o admin (ver nota de hardening arriba)."""
    pick = await session.get(Pick, pick_id)
    if pick is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Apuesta no encontrada"
        )
    _ensure_owner_or_admin(pick, current_user)

    await session.delete(pick)
    await session.commit()
