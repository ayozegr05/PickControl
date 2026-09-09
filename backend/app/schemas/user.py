"""DTOs de entrada/salida para el recurso `User` (capa API, no de tabla)."""
from datetime import datetime
from typing import Optional

from pydantic import EmailStr
from sqlmodel import Field, SQLModel

from app.models.user import UserBase, UserRole


class UserCreate(SQLModel):
    """Payload de registro (equivalente al body de POST /register)."""

    name: str = Field(max_length=120)
    email: EmailStr
    password: str = Field(min_length=6)


class UserLogin(SQLModel):
    """Payload de login (equivalente al body de POST /login)."""

    email: EmailStr
    password: str


class UserRead(UserBase):
    """Representación pública de un usuario (nunca incluye password_hash)."""

    id: int
    created_at: datetime
    last_login: Optional[datetime] = None


class AuthResponse(SQLModel):
    """Respuesta de /register y /login."""

    message: str
    token: str
    user: UserRead
