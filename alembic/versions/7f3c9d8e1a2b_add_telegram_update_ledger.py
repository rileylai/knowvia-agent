"""add telegram update ledger

Revision ID: 7f3c9d8e1a2b
Revises: 2d2ef2a72f1d
Create Date: 2026-07-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f3c9d8e1a2b"
down_revision: Union[str, Sequence[str], None] = "2d2ef2a72f1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_update_ledger",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("failure_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_index(
        "ix_telegram_update_ledger_status",
        "telegram_update_ledger",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_update_ledger_status",
        table_name="telegram_update_ledger",
    )
    op.drop_table("telegram_update_ledger")
