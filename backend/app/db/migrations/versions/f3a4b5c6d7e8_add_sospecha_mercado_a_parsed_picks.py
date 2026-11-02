"""add sospecha_mercado a parsed_picks

Marca de auditoría sobre la liquidación (no la altera): "3way"
cuando un pick se liquidó como push asiático en línea entera
(córners/tarjetas) pero el tipster probablemente jugó el mercado
a 3 vías (1xBet: Más/Exactamente/Menos — la línea exacta pierde,
no devuelve). Sin evidencia del mercado real del día queda como
sospecha revisable; con snapshots 1xBet se confirma por huella
de precio (el 3-way paga ~1.4-1.6 donde el 2-way paga ~1.25).

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parsed_picks",
        sa.Column("sospecha_mercado", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("parsed_picks", "sospecha_mercado")
