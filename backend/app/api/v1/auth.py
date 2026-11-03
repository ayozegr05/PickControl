"""Endpoints de autenticación.

Migrado de `backend/DbMongo/routes.js`:
- POST /register  (líneas 8-47 de routes.js)
- POST /login      (líneas 50-92 de routes.js)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dates import utc_now
from app.core.rate_limit import check_rate_limit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.postgres import get_session
from app.models.user import User
from app.schemas.user import AuthResponse, TokenRefresh, UserCreate, UserLogin

router = APIRouter(tags=["auth"])


@router.post(
    "/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    payload: UserCreate,
    session: AsyncSession = Depends(get_session),
    _rate_limit: None = Depends(check_rate_limit),
) -> AuthResponse:
    """Crea un nuevo usuario. Equivalente a `router.post("/register", ...)`."""
    existing_user = (
        await session.exec(select(User).where(User.email == payload.email))
    ).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El email ya está registrado",
        )

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    claims = {"sub": str(user.id), "email": user.email, "role": user.role}
    token = create_access_token(claims)

    return AuthResponse(
        message="Usuario creado exitosamente",
        token=token,
        user=user,
        refresh_token=create_refresh_token(claims),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: UserLogin,
    session: AsyncSession = Depends(get_session),
    _rate_limit: None = Depends(check_rate_limit),
) -> AuthResponse:
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

    claims = {"sub": str(user.id), "email": user.email, "role": user.role}
    token = create_access_token(claims)

    return AuthResponse(
        message="Login exitoso",
        token=token,
        user=user,
        refresh_token=create_refresh_token(claims),
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(
    payload: TokenRefresh,
    session: AsyncSession = Depends(get_session),
    _rate_limit: None = Depends(check_rate_limit),
) -> AuthResponse:
    """Canjea un refresh token válido por un par de tokens nuevos
    (rotación). Solo acepta JWT con `type="refresh"`: un access token
    no vale para refrescar. Pensado para el login biométrico del móvil,
    que guarda el refresh token en SecureStore en vez de la contraseña.
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token inválido o expirado",
    )
    try:
        claims = decode_access_token(payload.refresh_token)
    except JWTError:
        raise credentials_error
    if claims.get("type") != "refresh":
        raise credentials_error

    user_id = claims.get("sub")
    if user_id is None:
        raise credentials_error
    user = await session.get(User, int(user_id))
    if user is None:
        raise credentials_error

    new_claims = {"sub": str(user.id), "email": user.email, "role": user.role}
    return AuthResponse(
        message="Token renovado",
        token=create_access_token(new_claims),
        user=user,
        refresh_token=create_refresh_token(new_claims),
    )
