"""add verificado_provider a parsed_picks

Qué provider aportó el dato decisivo cuando el pick se liquidó en
auto ("espn", "footapi7", "football24h"...). Permite ver en
/system/providers cuántas liquidaciones resuelve cada provider, no
solo cuántas llamadas y misses hace. NULL en históricos y en
liquidaciones manuales/expired.

Revision ID: a2b3c4d5e6f7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parsed_picks",
        sa.Column("verificado_provider", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("parsed_picks", "verificado_provider")
