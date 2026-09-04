"""add requested and final URL identity to source snapshots

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Persist URL identity without changing existing source snapshots."""
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.add_column(sa.Column("requested_url", sa.String(length=2048)))
        batch_op.add_column(sa.Column("final_url", sa.String(length=2048)))
        batch_op.create_index(
            "ix_source_documents_requested_url",
            ["requested_url"],
            unique=False,
        )
        batch_op.create_index(
            "ix_source_documents_final_url",
            ["final_url"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.drop_index("ix_source_documents_final_url")
        batch_op.drop_index("ix_source_documents_requested_url")
        batch_op.drop_column("final_url")
        batch_op.drop_column("requested_url")
