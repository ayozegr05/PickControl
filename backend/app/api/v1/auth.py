"""Endpoints de autenticación.

Migrado de `backend/DbMongo/routes.js`:
- POST /register  (líneas 8-47 de routes.js)
- POST /login      (líneas 50-92 de routes.js)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dates import utc_now
from app.core.security import create_access_token, hash_password, verify_password
from app.db.postgres import get_session
from app.models.user import User
from app.schemas.user import AuthResponse, UserCreate, UserLogin

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    """Crea un nuevo usuario. Equivalente a `router.post("/register", ...)`."""
    existing_user = (
        await session.exec(select(User).where(User.email == payload.email))
    ).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El email ya está registrado")

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role})

    return AuthResponse(message="Usuario creado exitosamente", token=token, user=user)


@router.post("/login", response_model=AuthResponse)
async def login(payload: UserLogin, session: AsyncSession = Depends(get_session)) -> AuthResponse:
    """Autentica un usuario existente. Equivalente a `router.post("/login", ...)`."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales inválidas"
    )

    user = (await session.exec(select(User).where(User.email == payload.email))).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise credentials_error

    # Naive UTC, igual que created_at/updated_at (la columna es
    # TIMESTAMP WITHOUT TIME ZONE; asyncpg rechaza datetimes tz-aware ahí).
    user.last_login = utc_now()
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token({"sub": str(user.id), "email": user.email, "role": user.role})

    return AuthResponse(message="Login exitoso", token=token, user=user)
