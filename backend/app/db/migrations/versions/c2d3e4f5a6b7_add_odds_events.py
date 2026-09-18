"""add odds_events (registro de eventos del proveedor de odds)

Revision ID: c2d3e4f5a6b7
Revises: b2c3d4e5f6a7
Create Date: 2026-09-18 13:00:00.000000

Una fila por evento resuelto: nombres home/away (necesarios para
casar la selección del pick con la opción "1"/"2" del mercado),
deporte e inicio. Los snapshots la referencian por `event_ext_id`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "odds_events",
        sa.Column("event_ext_id", sa.String(length=60), nullable=False),
        sa.Column("sport", sa.String(length=20), nullable=False),
        sa.Column("home_team", sa.String(length=160), nullable=False),
        sa.Column("away_team", sa.String(length=160), nullable=False),
        sa.Column("start", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("event_ext_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("odds_events")
