from typing import List, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base
from src.db.types import Vector


class NotionPage(Base):
    __tablename__ = "notion_pages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    notion_page_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    notion_path: Mapped[str] = mapped_column(Text, nullable=False)
    parent_notion_page_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    last_edited_time: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class NotionBlock(Base):
    __tablename__ = "notion_blocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    notion_block_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    notion_page_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notion_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_block_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notion_blocks.id", ondelete="SET NULL"),
        nullable=True,
    )
    block_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=True)
    block_path: Mapped[str] = mapped_column(Text, nullable=True)
    block_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    source_preview: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    source_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    file_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    requested_url: Mapped[Optional[str]] = mapped_column(
        String(2048), nullable=True, index=True
    )
    final_url: Mapped[Optional[str]] = mapped_column(
        String(2048), nullable=True, index=True
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    owner_scope: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="local", index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="parsed", index=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_documents.id", ondelete="CASCADE"),
        nullable=True,
    )
    notion_block_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notion_blocks.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    notion_path: Mapped[str] = mapped_column(Text, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536),
        nullable=True,
    )
    embedding_text: Mapped[str] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_display_name: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    locator: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    citation_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    embedding_dimensions: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    owner_scope: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="local", index=True
    )
    eligibility_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="eligible", index=True
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_notion_page_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notion_pages.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    proposal_json: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str] = mapped_column(String(128), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    event: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failure_reason: Mapped[str] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    finished_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)


class TelegramUpdateLedger(Base):
    __tablename__ = "telegram_update_ledger"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    workflow_run_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ApiIdempotencyRecord(Base):
    __tablename__ = "api_idempotency_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_scope: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    response_status_code: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_headers_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
