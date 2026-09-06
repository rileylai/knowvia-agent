"""add owner-scoped conversation sessions and messages

Revision ID: f1a2b3c4d5e6
Revises: e6f7a8b9c0d1
Create Date: 2026-09-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
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
    )
    op.create_index(
        "ix_conversation_sessions_owner_id",
        "conversation_sessions",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_sessions_status",
        "conversation_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_sessions_updated_at",
        "conversation_sessions",
        ["updated_at"],
        unique=False,
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversation_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "sequence_number",
            name="uq_conversation_message_session_sequence",
        ),
    )
    op.create_index(
        "ix_conversation_messages_session_id",
        "conversation_messages",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_messages_session_id",
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")
    op.drop_index(
        "ix_conversation_sessions_updated_at",
        table_name="conversation_sessions",
    )
    op.drop_index(
        "ix_conversation_sessions_status",
        table_name="conversation_sessions",
    )
    op.drop_index(
        "ix_conversation_sessions_owner_id",
        table_name="conversation_sessions",
    )
    op.drop_table("conversation_sessions")
