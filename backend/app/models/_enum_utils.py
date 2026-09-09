"""Utilidad compartida para columnas ENUM de SQLAlchemy/Postgres.

Sin esto, SQLAlchemy guarda por defecto el `.name` de un Enum de Python
(p. ej. "USER") en vez de su `.value` (p. ej. "user"), lo que rompe el
tipo ENUM nativo de Postgres si `name` y `value` no coinciden.
"""
from enum import Enum


def enum_values(enum_cls: type[Enum]) -> list[str]:
    return [member.value for member in enum_cls]
