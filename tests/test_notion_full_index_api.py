from __future__ import annotations

import asyncio
import json
from typing import Dict

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import get_embedding_client, get_tool_registry
from src.app.main import app
from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, WorkflowRun
from src.db.session import get_db_session, get_db_session_factory, get_unit_of_work_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.observability.external_error import (
    ExternalErrorCategory,
    ExternalErrorDiagnostic,
)
from src.orchestrators import (
    NotionFullIndexOrchestrator,
    NotionPageIndexError,
    NotionPageIndexOrchestrator,
)
from src.providers import (
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
    get_openai_embedding_capabilities,
)
from src.services import (
    EmbeddingBatchLimits,
    EmbeddingBatchService,
    InfrastructureExecutionTimeout,
    WorkflowRunService,
)
from src.tools import (
    InMemoryNotionReaderClient,
    NotionBlockNode,
    NotionPageTree,
    NotionPageSummary,
    NotionReaderClient,
    NotionReaderTool,
    ToolRegistry,
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
        return EmbeddingResponse(
            provider="openai",
            model="text-embedding-3-small",
            embeddings=[[1.0] * 1536 for _ in request.inputs],
            indices=list(range(len(request.inputs))),
            token_input=len(request.inputs) * 10,
        )


class _FailOnThirdEmbeddingCall(_FakeEmbeddingClient):
    def __init__(self) -> None:
        self.call_count = 0

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.call_count += 1
        if self.call_count == 3:
            raise EmbeddingClientError(
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.REQUEST_INVALID,
                    retryable=False,
                    http_status=400,
                )
            )
        return await super().embed(request)


class _TimeoutOnPageReader(NotionReaderClient):
    def __init__(self, pages: Dict[str, NotionPageTree], timeout_page_id: str) -> None:
        self._pages = pages
        self._timeout_page_id = timeout_page_id

    def fetch_page_tree(self, page_id: str):
        if page_id == self._timeout_page_id:
            raise InfrastructureExecutionTimeout()
        return self._pages.get(page_id)

    def list_pages(self):
        return [
            NotionPageSummary(page_id=page.page_id, title=page.title)
            for page in sorted(self._pages.values(), key=lambda item: item.page_id)
        ]


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


def _tree(page_id: str, title: str, block_id: str, text: str) -> NotionPageTree:
    path = f"Knowledge/{title}"
    return NotionPageTree(
        page_id=page_id,
        title=title,
        notion_path=path,
        blocks=[
            NotionBlockNode(
                block_id=block_id,
                block_type="heading_2",
                content_text=text,
                block_path=f"{path}/{text}",
            )
        ],
    )


def _three_chunk_tree(page_id: str, title: str, prefix: str) -> NotionPageTree:
    path = f"Knowledge/{title}"
    return NotionPageTree(
        page_id=page_id,
        title=title,
        notion_path=path,
        blocks=[
            NotionBlockNode(
                block_id=f"{prefix}-{index}",
                block_type="heading_2",
                content_text=f"{prefix} section {index}",
                block_path=f"{path}/{prefix} section {index}",
            )
            for index in range(3)
        ],
    )


def _page_orchestrator(
    *,
    session_factory,
    pages: Dict[str, NotionPageTree],
    embedding_service: EmbeddingBatchService,
) -> tuple[ToolRegistry, NotionPageIndexOrchestrator]:
    registry = ToolRegistry()
    registry.register_tool(NotionReaderTool(InMemoryNotionReaderClient(pages)))
    return registry, NotionPageIndexOrchestrator(
        tool_registry=registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_batch_service=embedding_service,
    )


def _configure_overrides(
    session_factory,
    pages: Dict[str, NotionPageTree],
) -> None:
    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(
            NotionReaderTool(InMemoryNotionReaderClient(pages))
        )
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _FakeEmbeddingClient


