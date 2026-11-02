"""widen odds_snapshots.choice_group 20 -> 80

`choice_group` lleva la línea del mercado (grupo de opciones) tal
cual la publica el proveedor. Con feeds de bet365 (PulseScore) hay
mercados cuyo grupo es un string largo (p. ej. etiquetas de
"corners race"/especiales) y el varchar(20) rechazaba la tanda
entera del evento en el flush — perdía ~1200 filas congeladas.

Se amplía a 80, el mismo ancho que `market_name`.

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "odds_snapshots",
        "choice_group",
        type_=sa.String(length=80),
        existing_type=sa.String(length=20),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "odds_snapshots",
        "choice_group",
        type_=sa.String(length=20),
        existing_type=sa.String(length=80),
        existing_nullable=True,
    )
