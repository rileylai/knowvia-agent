from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument, WorkflowRun
from src.app.main import app
from src.db.session import get_db_session
from src.orchestrators import QAOrchestrator
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.orchestrators import URLIngestionError, URLIngestionOrchestrator
from src.providers import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
)
from src.rag import ProductionChunkRetriever, RetrievedChunk
from src.repositories import ChunkRepository
from src.services import EmbeddingBatchService, WorkflowRunService
from src.tools import (
    ParsedURLArticle,
    ToolRegistry,
    URLArticleParserClient,
    URLArticleParserTool,
)


@dataclass
class _FakeURLArticleParserClient(URLArticleParserClient):
    articles: list[ParsedURLArticle]

    def __post_init__(self) -> None:
        self.calls = 0

    def parse_article(self, *, url: str) -> ParsedURLArticle:
        _ = url
        article = self.articles[min(self.calls, len(self.articles) - 1)]
        self.calls += 1
        return article


class _FakeEmbeddingClient(EmbeddingClient):
    def __init__(self, *, should_fail: bool = False) -> None:
        self.requests: list[EmbeddingRequest] = []
        self.should_fail = should_fail

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
        if self.should_fail:
            raise EmbeddingClientError("provider unavailable")
        return EmbeddingResponse(
            provider="fake",
            model=request.model or "text-embedding-3-small",
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
    registry.register_tool(URLArticleParserTool(parser_client))
    return URLIngestionOrchestrator(
        tool_registry=registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_batch_service=EmbeddingBatchService(
            embedding_client=embedding_client,
            model="text-embedding-3-small",
            dimensions=1536,
        ),
    )


def test_url_ingestion_reuses_generic_chunk_embedding_and_retrieval_pipeline() -> None:
    session_factory = _build_session_factory()
    embedding_client = _FakeEmbeddingClient()
    parser_client = _FakeURLArticleParserClient(
        [
            ParsedURLArticle(
                url="https://docs.example.com/agents",
                title="Bounded Agents Guide",
                raw_text=(
                    "Bounded execution keeps agent runs safe. "
                    "Retrieval should provide backend citations."
                ),
            )
        ]
    )
    orchestrator = _build_orchestrator(
        session_factory, parser_client, embedding_client
    )

    result = asyncio.run(
        orchestrator.ingest_url(
            url="https://example.com/agents",
            request_workflow_id="wf-url-success",
        )
    )

    assert result.status == "succeeded"
    assert result.index_status == "indexed"
    assert result.source_display_name == "Bounded Agents Guide"
    assert result.final_url == "https://docs.example.com/agents"
    assert result.indexed_chunk_count == 1
    assert result.embedded_chunk_count == 1
    assert len(embedding_client.requests) == 1

    session: Session = session_factory()
    try:
        source = session.get(SourceDocument, result.source_document_id)
        assert source is not None
        assert source.source_type == "url"
        assert source.status == "indexed"
        assert source.requested_url == "https://example.com/agents"
        assert source.final_url == "https://docs.example.com/agents"

        chunks = session.query(KnowledgeChunk).all()
        assert len(chunks) == 1
        assert chunks[0].source_kind == "url"
        assert chunks[0].eligibility_status == "eligible"
        assert chunks[0].locator == "chunk 1"
        citation_metadata = json.loads(chunks[0].citation_metadata or "{}")
        assert citation_metadata["final_url"] == "https://docs.example.com/agents"
        assert citation_metadata["requested_url"] == "https://example.com/agents"

        retrieved = ProductionChunkRetriever(
            chunk_repository=ChunkRepository(session)
        ).retrieve(
            query_text="backend citations",
            source_kinds=["url"],
            top_k=3,
        )
        assert len(retrieved) == 1
        assert retrieved[0].source_kind == "url"
        assert retrieved[0].source_url == "https://docs.example.com/agents"
        default_retrieved = ProductionChunkRetriever(
            chunk_repository=ChunkRepository(session)
        ).retrieve(query_text="backend citations", top_k=3)
        assert [chunk.source_kind for chunk in default_retrieved] == ["url"]
    finally:
        session.close()


def test_url_exact_duplicate_reuses_indexed_snapshot_without_reembedding() -> None:
    session_factory = _build_session_factory()
    embedding_client = _FakeEmbeddingClient()
    parser_client = _FakeURLArticleParserClient(
        [
            ParsedURLArticle(
                url="https://docs.example.com/agents",
                title="Bounded Agents Guide",
                raw_text="Stable bounded execution guidance.",
            ),
            ParsedURLArticle(
                url="https://docs.example.com/agents",
                title="Renamed Bounded Agents Guide",
                raw_text="Stable bounded execution guidance.",
            ),
            ParsedURLArticle(
                url="https://docs.example.com/agents",
                title="Updated Bounded Agents Guide",
                raw_text="Changed bounded execution guidance.",
            ),
        ]
    )
    orchestrator = _build_orchestrator(
        session_factory, parser_client, embedding_client
    )

    first = asyncio.run(
        orchestrator.ingest_url(
            url="https://example.com/agents",
            request_workflow_id="wf-url-first",
        )
    )
    duplicate = asyncio.run(
        orchestrator.ingest_url(
            url="https://example.com/agents",
            request_workflow_id="wf-url-duplicate",
        )
    )
    changed = asyncio.run(
        orchestrator.ingest_url(
            url="https://example.com/agents",
            request_workflow_id="wf-url-changed",
        )
    )

    assert first.status == "succeeded"
    assert duplicate.status == "already_indexed"
    assert duplicate.source_document_id == first.source_document_id
    assert duplicate.source_display_name == "Bounded Agents Guide"
    assert changed.status == "succeeded"
    assert changed.source_document_id != first.source_document_id
    assert len(embedding_client.requests) == 2

    session: Session = session_factory()
    try:
        assert session.query(SourceDocument).count() == 2
        assert session.query(KnowledgeChunk).count() == 2
        assert [source.status for source in session.query(SourceDocument).all()] == [
            "indexed",
            "indexed",
        ]
    finally:
        session.close()


def test_url_embedding_failure_marks_snapshot_failed_without_eligible_chunks() -> None:
    session_factory = _build_session_factory()
    orchestrator = _build_orchestrator(
        session_factory,
        _FakeURLArticleParserClient(
            [
                ParsedURLArticle(
                    url="https://docs.example.com/failure",
                    raw_text="Content that should never become searchable.",
                )
            ]
        ),
        _FakeEmbeddingClient(should_fail=True),
    )

    with pytest.raises(URLIngestionError) as error:
        asyncio.run(
            orchestrator.ingest_url(
                url="https://example.com/failure",
                request_workflow_id="wf-url-embedding-failure",
            )
        )

    assert error.value.error_code == "EMBEDDING_PROVIDER_ERROR"
    session: Session = session_factory()
    try:
        source = session.query(SourceDocument).one()
        assert source.status == "failed"
        assert session.query(KnowledgeChunk).count() == 0
    finally:
        session.close()


def test_url_source_is_exposed_by_generic_inventory_and_backend_citation() -> None:
    session_factory = _build_session_factory()
    result = asyncio.run(
        _build_orchestrator(
            session_factory,
            _FakeURLArticleParserClient(
                [
                    ParsedURLArticle(
                        url="https://docs.example.com/inventory",
                        title="Inventory URL Guide",
                        raw_text="URL inventory evidence for grounded answers.",
                    )
                ]
            ),
            _FakeEmbeddingClient(),
        ).ingest_url(
            url="https://example.com/inventory",
            request_workflow_id="wf-url-inventory",
        )
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    try:
        response = TestClient(app).get("/api/knowledge/sources")
        assert response.status_code == 200
        inventory = response.json()
        assert len(inventory) == 1
        assert inventory[0]["id"] == result.source_document_id
        assert inventory[0]["display_name"] == "Inventory URL Guide"
        assert inventory[0]["source_kind"] == "url"
        assert inventory[0]["status"] == "indexed"
        assert inventory[0]["chunk_count"] == 1
        assert inventory[0]["source_url"] == "https://docs.example.com/inventory"
        assert inventory[0]["updated_at"] is not None
    finally:
        app.dependency_overrides.clear()

    citation = QAOrchestrator.__new__(QAOrchestrator)._build_citations(
        [
            RetrievedChunk(
                chunk_id=1,
                chunk_index=0,
                chunk_text="URL inventory evidence for grounded answers.",
                notion_path="",
                notion_page_id=None,
                source_kind="url",
                score=0.91,
                source_display_name="Inventory URL Guide",
                locator="chunk 1",
                source_url="https://docs.example.com/inventory",
            )
        ]
    )
    assert citation[0].source_kind == "url"
    assert citation[0].source_url == "https://docs.example.com/inventory"
