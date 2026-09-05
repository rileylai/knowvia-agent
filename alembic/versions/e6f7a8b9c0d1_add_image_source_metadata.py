"""add bounded image source preview and ordered metadata

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.add_column(sa.Column("source_preview", sa.String(length=512)))
        batch_op.add_column(sa.Column("source_metadata", sa.Text()))
        batch_op.add_column(sa.Column("image_count", sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.drop_column("image_count")
        batch_op.drop_column("source_metadata")
        batch_op.drop_column("source_preview")
