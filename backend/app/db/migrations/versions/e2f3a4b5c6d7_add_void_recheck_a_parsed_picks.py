"""add void recheck a parsed_picks

Estado del barrido correctivo diario de anuladas automáticas
sospechosas (verifier.recheck_suspicious_voids): cuántos re-checks
lleva el pick (máx. 3, en días +1/+3/+6 desde verificado_at) y cuándo
fue el último.

Revision ID: e2f3a4b5c6d7
Revises: b3c4d5e6f7a8
Create Date: 2026-10-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parsed_picks",
        sa.Column(
            "anulada_rechecks",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "parsed_picks",
        sa.Column("anulada_last_recheck", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("parsed_picks", "anulada_last_recheck")
    op.drop_column("parsed_picks", "anulada_rechecks")
