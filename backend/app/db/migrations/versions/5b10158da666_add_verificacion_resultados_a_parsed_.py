"""add verificacion resultados a parsed_picks

Revision ID: 5b10158da666
Revises: cd2dfbe83713
Create Date: 2026-09-14 15:22:01.630167

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b10158da666"
down_revision: Union[str, Sequence[str], None] = "cd2dfbe83713"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "parsed_picks", sa.Column("fecha_evento", sa.DateTime(), nullable=True)
    )
    op.add_column("parsed_picks", sa.Column("acierto", sa.Boolean(), nullable=True))
    op.add_column(
        "parsed_picks",
        sa.Column("verificado_por", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("parsed_picks", "verificado_por")
    op.drop_column("parsed_picks", "acierto")
    op.drop_column("parsed_picks", "fecha_evento")
