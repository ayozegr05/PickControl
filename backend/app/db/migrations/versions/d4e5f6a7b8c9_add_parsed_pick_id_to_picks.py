"""add parsed_pick_id a picks

Revision ID: d4e5f6a7b8c9
Revises: c3d5e7f9a1b2
Create Date: 2026-09-22 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "picks",
        sa.Column("parsed_pick_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_picks_parsed_pick_id",
        "picks",
        "parsed_picks",
        ["parsed_pick_id"],
        ["id"],
    )
    op.create_index("ix_picks_parsed_pick_id", "picks", ["parsed_pick_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_picks_parsed_pick_id", table_name="picks")
    op.drop_constraint("fk_picks_parsed_pick_id", "picks", type_="foreignkey")
    op.drop_column("picks", "parsed_pick_id")
