"""add generic knowledge contract metadata

Revision ID: a1b2c3d4e5f6
Revises: 9c5e7b1a2d4f
Create Date: 2026-09-04 22:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9c5e7b1a2d4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add source identity and chunk provenance required by generic Knowledge."""
    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "owner_scope",
                sa.String(length=128),
                nullable=False,
                server_default="local",
            )
        )
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="parsed",
            )
        )
        batch_op.create_index(
            "ix_source_documents_owner_scope",
            ["owner_scope"],
            unique=False,
        )
        batch_op.create_index(
            "ix_source_documents_status",
            ["status"],
            unique=False,
        )

    with op.batch_alter_table("knowledge_chunks") as batch_op:
        batch_op.add_column(sa.Column("source_display_name", sa.String(length=512)))
        batch_op.add_column(sa.Column("locator", sa.String(length=256)))
        batch_op.add_column(sa.Column("citation_metadata", sa.Text()))
        batch_op.add_column(sa.Column("embedding_model", sa.String(length=128)))
        batch_op.add_column(sa.Column("embedding_dimensions", sa.Integer()))
        batch_op.add_column(
            sa.Column(
                "owner_scope",
                sa.String(length=128),
                nullable=False,
                server_default="local",
            )
        )
        batch_op.add_column(
            sa.Column(
                "eligibility_status",
                sa.String(length=32),
                nullable=False,
                server_default="eligible",
            )
        )
        batch_op.create_index(
            "ix_knowledge_chunks_owner_scope",
            ["owner_scope"],
            unique=False,
        )
        batch_op.create_index(
            "ix_knowledge_chunks_eligibility_status",
            ["eligibility_status"],
            unique=False,
        )


def downgrade() -> None:
    """Remove generic Knowledge metadata while retaining the existing chunk foundation."""
    with op.batch_alter_table("knowledge_chunks") as batch_op:
        batch_op.drop_index("ix_knowledge_chunks_eligibility_status")
        batch_op.drop_index("ix_knowledge_chunks_owner_scope")
        batch_op.drop_column("eligibility_status")
        batch_op.drop_column("owner_scope")
        batch_op.drop_column("embedding_dimensions")
        batch_op.drop_column("embedding_model")
        batch_op.drop_column("citation_metadata")
        batch_op.drop_column("locator")
        batch_op.drop_column("source_display_name")

    with op.batch_alter_table("source_documents") as batch_op:
        batch_op.drop_index("ix_source_documents_status")
        batch_op.drop_index("ix_source_documents_owner_scope")
        batch_op.drop_column("status")
        batch_op.drop_column("owner_scope")
