"""add linea y anulada a parsed_picks

Revision ID: 8da644e4ae57
Revises: 5b10158da666
Create Date: 2026-09-15 10:18:29.576693

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8da644e4ae57"
down_revision: Union[str, Sequence[str], None] = "5b10158da666"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("parsed_picks", sa.Column("linea", sa.Float(), nullable=True))
    op.add_column(
        "parsed_picks",
        sa.Column("anulada", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("parsed_picks", "anulada")
    op.drop_column("parsed_picks", "linea")
