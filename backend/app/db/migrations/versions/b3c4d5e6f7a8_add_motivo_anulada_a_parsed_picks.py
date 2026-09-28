"""add motivo_anulada a parsed_picks

Motivo del void: "push" (empate técnico en línea entera), "aplazado"
(partido cancelado/aplazado/walkover), "jugador_fuera" (el jugador del
prop no disputó minutos) o "expired" (barrido de residuos). Las
anuladas históricas quedan NULL — no hay registro del motivo y no se
inventa. Solo se rellenan las del barrido, que sí sabemos que fueron
expired por verificado_por.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "parsed_picks",
        sa.Column("motivo_anulada", sa.String(length=20), nullable=True),
    )
    op.execute(
        "UPDATE parsed_picks SET motivo_anulada = 'expired' "
        "WHERE anulada AND verificado_por = 'expired'"
    )


def downgrade() -> None:
    op.drop_column("parsed_picks", "motivo_anulada")
