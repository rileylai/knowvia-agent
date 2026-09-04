from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import TracebackType
from typing import Dict, Optional, Type

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
from src.db.unit_of_work import SessionFactory, SqlAlchemyUnitOfWork
from src.orchestrators.notion_page_index_orchestrator import (
    NotionPageIndexError,
    NotionPageIndexOrchestrator,
)
from src.observability.external_error import (
    ExternalErrorCategory,
    ExternalErrorDiagnostic,
)
from src.providers import (
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
    get_openai_embedding_capabilities,
)
from src.repositories import (
    ChunkRepository,
    ChunkRepositoryError,
    NotionBlockRepository,
    NotionBlockSnapshot,
    NotionChunkUpsert,
)
from src.services import (
    EmbeddingBatchLimits,
    EmbeddingBatchService,
    WorkflowRunService,
)
from src.tools import (
    InMemoryNotionReaderClient,
    NotionBlockNode,
    NotionPageTree,
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


class _SelectedBatchFailingEmbeddingClient(_FakeEmbeddingClient):
    def __init__(self, *, fail_at_call: int) -> None:
        self.call_count = 0
        self._fail_at_call = fail_at_call

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.call_count += 1
        if self.call_count == self._fail_at_call:
            raise EmbeddingClientError(
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.REQUEST_INVALID,
                    retryable=False,
                    http_status=400,
                )
            )
        return await super().embed(request)


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