def test_full_index_discovers_external_page_ids_and_status() -> None:
    session_factory = _build_session_factory()
    pages = {
        "external-page-1": _tree(
            "external-page-1", "Page One", "block-one", "Section One"
        ),
        "external-page-2": _tree(
            "external-page-2", "Page Two", "block-two", "Section Two"
        ),
    }
    _configure_overrides(session_factory, pages)

    try:
        client = TestClient(app)
        response = client.post("/api/notion/index/full")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["discovered_page_count"] == 2
        assert payload["processed_page_count"] == 2
        assert {page["page_id"] for page in payload["indexed_pages"]} == {
            "external-page-1",
            "external-page-2",
        }

        status_response = client.get(
            "/api/notion/index/status",
            params={"workflow_run_id": payload["workflow_run_id"]},
        )
        assert status_response.status_code == 200
        status = status_response.json()
        assert status["workflow_run_id"] == payload["workflow_run_id"]
        assert status["status"] == "succeeded"
        assert status["metadata"]["operation"] == "index_full"
        assert status["metadata"]["discovered_page_count"] == 2

        session: Session = session_factory()
        try:
            assert session.query(NotionPage).count() == 2
            assert session.query(NotionBlock).count() == 2
            assert session.query(KnowledgeChunk).count() == 2
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_repeated_full_index_replaces_stale_blocks_without_duplicates() -> None:
    session_factory = _build_session_factory()
    pages = {
        "external-page-1": _tree(
            "external-page-1", "Page One", "old-block", "Old Section"
        )
    }
    _configure_overrides(session_factory, pages)

    try:
        client = TestClient(app)
        first_response = client.post("/api/notion/index/full")
        assert first_response.status_code == 200

        pages["external-page-1"] = _tree(
            "external-page-1", "Page One", "new-block", "New Section"
        )
        second_response = client.post("/api/notion/index/full")
        assert second_response.status_code == 200

        session: Session = session_factory()
        try:
            assert session.query(NotionPage).count() == 1
            blocks = session.query(NotionBlock).all()
            assert {block.notion_block_id for block in blocks} == {"new-block"}
            chunks = session.query(KnowledgeChunk).all()
            assert len(chunks) == 1
            workflow_runs = (
                session.query(WorkflowRun)
                .order_by(WorkflowRun.id.asc())
                .all()
            )
            assert len(workflow_runs) == 2
            assert all(run.status == "succeeded" for run in workflow_runs)
        finally:
            session.close()

        latest_status = client.get("/api/notion/index/status")
        assert latest_status.status_code == 200
        assert latest_status.json()["workflow_run_id"] == second_response.json()[
            "workflow_run_id"
        ]
    finally:
        app.dependency_overrides.clear()


def test_index_status_returns_not_found_for_unknown_workflow() -> None:
    session_factory = _build_session_factory()
    _configure_overrides(session_factory, {})

    try:
        client = TestClient(app)
        response = client.get(
            "/api/notion/index/status",
            params={"workflow_run_id": 999},
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error_code"] == (
            "NOTION_INDEX_STATUS_NOT_FOUND"
        )
    finally:
        app.dependency_overrides.clear()


def test_full_index_middle_page_batch_failure_preserves_existing_partial_outcome() -> None:
    session_factory = _build_session_factory()
    pages = {
        "page-1": _tree("page-1", "Page One", "page-1-old", "Old One"),
        "page-2": _tree("page-2", "Page Two", "page-2-old", "Old Two"),
        "page-3": _tree("page-3", "Page Three", "page-3-old", "Old Three"),
    }
    seed_client = _FakeEmbeddingClient()
    seed_service = EmbeddingBatchService(
        embedding_client=seed_client,
        model="text-embedding-3-small",
        dimensions=1536,
        limits=EmbeddingBatchLimits(max_inputs=1),
        token_counter=lambda value: 1,
    )
    _, seed_page_orchestrator = _page_orchestrator(
        session_factory=session_factory,
        pages=pages,
        embedding_service=seed_service,
    )
    for page_id in pages:
        asyncio.run(
            seed_page_orchestrator.index_page_snapshot(
                page_id=page_id,
                request_workflow_id="seed-full-index-partial-outcome",
            )
        )

    pages["page-1"] = _tree("page-1", "Page One", "page-1-new", "New One")
    pages["page-2"] = _three_chunk_tree("page-2", "Page Two", "page-2-new")
    pages["page-3"] = _tree("page-3", "Page Three", "page-3-new", "New Three")
    failing_client = _FailOnThirdEmbeddingCall()
    failing_service = EmbeddingBatchService(
        embedding_client=failing_client,
        model="text-embedding-3-small",
        dimensions=1536,
        limits=EmbeddingBatchLimits(max_inputs=1),
        token_counter=lambda value: 1,
    )
    registry, page_orchestrator = _page_orchestrator(
        session_factory=session_factory,
        pages=pages,
        embedding_service=failing_service,
    )
    full_orchestrator = NotionFullIndexOrchestrator(
        tool_registry=registry,
        page_index_orchestrator=page_orchestrator,
        workflow_run_service=WorkflowRunService(session_factory),
    )

    with pytest.raises(NotionPageIndexError) as exc_info:
        asyncio.run(
            full_orchestrator.index_all(
                request_workflow_id="wf-full-index-middle-batch-failure"
            )
        )

    assert exc_info.value.error_code == "EMBEDDING_PROVIDER_ERROR"
    assert failing_client.call_count == 3
    session: Session = session_factory()
    try:
        blocks_by_page = {
            page.notion_page_id: {
                block.notion_block_id
                for block in session.query(NotionBlock)
                .filter(NotionBlock.notion_page_id == page.id)
                .all()
            }
            for page in session.query(NotionPage).all()
        }
        assert blocks_by_page == {
            "page-1": {"page-1-new"},
            "page-2": {"page-2-old"},
            "page-3": {"page-3-old"},
        }
        workflow = session.query(WorkflowRun).one()
        metadata = json.loads(workflow.metadata_json or "{}")
        assert workflow.status == "failed"
        assert workflow.failure_reason == "EMBEDDING_PROVIDER_ERROR"
        assert metadata["processed_page_count"] == 1
        assert metadata["succeeded_page_ids"] == ["page-1"]
        assert metadata["failed_page_id"] == "page-2"
        assert metadata["failed_page_index"] == 1
        assert metadata["remaining_page_ids"] == ["page-3"]
    finally:
        session.close()


