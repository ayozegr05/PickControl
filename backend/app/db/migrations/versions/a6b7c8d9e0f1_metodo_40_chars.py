"""metodo de parsed_picks a 40 chars

Revision ID: a6b7c8d9e0f1
Revises: b5c6d7e8f9a0
Create Date: 2026-11-05

`metodo` era VARCHAR(20): las etiquetas auditan el motor extractor
("rule+llm-groq-ingesta" ya son 20) y el sufijo `+par` del
emparejamiento foto+texto lo pasaba a 24 → UPDATE fallaba con
StringDataRightTruncationError y la pareja no quedaba marcada
(caso real: pick 11676, Hapoel-Barcelona, nov-2026). A 40 hay
margen para nombres de endpoint futuros.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "parsed_picks",
        "metodo",
        type_=sa.String(length=40),
        existing_type=sa.String(length=20),
    )


def downgrade() -> None:
    op.alter_column(
        "parsed_picks",
        "metodo",
        type_=sa.String(length=20),
        existing_type=sa.String(length=40),
    )
