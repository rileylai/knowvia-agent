from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import get_embedding_client, get_tool_registry
from src.app.main import app
from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument, WorkflowRun
from src.db.session import get_db_session, get_db_session_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.orchestrators import DocumentIngestionOrchestrator
from src.providers import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
)
from src.repositories import SourceDocumentRepository
from src.services import EmbeddingBatchService, WorkflowRunService
from src.tools import PDFParserClient, PDFParserTool, ParsedPDFDocument, ToolRegistry


@dataclass
class _FakePDFParserClient(PDFParserClient):
    documents: list[ParsedPDFDocument]

    def __post_init__(self) -> None:
        self.calls = 0

    def parse_document(self, *, file_name: str, file_bytes: bytes) -> ParsedPDFDocument:
        _ = file_name, file_bytes
        document = self.documents[min(self.calls, len(self.documents) - 1)]
        self.calls += 1
        return document


class _FakeEmbeddingClient(EmbeddingClient):
    def __init__(self) -> None:
        self.requests: list[EmbeddingRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        return EmbeddingCapabilities(
            provider="fake",
            model=model,
            dimensions=dimensions,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        return EmbeddingResponse(
            provider="fake",
            model=request.model or "fake-embedding",
            embeddings=[[float(index + 1)] * 1536 for index, _ in enumerate(request.inputs)],
            indices=list(range(len(request.inputs))),
            token_input=len(request.inputs),
        )


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            NotionPage.__table__,
            NotionBlock.__table__,
            SourceDocument.__table__,
            KnowledgeChunk.__table__,
            WorkflowRun.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _build_orchestrator(session_factory, parser_client, embedding_client):
    registry = ToolRegistry()
    registry.register_tool(PDFParserTool(parser_client))
    return DocumentIngestionOrchestrator(
        tool_registry=registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_batch_service=EmbeddingBatchService(
            embedding_client=embedding_client,
            model="text-embedding-3-small",
            dimensions=1536,
        ),
    )


def _pdf_document(text: str) -> ParsedPDFDocument:
    return ParsedPDFDocument(raw_text=text, page_count=1, pages=[text])


@pytest.mark.parametrize("second_file_name", ["report.pdf", "renamed-report.pdf"])
def test_exact_duplicate_pdf_reuses_indexed_source_without_reembedding(
    second_file_name: str,
) -> None:
    session_factory = _build_session_factory()
    embedding_client = _FakeEmbeddingClient()
    parser_client = _FakePDFParserClient(
        documents=[_pdf_document("The same indexed PDF snapshot.")]
    )
    orchestrator = _build_orchestrator(
        session_factory, parser_client, embedding_client
    )

    first = asyncio.run(
        orchestrator.ingest_document(
            file_name="report.pdf",
            file_bytes=b"same-pdf-bytes",
            mime_type="application/pdf",
            request_workflow_id="wf-first",
        )
    )
    second = asyncio.run(
        orchestrator.ingest_document(
            file_name=second_file_name,
            file_bytes=b"same-pdf-bytes",
            mime_type="application/pdf",
            request_workflow_id="wf-second",
        )
    )

    assert first.status == "succeeded"
    assert second.status == "already_indexed"
    assert second.index_status == "indexed"
    assert second.source_document_id == first.source_document_id
    assert second.source_display_name == "report.pdf"
    assert second.indexed_chunk_count == 1
    assert second.embedded_chunk_count == 1
    assert len(embedding_client.requests) == 1
    assert parser_client.calls == 1

    session: Session = session_factory()
    try:
        assert session.query(SourceDocument).count() == 1
        source = session.query(SourceDocument).one()
        assert source.file_hash == hashlib.sha256(b"same-pdf-bytes").hexdigest()
        assert session.query(KnowledgeChunk).count() == 1
        workflow_runs = session.query(WorkflowRun).order_by(WorkflowRun.id).all()
        assert [run.status for run in workflow_runs] == ["succeeded", "succeeded"]
    finally:
        session.close()


def test_same_filename_with_different_file_hash_creates_new_indexed_source() -> None:
    session_factory = _build_session_factory()
    embedding_client = _FakeEmbeddingClient()
    parser_client = _FakePDFParserClient(
        documents=[
            _pdf_document("Version A content."),
            _pdf_document("Version B content."),
        ]
    )
    orchestrator = _build_orchestrator(
        session_factory, parser_client, embedding_client
    )

    first = asyncio.run(
        orchestrator.ingest_document(
            file_name="report.pdf",
            file_bytes=b"version-a",
            mime_type="application/pdf",
            request_workflow_id="wf-version-a",
        )
    )
    second = asyncio.run(
        orchestrator.ingest_document(
            file_name="report.pdf",
            file_bytes=b"version-b",
            mime_type="application/pdf",
            request_workflow_id="wf-version-b",
        )
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.source_document_id != first.source_document_id
    assert len(embedding_client.requests) == 2

    session: Session = session_factory()
    try:
        assert session.query(SourceDocument).count() == 2
        assert session.query(KnowledgeChunk).count() == 2
    finally:
        session.close()


def test_same_normalized_text_with_different_file_hash_is_not_an_exact_duplicate() -> None:
    session_factory = _build_session_factory()
    embedding_client = _FakeEmbeddingClient()
    parser_client = _FakePDFParserClient(
        documents=[
            _pdf_document("The same normalized PDF text."),
            _pdf_document("The same normalized PDF text."),
        ]
    )
    orchestrator = _build_orchestrator(
        session_factory, parser_client, embedding_client
    )

    first = asyncio.run(
        orchestrator.ingest_document(
            file_name="layout-a.pdf",
            file_bytes=b"raw-pdf-bytes-a",
            mime_type="application/pdf",
            request_workflow_id="wf-layout-a",
        )
    )
    second = asyncio.run(
        orchestrator.ingest_document(
            file_name="layout-b.pdf",
            file_bytes=b"raw-pdf-bytes-b",
            mime_type="application/pdf",
            request_workflow_id="wf-layout-b",
        )
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert first.content_hash == second.content_hash
    assert second.source_document_id != first.source_document_id
    assert len(embedding_client.requests) == 2

    session: Session = session_factory()
    try:
        sources = session.query(SourceDocument).order_by(SourceDocument.id).all()
        assert len(sources) == 2
        assert sources[0].file_hash != sources[1].file_hash
        assert session.query(KnowledgeChunk).count() == 2
    finally:
        session.close()


def test_pdf_inventory_returns_only_indexed_source_metadata_and_eligible_chunk_count() -> None:
    session_factory = _build_session_factory()
    session: Session = session_factory()
    try:
        indexed = SourceDocument(
            id=1,
            source_type="pdf",
            source_display_name="indexed.pdf",
            content_hash="indexed-hash",
            raw_text="private indexed text",
            owner_scope="local",
            status="indexed",
        )
        failed = SourceDocument(
            id=2,
            source_type="pdf",
            source_display_name="failed.pdf",
            content_hash="failed-hash",
            raw_text="private failed text",
            owner_scope="local",
            status="failed",
        )
        pending = SourceDocument(
            id=3,
            source_type="pdf",
            source_display_name="pending.pdf",
            content_hash="pending-hash",
            raw_text="private pending text",
            owner_scope="local",
            status="indexing",
        )
        other_owner = SourceDocument(
            id=4,
            source_type="pdf",
            source_display_name="other-owner.pdf",
            content_hash="other-owner-hash",
            raw_text="other owner text",
            owner_scope="other-owner",
            status="indexed",
        )
        session.add_all([indexed, failed, pending, other_owner])
        session.add_all(
            [
                KnowledgeChunk(
                    id=1,
                    source_document_id=1,
                    source_kind="pdf",
                    source_display_name="indexed.pdf",
                    chunk_index=0,
                    chunk_text="indexed chunk",
                    eligibility_status="eligible",
                    owner_scope="local",
                ),
                KnowledgeChunk(
                    id=2,
                    source_document_id=1,
                    source_kind="pdf",
                    source_display_name="indexed.pdf",
                    chunk_index=1,
                    chunk_text="incomplete chunk",
                    eligibility_status="pending",
                    owner_scope="local",
                ),
            ]
        )
        session.commit()

        summaries = SourceDocumentRepository(session).list_indexed_pdf_sources(
            owner_scope="local"
        )

        assert len(summaries) == 1
        assert summaries[0].id == 1
        assert summaries[0].display_name == "indexed.pdf"
        assert summaries[0].source_kind == "pdf"
        assert summaries[0].status == "indexed"
        assert summaries[0].chunk_count == 1
    finally:
        session.close()


def test_pdf_inventory_api_exposes_bounded_source_metadata_only() -> None:
    session_factory = _build_session_factory()
    session: Session = session_factory()
    try:
        source = SourceDocument(
            id=1,
            source_type="pdf",
            source_display_name="indexed.pdf",
            content_hash="indexed-hash",
            raw_text="private text must not be returned",
            owner_scope="local",
            status="indexed",
        )
        session.add(source)
        session.add(
            KnowledgeChunk(
                id=1,
                source_document_id=1,
                source_kind="pdf",
                source_display_name="indexed.pdf",
                chunk_index=0,
                chunk_text="private chunk must not be returned",
                eligibility_status="eligible",
                owner_scope="local",
            )
        )
        session.commit()
    finally:
        session.close()

    def _db_override():
        db_session = session_factory()
        try:
            yield db_session
        finally:
            db_session.close()

    app.dependency_overrides[get_db_session] = _db_override
    try:
        response = TestClient(app).get("/api/knowledge/sources")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert set(payload[0]) == {
        "id",
        "display_name",
        "source_kind",
        "status",
        "chunk_count",
        "updated_at",
    }
    assert payload[0]["display_name"] == "indexed.pdf"
    assert payload[0]["source_kind"] == "pdf"
    assert payload[0]["status"] == "indexed"
    assert payload[0]["chunk_count"] == 1
    assert "raw_text" not in payload[0]
    assert "chunk_text" not in payload[0]
    assert "embedding" not in payload[0]


def test_pdf_upload_api_returns_already_indexed_without_second_embedding() -> None:
    session_factory = _build_session_factory()
    embedding_client = _FakeEmbeddingClient()
    parser_client = _FakePDFParserClient(
        documents=[_pdf_document("The same API PDF snapshot.")]
    )
    registry = ToolRegistry()
    registry.register_tool(PDFParserTool(parser_client))

    def _db_override():
        db_session = session_factory()
        try:
            yield db_session
        finally:
            db_session.close()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_embedding_client] = lambda: embedding_client
    try:
        client = TestClient(app)
        first = client.post(
            "/api/ingest/document",
            files={"document": ("first.pdf", b"same-pdf-bytes", "application/pdf")},
        )
        second = client.post(
            "/api/ingest/document",
            files={"document": ("renamed.pdf", b"same-pdf-bytes", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert second_payload["status"] == "already_indexed"
    assert second_payload["index_status"] == "indexed"
    assert second_payload["source_document_id"] == first_payload["source_document_id"]
    assert second_payload["source_display_name"] == "first.pdf"
    assert second_payload["indexed_chunk_count"] == 1
    assert second_payload["embedded_chunk_count"] == 1
    assert len(embedding_client.requests) == 1
