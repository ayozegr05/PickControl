"""add verificado_at a parsed_picks

Timestamp de cuándo se liquidó el pick (auto por el verifier o manual
por el usuario). NULL mientras siga pendiente. Permite métricas de
depuración tipo "resueltas hoy" en /system/picks.

Revision ID: e1f2a3b4c5d6
Revises: b8c9d0e1f2a3
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parsed_picks",
        sa.Column("verificado_at", sa.DateTime(), nullable=True),
    )
    # Los ya liquidados quedan con NULL: no hay forma de saber cuándo
    # se resolvieron históricamente, y "resueltas hoy" mide actividad
    # del verifier desde ahora.
    op.create_index(
        "ix_parsed_picks_verificado_at",
        "parsed_picks",
        ["verificado_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_parsed_picks_verificado_at", table_name="parsed_picks")
    op.drop_column("parsed_picks", "verificado_at")
