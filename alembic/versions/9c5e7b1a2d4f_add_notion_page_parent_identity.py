"""add nullable canonical Notion page parent identity

Revision ID: 9c5e7b1a2d4f
Revises: 8a4d1f0c2b3e
Create Date: 2026-08-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c5e7b1a2d4f"
down_revision: Union[str, Sequence[str], None] = "8a4d1f0c2b3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("notion_pages") as batch_op:
        batch_op.add_column(
            sa.Column("parent_notion_page_id", sa.String(length=128), nullable=True)
        )
        batch_op.create_index(
            "ix_notion_pages_parent_notion_page_id",
            ["parent_notion_page_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("notion_pages") as batch_op:
        batch_op.drop_index("ix_notion_pages_parent_notion_page_id")
        batch_op.drop_column("parent_notion_page_id")