def _build_tool_registry(pages: Dict[str, NotionPageTree]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(NotionReaderTool(InMemoryNotionReaderClient(pages)))
    return registry


def _embedding_client_override() -> EmbeddingClient:
    return _FakeEmbeddingClient()


def _embedding_vector(fill_value: float) -> list[float]:
    return [fill_value] * 1536


def _sample_tree_v1() -> NotionPageTree:
    return NotionPageTree(
        page_id="page-1",
        title="NLP Week 5",
        notion_path="Knowledge/NLP/Week5",
        blocks=[
            NotionBlockNode(
                block_id="blk-attention",
                block_type="heading_2",
                content_text="Attention",
                block_path="Knowledge/NLP/Week5/Attention",
                children=[
                    NotionBlockNode(
                        block_id="blk-sdp",
                        block_type="bulleted_list_item",
                        content_text="Scaled dot-product attention",
                        block_path="Knowledge/NLP/Week5/Attention/Scaled dot-product attention",
                    )
                ],
            )
        ],
    )


def _sample_tree_v2() -> NotionPageTree:
    return NotionPageTree(
        page_id="page-1",
        title="NLP Week 5 (Updated)",
        notion_path="Knowledge/NLP/Week5",
        blocks=[
            NotionBlockNode(
                block_id="blk-summary",
                block_type="paragraph",
                content_text="Transformer recap",
                block_path="Knowledge/NLP/Week5/Transformer recap",
            )
        ],
    )


def _sample_tree_mixed_types_with_untrusted_paths() -> NotionPageTree:
    return NotionPageTree(
        page_id="page-mixed",
        title="Mixed Path Page",
        notion_path="Knowledge/Mixed",
        blocks=[
            NotionBlockNode(
                block_id="blk-h1",
                block_type="heading_2",
                content_text="Root Heading",
                block_path="INVALID/PATH",
                children=[
                    NotionBlockNode(
                        block_id="blk-toggle",
                        block_type="toggle",
                        content_text="Toggle Topic",
                        block_path="INVALID/PATH",
                        children=[
                            NotionBlockNode(
                                block_id="blk-child-page",
                                block_type="child_page",
                                content_text="Child Page A",
                                block_path="INVALID/PATH",
                                children=[
                                    NotionBlockNode(
                                        block_id="blk-leaf",
                                        block_type="paragraph",
                                        content_text="Leaf Note",
                                        block_path="INVALID/PATH",
                                    )
                                ],
                            )
                        ],
                    )
                ],
            )
        ],
    )


def _sample_incremental_v1() -> NotionPageTree:
    return NotionPageTree(
        page_id="page-sync",
        title="Sync Test Page",
        notion_path="Knowledge/Sync",
        blocks=[
            NotionBlockNode(
                block_id="blk-old-a",
                block_type="heading_2",
                content_text="Old Section A",
                block_path="Knowledge/Sync/Old Section A",
                children=[
                    NotionBlockNode(
                        block_id="blk-old-a-p",
                        block_type="paragraph",
                        content_text="Old note A",
                        block_path="Knowledge/Sync/Old Section A/Old note A",
                    )
                ],
            ),
            NotionBlockNode(
                block_id="blk-old-b",
                block_type="heading_2",
                content_text="Old Section B",
                block_path="Knowledge/Sync/Old Section B",
                children=[
                    NotionBlockNode(
                        block_id="blk-old-b-p",
                        block_type="paragraph",
                        content_text="Old note B",
                        block_path="Knowledge/Sync/Old Section B/Old note B",
                    )
                ],
            ),
        ],
    )


def _sample_incremental_v2_after_manual_delete() -> NotionPageTree:
    return NotionPageTree(
        page_id="page-sync",
        title="Sync Test Page",
        notion_path="Knowledge/Sync",
        blocks=[
            NotionBlockNode(
                block_id="blk-old-b",
                block_type="heading_2",
                content_text="Old Section B",
                block_path="Knowledge/Sync/Old Section B",
                children=[
                    NotionBlockNode(
                        block_id="blk-old-b-p",
                        block_type="paragraph",
                        content_text="Old note B (edited)",
                        block_path="Knowledge/Sync/Old Section B/Old note B (edited)",
                    )
                ],
            )
        ],
    )


def _sample_legacy_backfill_page() -> NotionPageTree:
    return NotionPageTree(
        page_id="page-legacy-a",
        title="Legacy Vector Page",
        notion_path="Knowledge/Legacy/A",
        blocks=[
            NotionBlockNode(
                block_id="blk-legacy-a-heading",
                block_type="heading_2",
                content_text="Recovered Section",
                block_path="Knowledge/Legacy/A/Recovered Section",
                children=[
                    NotionBlockNode(
                        block_id="blk-legacy-a-note",
                        block_type="paragraph",
                        content_text="Legacy page re-index restores vector state",
                        block_path=(
                            "Knowledge/Legacy/A/Recovered Section/"
                            "Legacy page re-index restores vector state"
                        ),
                    )
                ],
            )
        ],
    )


def _rollback_tree_v2() -> NotionPageTree:
    return NotionPageTree(
        page_id="page-rollback",
        title="Rollback Page Updated",
        notion_path="Knowledge/Rollback/Updated",
        blocks=[
            NotionBlockNode(
                block_id="blk-rollback-new",
                block_type="paragraph",
                content_text="New content should not survive rollback",
                block_path="Knowledge/Rollback/Updated/New content",
            )
        ],
    )


def _rollback_tree_with_three_chunks() -> NotionPageTree:
    path = "Knowledge/Rollback/Updated"
    return NotionPageTree(
        page_id="page-rollback",
        title="Rollback Page Updated",
        notion_path=path,
        blocks=[
            NotionBlockNode(
                block_id=f"blk-rollback-new-{index}",
                block_type="heading_2",
                content_text=f"Updated section {index}",
                block_path=f"{path}/Updated section {index}",
            )
            for index in range(3)
        ],
    )


def _seed_existing_rollback_page(session_factory: SessionFactory) -> None:
    session: Session = session_factory()
    try:
        page = NotionPage(
            id=11,
            notion_page_id="page-rollback",
            title="Rollback Page Original",
            notion_path="Knowledge/Rollback/Original",
        )
        session.add(page)
        session.flush()

        block = NotionBlock(
            id=21,
            notion_block_id="blk-rollback-old",
            notion_page_id=page.id,
            parent_block_id=None,
            block_type="paragraph",
            content_text="Old content must remain",
            block_path="Knowledge/Rollback/Original/Old content",
            block_order=0,
        )
        session.add(block)
        session.flush()

        session.add(
            KnowledgeChunk(
                id=31,
                source_document_id=None,
                notion_block_id=block.id,
                chunk_index=0,
                chunk_text="Old content must remain",
                notion_path="Knowledge/Rollback/Original/Old content",
                embedding=_embedding_vector(8.0),
                embedding_text=json.dumps(_embedding_vector(8.0)),
                source_kind="notion",
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_existing_stale_page(session_factory: SessionFactory) -> None:
    session: Session = session_factory()
    try:
        page = NotionPage(
            id=41,
            notion_page_id="page-stale",
            title="Current Page",
            notion_path="Knowledge/Current",
            last_edited_time=datetime(2026, 7, 27, 12, tzinfo=timezone.utc),
        )
        session.add(page)
        session.flush()
        session.add(
            NotionBlock(
                id=42,
                notion_block_id="blk-current",
                notion_page_id=page.id,
                parent_block_id=None,
                block_type="paragraph",
                content_text="Current content",
                block_path="Knowledge/Current/Current content",
                block_order=0,
            )
        )
        session.commit()
    finally:
        session.close()


def test_index_page_snapshot_rejects_stale_reader_payload_without_replacement() -> None:
    session_factory = _build_session_factory()
    _seed_existing_stale_page(session_factory)
    stale_tree = NotionPageTree(
        page_id="page-stale",
        title="Older Page",
        notion_path="Knowledge/Older",
        last_edited_time=datetime(2026, 7, 27, 11, tzinfo=timezone.utc),
        blocks=[
            NotionBlockNode(
                block_id="blk-older",
                block_type="paragraph",
                content_text="Older content",
                block_path="Knowledge/Older/Older content",
            )
        ],
    )
    orchestrator = NotionPageIndexOrchestrator(
        tool_registry=_build_tool_registry({"page-stale": stale_tree}),
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_client=_FakeEmbeddingClient(),
    )

    with pytest.raises(NotionPageIndexError) as exc_info:
        asyncio.run(
            orchestrator.index_page_snapshot(
                page_id="page-stale",
                request_workflow_id="wf-stale",
            )
        )

    assert exc_info.value.error_code == "STALE_PAGE_SNAPSHOT"
    assert exc_info.value.http_status_code == 409

    session = session_factory()
    try:
        page = (
            session.query(NotionPage)
            .filter(NotionPage.notion_page_id == "page-stale")
            .one()
        )
        block = (
            session.query(NotionBlock)
            .filter(NotionBlock.notion_page_id == page.id)
            .one()
        )
        assert page.title == "Current Page"
        assert page.notion_path == "Knowledge/Current"
        assert block.notion_block_id == "blk-current"
        assert block.content_text == "Current content"
    finally:
        session.close()


def _assert_existing_rollback_page_is_unchanged(
    session_factory: SessionFactory,
) -> None:
    session: Session = session_factory()
    try:
        page = (
            session.query(NotionPage)
            .filter(NotionPage.notion_page_id == "page-rollback")
            .one()
        )
        assert page.title == "Rollback Page Original"
        assert page.notion_path == "Knowledge/Rollback/Original"

        blocks = (
            session.query(NotionBlock)
            .filter(NotionBlock.notion_page_id == page.id)
            .order_by(NotionBlock.id.asc())
            .all()
        )
        assert len(blocks) == 1
        assert blocks[0].notion_block_id == "blk-rollback-old"
        assert blocks[0].content_text == "Old content must remain"

        chunks = (
            session.query(KnowledgeChunk)
            .join(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
            .filter(NotionBlock.notion_page_id == page.id)
            .order_by(KnowledgeChunk.id.asc())
            .all()
        )
        assert len(chunks) == 1
        assert chunks[0].chunk_text == "Old content must remain"
        assert chunks[0].embedding == _embedding_vector(8.0)
    finally:
        session.close()


class _FailingChunkRepository:
    def __init__(self, repository: ChunkRepository, fail_stage: str) -> None:
        self._repository = repository
        self._fail_stage = fail_stage

    def delete_page_chunks(self, *, notion_page_db_id: int) -> int:
        deleted_count = self._repository.delete_page_chunks(
            notion_page_db_id=notion_page_db_id
        )
        if self._fail_stage == "after_chunk_delete":
            raise RuntimeError("injected failure after chunk deletion")
        return deleted_count

    def upsert_chunks(
        self,
        *,
        notion_page_db_id: int,
        chunks: list[NotionChunkUpsert],
    ) -> list[KnowledgeChunk]:
        inserted = self._repository.upsert_chunks(
            notion_page_db_id=notion_page_db_id,
            chunks=chunks,
        )
        if self._fail_stage == "during_chunk_insert":
            raise ChunkRepositoryError("injected failure during chunk insert")
        return inserted


class _FailingNotionBlockRepository:
    def __init__(self, repository: NotionBlockRepository, fail_stage: str) -> None:
        self._repository = repository
        self._fail_stage = fail_stage

    def replace_page_blocks(
        self,
        *,
        notion_page_db_id: int,
        root_blocks: list[NotionBlockSnapshot],
    ) -> list[NotionBlock]:
        inserted = self._repository.replace_page_blocks(
            notion_page_db_id=notion_page_db_id,
            root_blocks=root_blocks,
        )
        if self._fail_stage == "after_block_replace":
            raise RuntimeError("injected failure after block replacement")
        return inserted


class _FailingPageIndexUnitOfWork:
    def __init__(self, session_factory: SessionFactory, fail_stage: str) -> None:
        self._unit_of_work = SqlAlchemyUnitOfWork(session_factory)
        self._fail_stage = fail_stage

    def __enter__(self) -> "_FailingPageIndexUnitOfWork":
        active_unit_of_work = self._unit_of_work.__enter__()
        self.notion_pages = active_unit_of_work.notion_pages
        self.notion_blocks = _FailingNotionBlockRepository(
            active_unit_of_work.notion_blocks,
            self._fail_stage,
        )
        self.chunks = _FailingChunkRepository(
            active_unit_of_work.chunks,
            self._fail_stage,
        )
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self._unit_of_work.__exit__(exc_type, exc_value, traceback)


@pytest.mark.parametrize(
    ("fail_stage", "expected_error_code"),
    [
        ("after_chunk_delete", "INDEX_PAGE_PERSIST_FAILED"),
        ("after_block_replace", "INDEX_PAGE_PERSIST_FAILED"),
        ("during_chunk_insert", "VECTOR_UPSERT_FAILED"),
    ],
)
def test_index_page_snapshot_rolls_back_page_mutations_when_persist_stage_fails(
    fail_stage: str,
    expected_error_code: str,
) -> None:
    session_factory = _build_session_factory()
    _seed_existing_rollback_page(session_factory)

    orchestrator = NotionPageIndexOrchestrator(
        tool_registry=_build_tool_registry({"page-rollback": _rollback_tree_v2()}),
        unit_of_work_factory=lambda: _FailingPageIndexUnitOfWork(
            session_factory,
            fail_stage,
        ),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_client=_FakeEmbeddingClient(),
    )

    with pytest.raises(NotionPageIndexError) as exc_info:
        asyncio.run(
            orchestrator.index_page_snapshot(
                page_id="page-rollback",
                request_workflow_id="wf-rollback",
            )
        )

    assert exc_info.value.error_code == expected_error_code
    _assert_existing_rollback_page_is_unchanged(session_factory)


@pytest.mark.parametrize("fail_at_call", [1, 2, 3])
def test_embedding_batch_failure_never_opens_replacement_transaction(
    fail_at_call: int,
) -> None:
    session_factory = _build_session_factory()
    _seed_existing_rollback_page(session_factory)
    client = _SelectedBatchFailingEmbeddingClient(
        fail_at_call=fail_at_call,
    )
    service = EmbeddingBatchService(
        embedding_client=client,
        model="text-embedding-3-small",
        dimensions=1536,
        limits=EmbeddingBatchLimits(max_inputs=1),
        token_counter=lambda value: 1,
    )
    replacement_factory_calls = 0

    def replacement_factory() -> SqlAlchemyUnitOfWork:
        nonlocal replacement_factory_calls
        replacement_factory_calls += 1
        return SqlAlchemyUnitOfWork(session_factory)

    orchestrator = NotionPageIndexOrchestrator(
        tool_registry=_build_tool_registry(
            {"page-rollback": _rollback_tree_with_three_chunks()}
        ),
        unit_of_work_factory=replacement_factory,
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_batch_service=service,
    )

    with pytest.raises(NotionPageIndexError) as exc_info:
        asyncio.run(
            orchestrator.index_page_snapshot(
                page_id="page-rollback",
                request_workflow_id="wf-selected-batch-failure",
            )
        )

    assert exc_info.value.error_code == "EMBEDDING_PROVIDER_ERROR"
    assert client.call_count == fail_at_call
    assert replacement_factory_calls == 0
    _assert_existing_rollback_page_is_unchanged(session_factory)


def test_middle_batch_failure_for_new_page_persists_no_rows() -> None:
    session_factory = _build_session_factory()
    client = _SelectedBatchFailingEmbeddingClient(fail_at_call=2)
    service = EmbeddingBatchService(
        embedding_client=client,
        model="text-embedding-3-small",
        dimensions=1536,
        limits=EmbeddingBatchLimits(max_inputs=1),
        token_counter=lambda value: 1,
    )
    replacement_factory_calls = 0

    def replacement_factory() -> SqlAlchemyUnitOfWork:
        nonlocal replacement_factory_calls
        replacement_factory_calls += 1
        return SqlAlchemyUnitOfWork(session_factory)

    orchestrator = NotionPageIndexOrchestrator(
        tool_registry=_build_tool_registry(
            {"page-new": replace(_rollback_tree_with_three_chunks(), page_id="page-new")}
        ),
        unit_of_work_factory=replacement_factory,
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_batch_service=service,
    )

    with pytest.raises(NotionPageIndexError):
        asyncio.run(
            orchestrator.index_page_snapshot(
                page_id="page-new",
                request_workflow_id="wf-new-page-batch-failure",
            )
        )

    assert client.call_count == 2
    assert replacement_factory_calls == 0
    session: Session = session_factory()
    try:
        assert session.query(NotionPage).count() == 0
        assert session.query(NotionBlock).count() == 0
        assert session.query(KnowledgeChunk).count() == 0
    finally:
        session.close()


def test_successful_multi_batch_page_uses_one_replacement_transaction() -> None:
    session_factory = _build_session_factory()
    client = _FakeEmbeddingClient()
    service = EmbeddingBatchService(
        embedding_client=client,
        model="text-embedding-3-small",
        dimensions=1536,
        limits=EmbeddingBatchLimits(max_inputs=1),
        token_counter=lambda value: 1,
    )
    replacement_factory_calls = 0

    def replacement_factory() -> SqlAlchemyUnitOfWork:
        nonlocal replacement_factory_calls
        replacement_factory_calls += 1
        return SqlAlchemyUnitOfWork(session_factory)

    orchestrator = NotionPageIndexOrchestrator(
        tool_registry=_build_tool_registry(
            {"page-rollback": _rollback_tree_with_three_chunks()}
        ),
        unit_of_work_factory=replacement_factory,
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_batch_service=service,
    )

    snapshot = asyncio.run(
        orchestrator.index_page_snapshot(
            page_id="page-rollback",
            request_workflow_id="wf-multi-batch-success",
        )
    )

    assert replacement_factory_calls == 1
    assert snapshot.indexed_chunk_count == 3
    assert snapshot.embedding_batch_count == 3


def test_index_page_api_persists_page_and_nested_blocks() -> None:
    session_factory = _build_session_factory()
    pages = {"page-1": _sample_tree_v1()}

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry(pages)

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)
        response = client.post("/api/notion/index/page", json={"page_id": "page-1"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["page_id"] == "page-1"
        assert payload["indexed_block_count"] == 2

        session: Session = session_factory()
        try:
            page = (
                session.query(NotionPage)
                .filter(NotionPage.notion_page_id == "page-1")
                .one_or_none()
            )
            assert page is not None
            assert page.title == "NLP Week 5"

            blocks = (
                session.query(NotionBlock)
                .filter(NotionBlock.notion_page_id == page.id)
                .all()
            )
            assert len(blocks) == 2

            parent_block = next(
                block for block in blocks if block.notion_block_id == "blk-attention"
            )
            child_block = next(
                block for block in blocks if block.notion_block_id == "blk-sdp"
            )
            assert parent_block.parent_block_id is None
            assert child_block.parent_block_id == parent_block.id

            chunks = (
                session.query(KnowledgeChunk)
                .join(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
                .filter(NotionBlock.notion_page_id == page.id)
                .order_by(KnowledgeChunk.chunk_index.asc())
                .all()
            )
            assert len(chunks) == 1
            assert chunks[0].embedding == [1.0] * 1536
            assert chunks[0].embedding_text is not None

            workflow_run = session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None
            workflow_metadata = json.loads(workflow_run.metadata_json or "{}")
            assert workflow_metadata["embedding_provider"] == "openai"
            assert workflow_metadata["embedding_model"] == "text-embedding-3-small"
            assert workflow_metadata["embedding_dimensions"] == 1536
            assert workflow_metadata["embedding_token_input"] == 10
            assert workflow_metadata["embedding_estimated_cost"] == pytest.approx(0.0000002)
            assert workflow_metadata["embedding_batch_count"] == 1
            assert workflow_metadata["embedding_retry_count"] == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_index_page_api_builds_paths_from_block_hierarchy() -> None:
    session_factory = _build_session_factory()
    pages = {"page-mixed": _sample_tree_mixed_types_with_untrusted_paths()}

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry(pages)

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)
        response = client.post("/api/notion/index/page", json={"page_id": "page-mixed"})

        assert response.status_code == 200

        session: Session = session_factory()
        try:
            page = (
                session.query(NotionPage)
                .filter(NotionPage.notion_page_id == "page-mixed")
                .one_or_none()
            )
            assert page is not None

            blocks = {
                block.notion_block_id: block
                for block in session.query(NotionBlock)
                .filter(NotionBlock.notion_page_id == page.id)
                .all()
            }
            assert blocks["blk-h1"].block_path == "Knowledge/Mixed/Root Heading"
            assert (
                blocks["blk-toggle"].block_path
                == "Knowledge/Mixed/Root Heading/Toggle Topic"
            )
            assert (
                blocks["blk-child-page"].block_path
                == "Knowledge/Mixed/Root Heading/Toggle Topic/Child Page A"
            )
            assert (
                blocks["blk-leaf"].block_path
                == "Knowledge/Mixed/Root Heading/Toggle Topic/Child Page A/Leaf Note"
            )
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_index_page_snapshot_persists_reader_parent_identity() -> None:
    session_factory = _build_session_factory()
    child_tree = replace(
        _sample_tree_v1(),
        page_id="page-child",
        title="Child Page",
        notion_path="Knowledge/Parent/Child",
        parent_notion_page_id="page-parent",
    )
    orchestrator = NotionPageIndexOrchestrator(
        tool_registry=_build_tool_registry({"page-child": child_tree}),
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_client=_FakeEmbeddingClient(),
    )

    result = asyncio.run(
        orchestrator.index_page_snapshot(
            page_id="page-child",
            request_workflow_id="wf-parent-identity",
        )
    )

    assert result.page_title == "Child Page"
    session = session_factory()
    try:
        page = (
            session.query(NotionPage)
            .filter(NotionPage.notion_page_id == "page-child")
            .one()
        )
        assert page.parent_notion_page_id == "page-parent"
    finally:
        session.close()


def test_index_page_api_replaces_existing_page_blocks() -> None:
    session_factory = _build_session_factory()
    pages = {"page-1": _sample_tree_v1()}

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry(pages)

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)
        first_response = client.post("/api/notion/index/page", json={"page_id": "page-1"})
        assert first_response.status_code == 200

        pages["page-1"] = _sample_tree_v2()
        second_response = client.post("/api/notion/index/page", json={"page_id": "page-1"})
        assert second_response.status_code == 200
        second_payload = second_response.json()
        assert second_payload["page_title"] == "NLP Week 5 (Updated)"
        assert second_payload["indexed_block_count"] == 1

        session: Session = session_factory()
        try:
            pages_in_db = session.query(NotionPage).all()
            assert len(pages_in_db) == 1
            assert pages_in_db[0].title == "NLP Week 5 (Updated)"

            blocks = (
                session.query(NotionBlock)
                .filter(NotionBlock.notion_page_id == pages_in_db[0].id)
                .all()
            )
            assert len(blocks) == 1
            assert blocks[0].notion_block_id == "blk-summary"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_index_page_api_returns_not_found_when_page_missing() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry({})

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)
        response = client.post("/api/notion/index/page", json={"page_id": "missing-page"})

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert detail["error_code"] == "NOTION_PAGE_NOT_FOUND"
        assert detail["failure_reason"] == "NOTION_PAGE_NOT_FOUND"

        session: Session = session_factory()
        try:
            workflow_runs = session.query(WorkflowRun).all()
            assert len(workflow_runs) == 1
            assert workflow_runs[0].status == "failed"
            assert workflow_runs[0].failure_reason == "NOTION_PAGE_NOT_FOUND"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_index_page_api_fails_closed_when_embedding_provider_missing() -> None:
    session_factory = _build_session_factory()
    pages = {"page-1": _sample_tree_v1()}

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry(pages)

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = lambda: None

    try:
        client = TestClient(app)
        response = client.post("/api/notion/index/page", json={"page_id": "page-1"})

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["error_code"] == "EMBEDDING_PROVIDER_NOT_CONFIGURED"
        assert detail["failure_reason"] == "EMBEDDING_PROVIDER_NOT_CONFIGURED"

        session: Session = session_factory()
        try:
            assert session.query(NotionPage).count() == 0
            assert session.query(NotionBlock).count() == 0
            assert session.query(KnowledgeChunk).count() == 0

            workflow_run = session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "EMBEDDING_PROVIDER_NOT_CONFIGURED"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_index_incremental_api_reconciles_manual_deletion_with_page_replacement() -> None:
    session_factory = _build_session_factory()
    pages = {"page-sync": _sample_incremental_v1()}

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry(pages)

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)
        first_response = client.post("/api/notion/index/page", json={"page_id": "page-sync"})
        assert first_response.status_code == 200

        session: Session = session_factory()
        try:
            page = (
                session.query(NotionPage)
                .filter(NotionPage.notion_page_id == "page-sync")
                .one_or_none()
            )
            assert page is not None
            original_chunk_count = (
                session.query(KnowledgeChunk)
                .join(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
                .filter(NotionBlock.notion_page_id == page.id)
                .count()
            )
            assert original_chunk_count == 2
        finally:
            session.close()

        pages["page-sync"] = _sample_incremental_v2_after_manual_delete()
        incremental_response = client.post(
            "/api/notion/index/incremental",
            json={"page_ids": ["page-sync"]},
        )
        assert incremental_response.status_code == 200
        payload = incremental_response.json()
        assert payload["status"] == "succeeded"
        assert payload["sync_mode"] == "manual"
        assert payload["processed_page_count"] == 1
        assert payload["indexed_pages"][0]["page_id"] == "page-sync"
        assert payload["indexed_pages"][0]["indexed_block_count"] == 2

        session = session_factory()
        try:
            page = (
                session.query(NotionPage)
                .filter(NotionPage.notion_page_id == "page-sync")
                .one_or_none()
            )
            assert page is not None
            blocks = (
                session.query(NotionBlock)
                .filter(NotionBlock.notion_page_id == page.id)
                .all()
            )
            assert len(blocks) == 2
            assert {block.notion_block_id for block in blocks} == {"blk-old-b", "blk-old-b-p"}

            chunks = (
                session.query(KnowledgeChunk)
                .join(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
                .filter(NotionBlock.notion_page_id == page.id)
                .all()
            )
            assert len(chunks) == 1
            assert chunks[0].chunk_text == "Old Section B\nOld note B (edited)"
            assert chunks[0].embedding == [1.0] * 1536
            assert chunks[0].embedding_text is not None

            workflow_run = session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["embedding_provider"] == "openai"
            assert metadata["embedding_model"] == "text-embedding-3-small"
            assert metadata["embedding_dimensions"] == 1536
            assert metadata["embedding_token_input"] == 10
            assert metadata["embedding_estimated_cost"] == pytest.approx(0.0000002)
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_index_incremental_api_returns_not_found_when_any_page_missing() -> None:
    session_factory = _build_session_factory()
    pages = {"page-sync": _sample_incremental_v1()}

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry(pages)

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/notion/index/incremental",
            json={"page_ids": ["page-sync", "missing-page", "later-page"]},
        )
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert detail["error_code"] == "NOTION_PAGE_NOT_FOUND"
        assert detail["failure_reason"] == "NOTION_PAGE_NOT_FOUND"
        assert detail["workflow_run_id"] is not None

        session: Session = session_factory()
        try:
            workflow_runs = session.query(WorkflowRun).order_by(WorkflowRun.id.asc()).all()
            assert len(workflow_runs) == 1
            assert workflow_runs[0].status == "failed"
            assert workflow_runs[0].failure_reason == "NOTION_PAGE_NOT_FOUND"
            metadata = json.loads(workflow_runs[0].metadata_json or "{}")
            assert metadata["processed_page_count"] == 1
            assert metadata["succeeded_page_count"] == 1
            assert metadata["succeeded_page_ids"] == ["page-sync"]
            assert metadata["failed_page_id"] == "missing-page"
            assert metadata["failed_page_index"] == 1
            assert metadata["remaining_page_count"] == 1
            assert metadata["remaining_page_ids"] == ["later-page"]

            persisted_page = (
                session.query(NotionPage)
                .filter(NotionPage.notion_page_id == "page-sync")
                .one_or_none()
            )
            assert persisted_page is not None
            assert (
                session.query(NotionBlock)
                .filter(NotionBlock.notion_page_id == persisted_page.id)
                .count()
                == 4
            )
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_index_incremental_api_backfills_only_requested_legacy_page_vectors() -> None:
    session_factory = _build_session_factory()
    pages = {"page-legacy-a": _sample_legacy_backfill_page()}

    seed_session = session_factory()
    try:
        legacy_page = NotionPage(
            id=101,
            notion_page_id="page-legacy-a",
            title="Legacy Vector Page",
            notion_path="Knowledge/Legacy/A",
        )
        untouched_page = NotionPage(
            id=102,
            notion_page_id="page-legacy-b",
            title="Untouched Vector Page",
            notion_path="Knowledge/Legacy/B",
        )
        seed_session.add_all([legacy_page, untouched_page])
        seed_session.flush()

        legacy_block = NotionBlock(
            id=201,
            notion_block_id="blk-legacy-a-old",
            notion_page_id=legacy_page.id,
            parent_block_id=None,
            block_type="paragraph",
            content_text="Old legacy chunk without vectors",
            block_path="Knowledge/Legacy/A/Old Legacy Chunk",
            block_order=0,
        )
        untouched_block = NotionBlock(
            id=202,
            notion_block_id="blk-legacy-b-stable",
            notion_page_id=untouched_page.id,
            parent_block_id=None,
            block_type="paragraph",
            content_text="Existing live vector chunk",
            block_path="Knowledge/Legacy/B/Stable Vector Chunk",
            block_order=0,
        )
        seed_session.add_all([legacy_block, untouched_block])
        seed_session.flush()

        seed_session.add_all(
            [
                KnowledgeChunk(
                    id=301,
                    source_document_id=None,
                    notion_block_id=legacy_block.id,
                    chunk_index=0,
                    chunk_text="Old legacy chunk without vectors",
                    notion_path="Knowledge/Legacy/A/Old Legacy Chunk",
                    embedding=None,
                    embedding_text=None,
                    source_kind="notion",
                ),
                KnowledgeChunk(
                    id=302,
                    source_document_id=None,
                    notion_block_id=untouched_block.id,
                    chunk_index=0,
                    chunk_text="Existing live vector chunk",
                    notion_path="Knowledge/Legacy/B/Stable Vector Chunk",
                    embedding=_embedding_vector(9.0),
                    embedding_text=json.dumps(_embedding_vector(9.0)),
                    source_kind="notion",
                ),
            ]
        )
        seed_session.commit()
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry(pages)

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/notion/index/incremental",
            json={"page_ids": ["page-legacy-a"]},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["processed_page_count"] == 1
        assert payload["indexed_pages"][0]["page_id"] == "page-legacy-a"

        verify_session = session_factory()
        try:
            legacy_chunks = (
                verify_session.query(KnowledgeChunk)
                .join(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
                .join(NotionPage, NotionBlock.notion_page_id == NotionPage.id)
                .filter(NotionPage.notion_page_id == "page-legacy-a")
                .order_by(KnowledgeChunk.chunk_index.asc())
                .all()
            )
            assert len(legacy_chunks) == 1
            assert legacy_chunks[0].chunk_text == (
                "Recovered Section\nLegacy page re-index restores vector state"
            )
            assert legacy_chunks[0].embedding == _embedding_vector(1.0)
            assert legacy_chunks[0].embedding_text is not None

            untouched_chunks = (
                verify_session.query(KnowledgeChunk)
                .join(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
                .join(NotionPage, NotionBlock.notion_page_id == NotionPage.id)
                .filter(NotionPage.notion_page_id == "page-legacy-b")
                .order_by(KnowledgeChunk.id.asc())
                .all()
            )
            assert len(untouched_chunks) == 1
            assert untouched_chunks[0].chunk_text == "Existing live vector chunk"
            assert untouched_chunks[0].embedding == _embedding_vector(9.0)
            assert untouched_chunks[0].embedding_text == json.dumps(_embedding_vector(9.0))
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()
