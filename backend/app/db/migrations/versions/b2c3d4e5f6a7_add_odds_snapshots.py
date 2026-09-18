"""add odds_snapshots y parsed_picks.odds_event_id

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-18 12:00:00.000000

ADR — auditoría "cuota tipster vs cuota real de mercado" (hito 4):

- `odds_snapshots` es append-only (como `telegram_raw_messages`): una
  fila por opción de mercado por captura. Nunca se actualiza ni se
  borra; la curva de la cuota se reconstruye con `captured_at`.

- Los snapshots van POR EVENTO, no por pick: el mismo partido
  publicado en tres canales cuesta una sola llamada a la API y
  alimenta la comparación de los tres picks. El enlace pick->evento
  es `parsed_picks.odds_event_id` ("<familia>:<id>").

- Se guarda TODO lo que devuelve la API (todos los mercados y
  opciones), no solo lo mapeable hoy: un mapeo de mercados nuevo se
  aplica a datos ya guardados sin volver a llamar.

- `cuota`/`cuota_apertura` en decimal; la API entrega fraccional
  ("6/5") y la conversión se hace al capturar.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "odds_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("event_ext_id", sa.String(length=60), nullable=False),
        sa.Column("market_name", sa.String(length=80), nullable=False),
        sa.Column("choice_group", sa.String(length=20), nullable=True),
        sa.Column("choice_name", sa.String(length=120), nullable=False),
        sa.Column("cuota", sa.Float(), nullable=False),
        sa.Column("cuota_apertura", sa.Float(), nullable=True),
        sa.Column("is_live", sa.Boolean(), nullable=False),
        sa.Column("suspended", sa.Boolean(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("parsed_pick_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["parsed_pick_id"], ["parsed_picks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_odds_snapshots_provider", "odds_snapshots", ["provider"])
    op.create_index(
        "ix_odds_snapshots_event_ext_id", "odds_snapshots", ["event_ext_id"]
    )
    op.create_index("ix_odds_snapshots_captured_at", "odds_snapshots", ["captured_at"])
    op.create_index(
        "ix_odds_snapshots_parsed_pick_id",
        "odds_snapshots",
        ["parsed_pick_id"],
    )
    op.add_column(
        "parsed_picks",
        sa.Column("odds_event_id", sa.String(length=60), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("parsed_picks", "odds_event_id")
    op.drop_index("ix_odds_snapshots_parsed_pick_id", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_captured_at", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_event_ext_id", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_provider", table_name="odds_snapshots")
    op.drop_table("odds_snapshots")
