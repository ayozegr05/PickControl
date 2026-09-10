"""Tests unitarios de `app/core/security.py`.

Aíslan funciones puras de hashing y JWT sin tocar la app FastAPI ni BD.
"""
from datetime import datetime, timedelta, timezone

import pytest
from jose import JWTError, jwt

from app.core import security
from app.core.config import get_settings


class TestHashAndVerify:
    def test_hash_y_verificacion(self):
        hashed = security.hash_password("password123")
        assert security.verify_password("password123", hashed) is True
        assert security.verify_password("wrong", hashed) is False

    def test_hashes_diferentes_para_mismo_password(self):
        # bcrypt añade salt automáticamente.
        h1 = security.hash_password("password123")
        h2 = security.hash_password("password123")
        assert h1 != h2


class TestAccessToken:
    def test_create_and_decode(self):
        token = security.create_access_token({"sub": "42", "email": "a@b.com"})
        payload = security.decode_access_token(token)
        assert payload["sub"] == "42"
        assert payload["email"] == "a@b.com"

    def test_decode_invalid_token_raises_jwt_error(self):
        with pytest.raises(JWTError):
            security.decode_access_token("not.a.jwt")

    def test_decode_expired_token_raises_jwt_error(self):
        settings = get_settings()
        expired = datetime.now(timezone.utc) - timedelta(minutes=1)
        token = jwt.encode(
            {"sub": "42", "exp": expired},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(JWTError):
            security.decode_access_token(token)
