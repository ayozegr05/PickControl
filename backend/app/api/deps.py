"""Dependencias reutilizables de FastAPI.

`get_current_user` reemplaza la lectura manual de
`req.headers.authorization?.split(' ')[1]` + `jwt.verify(...)` que se
repetía en cada ruta protegida de `DbMongo/routes.js`.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import decode_access_token
from app.db.postgres import get_session
from app.models.user import User

# tokenUrl solo se usa para que /docs muestre el botón "Authorize";
# el login real sigue siendo un POST JSON normal en auth.py.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
    except JWTError as exc:
        raise credentials_exception from exc

    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    user = await session.get(User, int(user_id))
    if user is None or not user.is_active:
        raise credentials_exception

    return user
