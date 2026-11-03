"""Sesiones persistentes (refresh tokens) para el login biométrico.

Una fila por refresh token emitido. El JWT lleva `jti` (id único del
token) y `fam` (id de familia = jti del primer token de la cadena).

- `used_at`: cuándo se rotó (un canje válido marca el viejo y emite
  uno nuevo de la misma familia).
- `revoked_at`: sesión matada explícitamente (logout) o por detección
  de reuso — si un token YA rotado vuelve a presentarse, alguien lo
  robó y se revoca la familia entera.

Con esto el refresh deja de ser un JWT irrevocable: el servidor solo
confía en jti que emitió él y puede cortar la sesión cuando quiera.
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.core.dates import utc_now


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    # El jti del JWT ES la clave: es un uuid4, no hace falta autoincrement.
    jti: str = Field(primary_key=True, max_length=36)
    user_id: int = Field(foreign_key="users.id", index=True)
    # Familia = jti del primer token de la cadena. Rotar mantiene el
    # family_id; revocar se hace por familia.
    family_id: str = Field(index=True, max_length=36)
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
