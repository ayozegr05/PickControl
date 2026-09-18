"""add combinadas (self-FK) a parsed_picks

Revision ID: f1a2b3c4d5e6
Revises: d4e5f6a7b8c9
Create Date: 2026-09-18 10:00:00.000000

Decisión de diseño (ADR) — cómo modelar las patas de una combinada
==================================================================

Contexto: una combinada es UNA apuesta con N selecciones (patas) que se
liquida en conjunto: una pata perdida la tumba, una anulada se excluye
y se recalcula la cuota, todas verdes -> acierto.

Se evaluaron tres opciones:

1. **Self-FK en `parsed_picks` (ELEGIDA)**: la pata es una fila
   `parsed_picks` con `combinada_id` apuntando al padre.
   - Pros: reutiliza TODO el pipeline sin duplicar nada — el verificador
     (`verify_pick` solo lee atributos) resuelve cada pata como un pick
     normal; el PATCH manual sirve por pata; `fecha_evento`, ventanas de
     edad/gracia y dedup del padre (por texto unido) funcionan tal cual.
   - Contras: las queries de `parsed_picks` deben filtrar patas y padres
     para no contaminar la sección de simples (`combinada_id IS NULL AND
     NOT es_combinada`); el riesgo es olvidar el filtro en una query
     nueva — mitigado con tests y por ser un filtro mecánico.

2. **Tabla hija `parsed_pick_patas`**: el modelo de dominio más limpio
   en abstracto (padre y pata son entidades distintas; imposible que una
   pata se cuele en una query de picks).
   - Contra decisiva aquí: el verificador, el PATCH manual y la
     selección de pendientes están construidos sobre filas
     `parsed_picks`; una tabla aparte exige un refactor del verificador
     a un Protocol o un adapter + endpoints duplicados — más código para
     el mismo comportamiento observable, con riesgo de dos ciclos de
     vida que se desincronicen.

3. **Campo JSON `patas`**: migración mínima pero antipatrón para datos
   con ciclo de vida propio: no consultable, sin FK ni índices,
   corrección por pata frágil (PATCH sobre índices de array) y
   read-modify-write del blob completo en cada resolución.

`cuota_efectiva`: cuota real de cobro tras anular patas. La casa
recalcula quitando la cuota de la pata anulada; como los slips OCR casi
nunca traen cuota por pata, se recalcula SOLO si todas las patas tienen
cuota — si no, queda NULL (no se inventa: la ganancia no cuenta en
stats hasta corrección manual).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "parsed_picks",
        sa.Column(
            "es_combinada",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "parsed_picks",
        sa.Column("combinada_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_parsed_picks_combinada_id",
        "parsed_picks",
        ["combinada_id"],
    )
    op.create_foreign_key(
        "fk_parsed_picks_combinada_id",
        "parsed_picks",
        "parsed_picks",
        ["combinada_id"],
        ["id"],
    )
    op.add_column(
        "parsed_picks",
        sa.Column("orden", sa.Integer(), nullable=True),
    )
    op.add_column(
        "parsed_picks",
        sa.Column("cuota_efectiva", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("parsed_picks", "cuota_efectiva")
    op.drop_column("parsed_picks", "orden")
    op.drop_constraint(
        "fk_parsed_picks_combinada_id", "parsed_picks", type_="foreignkey"
    )
    op.drop_index("ix_parsed_picks_combinada_id", table_name="parsed_picks")
    op.drop_column("parsed_picks", "combinada_id")
    op.drop_column("parsed_picks", "es_combinada")
