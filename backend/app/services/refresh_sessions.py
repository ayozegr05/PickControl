"""Ciclo de vida de las sesiones persistentes (refresh tokens).

El endpoint `/auth/refresh` delega aquí la lógica: cada refresh token
tiene una fila en `refresh_tokens` y solo es canjeable mientras la
fila siga viva. Diseño:

- **Rotación**: canjear marca el token como `used_at` y emite uno
  nuevo de la misma familia. El refresh viejo queda muerto.
- **Detección de reuso**: si un token ya rotado vuelve a presentarse,
  lo normal es que se lo hayan robado (el usuario legítimo tiene el
  nuevo) → se revoca la FAMILIA entera, incluido el token del
  usuario legítimo, que tendrá que loguearse de nuevo.
- **Revocación**: `revoke_family` mata la sesión de un dispositivo
  (logout). No hay "cerrar sesión global" porque cada dispositivo
  forma su propia familia.
"""

from datetime import timedelta
from uuid import uuid4

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.dates import utc_now
from app.core.security import create_refresh_token
from app.models.refresh_token import RefreshToken
from app.models.user import User

settings = get_settings()


async def issue_session(session: AsyncSession, user: User) -> str:
    """Emite un refresh token nuevo abriendo una familia nueva
    (una familia = una sesión en un dispositivo)."""
    jti = uuid4().hex
    family_id = jti
    expires_at = utc_now() + timedelta(minutes=settings.jwt_refresh_expires_minutes)
    session.add(
        RefreshToken(
            jti=jti,
            user_id=user.id,
            family_id=family_id,
            expires_at=expires_at,
        )
    )
    claims = {"sub": str(user.id), "email": user.email, "role": user.role}
    return create_refresh_token(claims, jti=jti, family_id=family_id)


async def revoke_family(session: AsyncSession, family_id: str) -> int:
    """Marca `revoked_at` en todos los tokens vivos de una familia.
    Devuelve cuántos se revocaron."""
    rows = (
        await session.exec(
            select(RefreshToken).where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        )
    ).all()
    now = utc_now()
    for row in rows:
        row.revoked_at = now
        session.add(row)
    return len(rows)


async def rotate_session(
    session: AsyncSession, claims: dict
) -> tuple[str, User] | None:
    """Canjea un refresh token por uno nuevo de la misma familia.

    Devuelve `(nuevo_refresh_token, user)` o `None` si el token no es
    canjeable (desconocido, expirado, revocado — o reusado, en cuyo
    caso además revoca la familia entera como efecto lateral).
    """
    jti = claims.get("jti")
    family_id = claims.get("fam")
    if not jti or not family_id:
        return None

    row = await session.get(RefreshToken, jti)
    if row is None or row.revoked_at is not None or row.expires_at < utc_now():
        return None

    if row.used_at is not None:
        # Token ya rotado presentado otra vez → robo probable. Se mata
        # la sesión completa: el ladrón pierde el token y el usuario
        # legítimo pierde el suyo (tendrá que loguearse, señal de que
        # algo pasó).
        await revoke_family(session, family_id)
        await session.commit()
        return None

    user = await session.get(User, row.user_id)
    if user is None:
        return None

    row.used_at = utc_now()
    session.add(row)

    new_jti = uuid4().hex
    expires_at = utc_now() + timedelta(minutes=settings.jwt_refresh_expires_minutes)
    session.add(
        RefreshToken(
            jti=new_jti,
            user_id=user.id,
            family_id=row.family_id,
            expires_at=expires_at,
        )
    )
    claims = {"sub": str(user.id), "email": user.email, "role": user.role}
    return create_refresh_token(claims, jti=new_jti, family_id=row.family_id), user
