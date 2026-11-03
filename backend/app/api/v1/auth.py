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
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.postgres import get_session
from app.models.user import User
from app.schemas.user import AuthResponse, TokenRefresh, UserCreate, UserLogin
from app.services.refresh_sessions import (
    issue_session,
    revoke_family,
    rotate_session,
)

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
    refresh = await issue_session(session, user)
    await session.commit()

    return AuthResponse(
        message="Usuario creado exitosamente",
        token=token,
        user=user,
        refresh_token=refresh,
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
    refresh = await issue_session(session, user)
    await session.commit()

    return AuthResponse(
        message="Login exitoso",
        token=token,
        user=user,
        refresh_token=refresh,
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(
    payload: TokenRefresh,
    session: AsyncSession = Depends(get_session),
    _rate_limit: None = Depends(check_rate_limit),
) -> AuthResponse:
    """Canjea un refresh token válido por un par de tokens nuevos
    (rotación). Solo acepta JWT con `type="refresh"` cuyo `jti` esté
    registrado en `refresh_tokens` y siga vivo: un access token no
    vale para refrescar, y un token ya rotado que reaparece revoca la
    familia entera (detección de robo). Pensado para el login
    biométrico del móvil, que guarda el refresh en SecureStore en
    vez de la contraseña.
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

    result = await rotate_session(session, claims)
    if result is None:
        await session.commit()  # persiste la revocación si fue reuso
        raise credentials_error
    new_refresh, user = result
    await session.commit()

    new_claims = {"sub": str(user.id), "email": user.email, "role": user.role}
    return AuthResponse(
        message="Token renovado",
        token=create_access_token(new_claims),
        user=user,
        refresh_token=new_refresh,
    )


@router.post("/logout")
async def logout(
    payload: TokenRefresh,
    session: AsyncSession = Depends(get_session),
    _rate_limit: None = Depends(check_rate_limit),
) -> dict:
    """Revoca la sesión del refresh token presentado: cierra la
    familia entera (todos sus tokens rotados pasados y futuros).
    El móvil puede llamarlo con la credencial biométrica guardada
    para matar la sesión de ese dispositivo en el servidor."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token inválido o expirado",
    )
    try:
        claims = decode_access_token(payload.refresh_token)
    except JWTError:
        raise credentials_error
    if claims.get("type") != "refresh" or not claims.get("fam"):
        raise credentials_error

    revoked = await revoke_family(session, claims["fam"])
    await session.commit()
    return {"message": "Sesión cerrada", "revoked": revoked}
