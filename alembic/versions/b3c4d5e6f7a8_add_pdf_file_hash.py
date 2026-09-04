"""add raw PDF file hash for exact duplicate identity

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Store the raw uploaded PDF digest without changing legacy content hashes."""
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.add_column(
            sa.Column("file_hash", sa.String(length=64), nullable=True)
        )
        batch_op.create_index(
            "ix_source_documents_file_hash",
            ["file_hash"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.drop_index("ix_source_documents_file_hash")
        batch_op.drop_column("file_hash")
