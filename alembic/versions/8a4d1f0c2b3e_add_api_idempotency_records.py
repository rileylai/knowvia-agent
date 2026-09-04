"""add api idempotency records

Revision ID: 8a4d1f0c2b3e
Revises: 7f3c9d8e1a2b
Create Date: 2026-07-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a4d1f0c2b3e"
down_revision: Union[str, Sequence[str], None] = "7f3c9d8e1a2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_idempotency_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_scope", sa.String(length=256), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("response_headers_json", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_scope",
            "idempotency_key",
            name="uq_api_idempotency_scope_key",
        ),
    )
    op.create_index(
        "ix_api_idempotency_records_status",
        "api_idempotency_records",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_api_idempotency_records_status",
        table_name="api_idempotency_records",
    )
    op.drop_table("api_idempotency_records")
