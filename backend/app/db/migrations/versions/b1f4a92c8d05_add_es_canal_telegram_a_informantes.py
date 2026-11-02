"""add es_canal_telegram a informantes

Revision ID: b1f4a92c8d05
Revises: 96270cbac2d1
Create Date: 2026-09-16 13:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f4a92c8d05"
down_revision: Union[str, Sequence[str], None] = "96270cbac2d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "informantes",
        sa.Column(
            "es_canal_telegram",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Backfill: los informantes que ya tienen picks extraídos de Telegram
    # son los canales reales. Los creados a mano desde el formulario
    # quedan con es_canal_telegram=false.
    op.execute(
        """
        UPDATE informantes
        SET es_canal_telegram = true
        WHERE id IN (
            SELECT DISTINCT informante_id
            FROM parsed_picks
            WHERE informante_id IS NOT NULL
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("informantes", "es_canal_telegram")
