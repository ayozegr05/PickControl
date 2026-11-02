"""add es_reto a parsed_picks

Revision ID: c3d5e7f9a1b2
Revises: b1f4a92c8d05
Create Date: 2026-09-17 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, Sequence[str], None] = "b1f4a92c8d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "parsed_picks",
        sa.Column(
            "es_reto",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Backfill: los picks cuyo mensaje (texto u OCR) menciona "reto" son
    # apuestas de reto del tipster y pasan a su propia sección.
    op.execute(
        """
        UPDATE parsed_picks
        SET es_reto = true
        WHERE raw_message_id IN (
            SELECT id
            FROM telegram_raw_messages
            WHERE lower(text) ~ '\\mreto\\M'
               OR lower(coalesce(extracted_text, '')) ~ '\\mreto\\M'
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("parsed_picks", "es_reto")
