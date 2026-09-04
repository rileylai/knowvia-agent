from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, WorkflowRun
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.orchestrators import NotionPageIndexOrchestrator
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    get_openai_embedding_capabilities,
)
from src.services import WorkflowRunService
from src.tools import (
    DEFAULT_MOCK_NOTION_DATA_DIR,
    JSONMockNotionReaderClient,
    MockNotionDataError,
    NotionReaderTool,
    ToolRegistry,
    load_mock_notion_pages,
)


class _FakeEmbeddingClient(EmbeddingClient):
    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(self, *, model: str, dimensions: int):
        return get_openai_embedding_capabilities(
            model=model,
            dimensions=dimensions,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        embeddings = [
            [float(index + 1)] * 1536
            for index, _ in enumerate(request.inputs)
        ]
        return EmbeddingResponse(
            provider="openai",
            model="text-embedding-3-small",
            embeddings=embeddings,
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
            KnowledgeChunk.__table__,
            WorkflowRun.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _find_block_path(blocks: list, block_id: str) -> str | None:
    for block in blocks:
        if block.block_id == block_id:
            return block.block_path
        found = _find_block_path(block.children, block_id)
        if found is not None:
            return found
    return None


def test_json_mock_notion_reader_client_loads_demo_pages() -> None:
    client = JSONMockNotionReaderClient.from_directory(DEFAULT_MOCK_NOTION_DATA_DIR)
    pages = load_mock_notion_pages(DEFAULT_MOCK_NOTION_DATA_DIR)

    page = client.fetch_page_tree("page-nlp-week5")

    assert page is not None
    assert set(pages) == {
        "page-iso-9001",
        "page-nlp-week5",
        "page-rag-basics",
    }
    assert page.notion_path == "Knowledge/NLP/Week5"
    assert (
        _find_block_path(page.blocks, "blk-ai-topic")
        == "Knowledge/NLP/Week5/AI Supplement Zone/2026-06-04/Positional Encoding Supplement"
    )


def test_json_mock_notion_reader_client_rejects_unsafe_demo_metadata(
    tmp_path: Path,
) -> None:
    payload = {
        "page_id": "unsafe-page",
        "title": "Unsafe Demo Page",
        "notion_path": "Knowledge/Unsafe",
        "demo_metadata": {
            "synthetic_content": False,
            "safe_for_public_demo": True,
            "contains_private_content": False,
            "scenario": "invalid"
        },
        "blocks": [],
    }
    json_path = tmp_path / "unsafe-page.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MockNotionDataError, match="synthetic_content"):
        JSONMockNotionReaderClient.from_directory(tmp_path)


def test_json_mock_notion_reader_client_indexes_demo_page_into_chunks() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    try:
        registry = ToolRegistry()
        registry.register_tool(
            NotionReaderTool(
                JSONMockNotionReaderClient.from_directory(DEFAULT_MOCK_NOTION_DATA_DIR)
            )
        )
        orchestrator = NotionPageIndexOrchestrator(
            tool_registry=registry,
            unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
            workflow_run_service=WorkflowRunService(session_factory),
            embedding_client=_FakeEmbeddingClient(),
        )

        snapshot = asyncio.run(
            orchestrator.index_page_snapshot(
                page_id="page-nlp-week5",
                request_workflow_id="wf-demo-index",
            )
        )

        assert snapshot.notion_page_id == "page-nlp-week5"
        assert snapshot.indexed_block_count == 12
        assert snapshot.indexed_chunk_count >= 3
        assert snapshot.embedding_provider == "openai"
        assert snapshot.embedding_model == "text-embedding-3-small"
        assert snapshot.embedding_dimensions == 1536

        stored_paths = {
            row[0]
            for row in session.query(KnowledgeChunk.notion_path).all()
        }
        assert (
            "Knowledge/NLP/Week5/AI Supplement Zone/2026-06-04/Positional Encoding Supplement"
            in stored_paths
        )
    finally:
        session.close()
