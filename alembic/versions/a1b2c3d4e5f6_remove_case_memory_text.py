"""remove case memory_text

Revision ID: a1b2c3d4e5f6
Revises: 57e40cc5d20e
Create Date: 2026-04-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "57e40cc5d20e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the memory_text column — case memory is now managed by LlamaIndex."""
    with op.batch_alter_table("cases") as batch_op:
        batch_op.drop_column("memory_text")


def downgrade() -> None:
    with op.batch_alter_table("cases") as batch_op:
        batch_op.add_column(sa.Column("memory_text", sa.String(), nullable=True))