def test_full_index_queue_timeout_preserves_failed_page_replacement_atomicity() -> None:
    session_factory = _build_session_factory()
    initial_pages = {
        "page-1": _tree("page-1", "Page One", "page-1-old", "Old One"),
        "page-2": _tree("page-2", "Page Two", "page-2-old", "Old Two"),
    }
    seed_service = EmbeddingBatchService(
        embedding_client=_FakeEmbeddingClient(),
        model="text-embedding-3-small",
        dimensions=1536,
        limits=EmbeddingBatchLimits(max_inputs=1),
        token_counter=lambda value: 1,
    )
    _, seed_page_orchestrator = _page_orchestrator(
        session_factory=session_factory,
        pages=initial_pages,
        embedding_service=seed_service,
    )
    for page_id in initial_pages:
        asyncio.run(
            seed_page_orchestrator.index_page_snapshot(
                page_id=page_id,
                request_workflow_id="seed-timeout-atomicity",
            )
        )

    updated_pages = {
        "page-1": _tree("page-1", "Page One", "page-1-new", "New One"),
        "page-2": _tree("page-2", "Page Two", "page-2-new", "New Two"),
    }
    registry = ToolRegistry()
    registry.register_tool(
        NotionReaderTool(_TimeoutOnPageReader(updated_pages, "page-2"))
    )
    page_orchestrator = NotionPageIndexOrchestrator(
        tool_registry=registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_batch_service=seed_service,
    )
    full_orchestrator = NotionFullIndexOrchestrator(
        tool_registry=registry,
        page_index_orchestrator=page_orchestrator,
        workflow_run_service=WorkflowRunService(session_factory),
    )

    with pytest.raises(InfrastructureExecutionTimeout):
        asyncio.run(
            full_orchestrator.index_all(
                request_workflow_id="wf-full-index-timeout-atomicity"
            )
        )

    session: Session = session_factory()
    try:
        blocks_by_page = {
            page.notion_page_id: {
                block.notion_block_id
                for block in session.query(NotionBlock)
                .filter(NotionBlock.notion_page_id == page.id)
                .all()
            }
            for page in session.query(NotionPage).all()
        }
        assert blocks_by_page == {
            "page-1": {"page-1-new"},
            "page-2": {"page-2-old"},
        }
        workflow = session.query(WorkflowRun).one()
        metadata = json.loads(workflow.metadata_json or "{}")
        assert workflow.status == "failed"
        assert workflow.failure_reason == "QUEUE_JOB_TIMEOUT"
        assert metadata["processed_page_count"] == 1
        assert metadata["failed_page_id"] == "page-2"
        assert metadata["error_code"] == "QUEUE_JOB_TIMEOUT"
    finally:
        session.close()
