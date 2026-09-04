"""add pgvector embedding foundation

Revision ID: 2d2ef2a72f1d
Revises: 989de3f24186
Create Date: 2026-06-19 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2d2ef2a72f1d"
down_revision: Union[str, Sequence[str], None] = "989de3f24186"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VECTOR_DIMENSIONS = 1536


class VectorType(sa.types.UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: object) -> str:
        return f"VECTOR({self.dimensions})"


def upgrade() -> None:
    """Upgrade schema."""
    if _is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    with op.batch_alter_table("knowledge_chunks") as batch_op:
        batch_op.add_column(sa.Column("embedding", VectorType(VECTOR_DIMENSIONS), nullable=True))
        batch_op.create_index(
            "ix_knowledge_chunks_source_kind",
            ["source_kind"],
            unique=False,
        )
        batch_op.create_index(
            "ix_knowledge_chunks_notion_block_id",
            ["notion_block_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_knowledge_chunks_notion_path",
            ["notion_path"],
            unique=False,
        )

    with op.batch_alter_table("notion_blocks") as batch_op:
        batch_op.create_index(
            "ix_notion_blocks_notion_page_id",
            ["notion_page_id"],
            unique=False,
        )

    if _is_postgresql():
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw_cosine
            ON knowledge_chunks
            USING hnsw (embedding vector_cosine_ops)
            WHERE embedding IS NOT NULL
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw_cosine")

    with op.batch_alter_table("notion_blocks") as batch_op:
        batch_op.drop_index("ix_notion_blocks_notion_page_id")

    with op.batch_alter_table("knowledge_chunks") as batch_op:
        batch_op.drop_index("ix_knowledge_chunks_notion_path")
        batch_op.drop_index("ix_knowledge_chunks_notion_block_id")
        batch_op.drop_index("ix_knowledge_chunks_source_kind")
        batch_op.drop_column("embedding")


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"
