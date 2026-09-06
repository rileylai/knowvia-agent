"""add owner-scoped long-term memories

Revision ID: 6f7a8b9c0d1e
Revises: f1a2b3c4d5e6
Create Date: 2026-09-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6f7a8b9c0d1e"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


class VectorType(sa.types.UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: object) -> str:
        return f"VECTOR({self.dimensions})"


def upgrade() -> None:
    if _is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "long_term_memories",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_normalized", sa.Text(), nullable=False),
        sa.Column("embedding", VectorType(1536), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("source_session_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["source_session_id"], ["conversation_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_message_id"], ["conversation_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "memory_type",
            "content_normalized",
            name="uq_long_term_memory_owner_type_content",
        ),
    )
    op.create_index("ix_long_term_memories_owner_id", "long_term_memories", ["owner_id"], unique=False)
    op.create_index("ix_long_term_memories_memory_type", "long_term_memories", ["memory_type"], unique=False)
    op.create_index("ix_long_term_memories_status", "long_term_memories", ["status"], unique=False)
    if _is_postgresql():
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_long_term_memories_embedding_hnsw_cosine
            ON long_term_memories
            USING hnsw (embedding vector_cosine_ops)
            WHERE status = 'active'
            """
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS ix_long_term_memories_embedding_hnsw_cosine")
    op.drop_index("ix_long_term_memories_status", table_name="long_term_memories")
    op.drop_index("ix_long_term_memories_memory_type", table_name="long_term_memories")
    op.drop_index("ix_long_term_memories_owner_id", table_name="long_term_memories")
    op.drop_table("long_term_memories")


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"
