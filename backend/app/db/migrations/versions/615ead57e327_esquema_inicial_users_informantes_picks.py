"""esquema inicial: users, informantes, picks

Crea las 3 tablas base migradas desde MongoDB (`model.js`):
- users        (antes `userSchema`)
- informantes  (antes el string libre `Informante` dentro de cada Pick)
- picks        (antes `apuestaSchema`)

El DDL de este archivo se escribió a mano (no autogenerado) y se validó
comparándolo con `CreateTable(...).compile(dialect=postgresql.dialect())`
sobre los modelos de `app/models/`, ya que no había una instancia de
Postgres disponible en el entorno para autogenerar contra ella. Los
nombres de los tipos ENUM (`userrole`, `acierto`, `picksource`) y de los
índices (`ix_users_email`, `ix_informantes_nombre`) coinciden
exactamente con lo que generaría `SQLModel.metadata.create_all()`.

Revision ID: 615ead57e327
Revises:
Create Date: 2026-09-09 14:36:38.978795

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '615ead57e327'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    user_role = sa.Enum("user", "admin", name="userrole")
    acierto = sa.Enum("Pending", "True", "False", name="acierto")
    pick_source = sa.Enum("manual", "telegram", name="picksource")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "informantes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=150), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_informantes_nombre", "informantes", ["nombre"], unique=True)

    op.create_table(
        "picks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("apuesta", sa.String(), nullable=False),
        sa.Column("tipo_de_apuesta", sa.String(length=50), nullable=False),
        sa.Column("acierto", acierto, nullable=False),
        sa.Column("casa", sa.String(length=100), nullable=False),
        sa.Column("cantidad_apostada", sa.Float(), nullable=False),
        sa.Column("cuota", sa.Float(), nullable=False),
        sa.Column("fecha", sa.DateTime(), nullable=False),
        sa.Column("source", pick_source, nullable=False),
        sa.Column("channel_id", sa.String(), nullable=True),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("informante_id", sa.Integer(), sa.ForeignKey("informantes.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("picks")
    op.drop_index("ix_informantes_nombre", table_name="informantes")
    op.drop_table("informantes")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    sa.Enum(name="picksource").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="acierto").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="userrole").drop(op.get_bind(), checkfirst=True)
