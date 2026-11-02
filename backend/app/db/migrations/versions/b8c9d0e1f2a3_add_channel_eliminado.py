"""add channels.eliminado (borrado logico de canales)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-20

Borrar un canal pasa a ser soft delete (`eliminado=True`): la fila se
conserva con su historial y re-anadirlo reactiva la misma fila, pero
desaparece de la lista de monitorizados y vuelve a la de disponibles.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column("eliminado", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("channels", "eliminado")
