"""add unique constraint to docket_entry_documents

Revision ID: 57e40cc5d20e
Revises: fd958aed1db8
Create Date: 2026-04-12 07:35:40.011432

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "57e40cc5d20e"
down_revision: str | None = "fd958aed1db8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite does not support ALTER TABLE ADD CONSTRAINT, so we use batch mode
    # (copy-and-move strategy) to recreate the table with the unique constraint.
    with op.batch_alter_table("docket_entry_documents") as batch_op:
        batch_op.create_unique_constraint("uq_doc_entry_url", ["docket_entry_id", "pdf_url"])


def downgrade() -> None:
    with op.batch_alter_table("docket_entry_documents") as batch_op:
        batch_op.drop_constraint("uq_doc_entry_url", type_="unique")
