from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import (
    KnowledgeChunk,
    NotionBlock,
    NotionPage,
    SourceDocument,
    WorkflowRun,
)
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.orchestrators import DocumentIngestionError, DocumentIngestionOrchestrator
from src.providers import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
)
from src.rag import ProductionChunkRetriever
from src.repositories import ChunkRepository
from src.services import EmbeddingBatchService, WorkflowRunService
from src.tools import (
    PDFParserClient,
    PDFParserTool,
    ParsedPDFDocument,
    ToolRegistry,
)


@dataclass
class _FakePDFParserClient(PDFParserClient):
    raw_text: str
    pages: list[str]

    def parse_document(self, *, file_name: str, file_bytes: bytes) -> ParsedPDFDocument:
        _ = file_name, file_bytes
        return ParsedPDFDocument(
            raw_text=self.raw_text,
            page_count=len(self.pages),
            pages=list(self.pages),
        )


class _FakeEmbeddingClient(EmbeddingClient):
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.requests: list[EmbeddingRequest] = []

    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        return EmbeddingCapabilities(
            provider="openai",
            model=model,
            dimensions=dimensions,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        if self.should_fail:
            from src.providers import EmbeddingClientError

            raise EmbeddingClientError("provider unavailable")
        return EmbeddingResponse(
            provider="openai",
            model=request.model or "text-embedding-3-small",
            embeddings=[[float(index + 1)] * 1536 for index, _ in enumerate(request.inputs)],
            indices=list(range(len(request.inputs))),
            token_input=len(request.inputs) * 10,
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


def _build_orchestrator(session_factory, embedding_client: EmbeddingClient):
    registry = ToolRegistry()
    registry.register_tool(
        PDFParserTool(
            _FakePDFParserClient(
                raw_text="Agent systems need bounded execution.\n\nRetrieval needs citations.",
                pages=[
                    "Agent systems need bounded execution.",
                    "Retrieval needs citations.",
                ],
            )
        )
    )
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


def test_pdf_ingestion_persists_source_document_and_embedded_knowledge_chunks() -> None:
    session_factory = _build_session_factory()
    embedding_client = _FakeEmbeddingClient()
    orchestrator = _build_orchestrator(session_factory, embedding_client)

    result = asyncio.run(
        orchestrator.ingest_document(
            file_name="agent-notes.pdf",
            file_bytes=b"%PDF-1.7 fixture",
            mime_type="application/pdf",
            request_workflow_id="wf-pdf-success",
        )
    )

    assert result.status == "succeeded"
    assert result.index_status == "indexed"
    assert result.indexed_chunk_count == 2
    assert result.embedded_chunk_count == 2
    assert len(embedding_client.requests) == 1
    assert embedding_client.requests[0].inputs == [
        "Agent systems need bounded execution.",
        "Retrieval needs citations.",
    ]

    session: Session = session_factory()
    try:
        source_document = session.get(SourceDocument, result.source_document_id)
        assert source_document is not None
        assert source_document.source_type == "pdf"
        assert source_document.status == "indexed"
        assert source_document.owner_scope == "local"

        chunks = session.query(KnowledgeChunk).order_by(KnowledgeChunk.chunk_index).all()
        assert len(chunks) == 2
        assert {chunk.source_kind for chunk in chunks} == {"pdf"}
        assert {chunk.source_document_id for chunk in chunks} == {source_document.id}
        assert {chunk.eligibility_status for chunk in chunks} == {"eligible"}
        assert {chunk.locator for chunk in chunks} == {"page 1", "page 2"}
        assert {chunk.embedding_model for chunk in chunks} == {"text-embedding-3-small"}
        assert {chunk.embedding_dimensions for chunk in chunks} == {1536}
        assert all(len(chunk.embedding or []) == 1536 for chunk in chunks)
    finally:
        session.close()


def test_pdf_embedding_failure_marks_snapshot_ineligible_and_not_searchable() -> None:
    session_factory = _build_session_factory()
    orchestrator = _build_orchestrator(
        session_factory,
        _FakeEmbeddingClient(should_fail=True),
    )

    with pytest.raises(DocumentIngestionError) as error:
        asyncio.run(
            orchestrator.ingest_document(
                file_name="failed.pdf",
                file_bytes=b"%PDF-1.7 fixture",
                mime_type="application/pdf",
                request_workflow_id="wf-pdf-failure",
            )
        )

    assert error.value.error_code == "EMBEDDING_PROVIDER_ERROR"
    session: Session = session_factory()
    try:
        source_document = session.query(SourceDocument).one()
        assert source_document.status == "failed"
        assert session.query(KnowledgeChunk).count() == 0
        workflow_run = session.get(WorkflowRun, error.value.workflow_run_id)
        assert workflow_run is not None
        assert workflow_run.status == "failed"
    finally:
        session.close()


def test_retriever_includes_indexed_pdf_chunks_and_excludes_incomplete_snapshots() -> None:
    session_factory = _build_session_factory()
    session: Session = session_factory()
    try:
        indexed = SourceDocument(
            id=1,
            source_type="pdf",
            source_display_name="indexed.pdf",
            content_hash="indexed-hash",
            raw_text="Indexed PDF evidence",
            owner_scope="local",
            status="indexed",
        )
        failed = SourceDocument(
            id=2,
            source_type="pdf",
            source_display_name="failed.pdf",
            content_hash="failed-hash",
            raw_text="Failed PDF evidence",
            owner_scope="local",
            status="failed",
        )
        session.add_all([indexed, failed])
        session.flush()
        session.add_all(
            [
                KnowledgeChunk(
                    id=1,
                    source_document_id=indexed.id,
                    chunk_index=0,
                    chunk_text="Indexed PDF evidence about bounded agents",
                    source_kind="pdf",
                    source_display_name="indexed.pdf",
                    locator="page 1",
                    eligibility_status="eligible",
                    embedding_text="[1.0, 0.0]",
                ),
                KnowledgeChunk(
                    id=2,
                    source_document_id=failed.id,
                    chunk_index=0,
                    chunk_text="Failed PDF evidence about bounded agents",
                    source_kind="pdf",
                    source_display_name="failed.pdf",
                    locator="page 1",
                    eligibility_status="eligible",
                    embedding_text="[1.0, 0.0]",
                ),
            ]
        )
        session.commit()

        results = ProductionChunkRetriever(
            chunk_repository=ChunkRepository(session)
        ).retrieve(query_text="bounded agents", top_k=5)

        assert [chunk.chunk_id for chunk in results] == [1]
        assert results[0].source_kind == "pdf"
        assert results[0].source_display_name == "indexed.pdf"
        assert results[0].locator == "page 1"
    finally:
        session.close()
