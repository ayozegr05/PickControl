"""Utilidades de seguridad: hashing de contraseñas y JWT.

Equivalente a lo que en el backend Node se hacía con:
- `bcryptjs` (hash/compare de password en `model.js`, middleware `pre('save')`)
- `jsonwebtoken` (`jwt.sign` / `jwt.verify` en `DbMongo/routes.js`)
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Genera el hash de una contraseña en texto plano (equivalente a
    `bcrypt.hash()` en `userSchema.pre('save')`)."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Compara una contraseña en texto plano contra su hash (equivalente a
    `userSchema.methods.comparePassword`)."""
    return _pwd_context.verify(plain_password, password_hash)


def create_access_token(data: dict[str, Any]) -> str:
    """Genera un JWT firmado (equivalente a `jwt.sign(...)`).

    `data` debe incluir los claims que queramos exponer, por ejemplo
    `{"sub": str(user.id), "email": user.email, "role": user.role}`.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_expires_minutes
    )
    to_encode = {**data, "exp": expire}
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decodifica y valida un JWT. Lanza `JWTError` si el token es
    inválido o expiró (equivalente a `jwt.verify(...)`)."""
    try:
        return jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise exc
