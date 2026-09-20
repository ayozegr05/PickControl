"""add channels table

Revision ID: a1b2c3d4e5f6
Revises: c2d3e4f5a6b7
Create Date: 2026-09-20 12:00:00.000000

Canales de Telegram monitorizados, gestionados desde la app. La
población inicial desde `TELEGRAM_TARGET_CHANNEL` se hace en tiempo de
arranque del listener (`services/telegram/channels.py`), no aquí, para
que la migración sea DDL puro y no dependa del `.env`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target"),
    )
    op.create_index("ix_channels_channel_id", "channels", ["channel_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_channels_channel_id", table_name="channels")
    op.drop_table("channels")
