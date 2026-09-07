"""add a derived retrieval representation for long-term memories

Revision ID: 7a8b9c0d1e2f
Revises: 6f7a8b9c0d1e
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a8b9c0d1e2f"
down_revision: Union[str, Sequence[str], None] = "6f7a8b9c0d1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("long_term_memories") as batch_op:
        batch_op.add_column(sa.Column("retrieval_text", sa.Text(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE long_term_memories "
            "SET retrieval_text = content "
            "WHERE retrieval_text IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("long_term_memories") as batch_op:
        batch_op.drop_column("retrieval_text")
