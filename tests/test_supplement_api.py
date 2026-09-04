from __future__ import annotations

import json
from typing import Dict

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import (
    get_embedding_client,
    get_provider_router,
    get_tool_registry,
)
from src.app.main import app
from src.db.base import Base
from src.db.models import (
    ChangeRequest,
    KnowledgeChunk,
    NotionBlock,
    NotionPage,
    SourceDocument,
    WorkflowRun,
)
from src.db.session import get_db_session, get_db_session_factory, get_unit_of_work_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers import (
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
    get_openai_embedding_capabilities,
)
from src.services import InfrastructureExecutionTimeout
from src.tools import (
    InMemoryNotionPageSnapshot,
    InMemoryNotionWriterClient,
    NotionBlockNode,
    NotionPageTree,
    NotionReaderClient,
    NotionReaderTool,
    NotionWriterTool,
    ToolRegistry,
)


class _FakeProposalProvider(LLMProvider):
    def __init__(self, *, output_text: str) -> None:
        self._output_text = output_text

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=self._output_text,
            token_input=120,
            token_output=90,
        )


class _ScreenshotTitleRepairProvider(LLMProvider):
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []
        self.outputs = [
            json.dumps(
                {
                    "title": "MySQL EXPLAIN 與 Redis 索引",
                    "target_path": "NONE (no selected target page)",
                    "source": {
                        "source_type": "screenshot",
                        "source_display_name": "Screenshot batch (5 images)",
                    },
                    "summary": "這組截圖整理 MySQL EXPLAIN、SQL 與索引查詢資訊。",
                    "concepts": ["MySQL", "EXPLAIN", "SQL", "索引", "查詢執行計畫"],
                    "notes": [
                        "MySQL EXPLAIN 會顯示查詢的執行計畫。",
                        "SQL 查詢可搭配 EXPLAIN 觀察索引。",
                        "畫面列出 type、key、rows 欄位。",
                        "索引可協助查詢條件過濾。",
                    ],
                }
            ),
            json.dumps({"title": "MySQL EXPLAIN 與 SQL 索引查詢"}),
        ]

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=self.outputs.pop(0),
            token_input=20,
            token_output=10,
        )


class _ScreenshotSummaryRepairProvider(LLMProvider):
    def __init__(self, *, repaired_summary: str) -> None:
        self.requests: list[LLMRequest] = []
        self.outputs = [
            json.dumps(
                {
                    "title": "MySQL EXPLAIN 與 SQL 索引查詢",
                    "target_path": "NONE (no selected target page)",
                    "source": {
                        "source_type": "screenshot",
                        "source_display_name": "Screenshot batch (5 images)",
                    },
                    "summary": "這些截圖整理學習內容。",
                    "concepts": ["MySQL", "EXPLAIN", "SQL", "索引", "查詢執行計畫"],
                    "notes": [
                        "MySQL EXPLAIN 會顯示查詢的執行計畫。",
                        "SQL 查詢可搭配 EXPLAIN 觀察索引。",
                        "畫面列出 type、key、rows 欄位。",
                        "索引可協助查詢條件過濾。",
                    ],
                }
            ),
            json.dumps({"summary": repaired_summary}),
        ]

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=self.outputs.pop(0),
            token_input=25,
            token_output=20,
        )


class _ScreenshotBodyRepairProvider(LLMProvider):
    def __init__(
        self,
        *,
        initial_title: str = "MySQL EXPLAIN 與 SQL 索引查詢",
        include_title_repair: bool = False,
        repaired_body_is_valid: bool = True,
    ) -> None:
        self.requests: list[LLMRequest] = []
        initial_proposal = {
            "title": initial_title,
            "target_path": "NONE (no selected target page)",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "Screenshot batch (5 images)",
            },
            "summary": (
                "這組截圖統整 MySQL EXPLAIN 與 SQL 的 optimizer workflow。"
            ),
            "concepts": [
                "MySQL optimizer",
                "EXPLAIN planner",
                "SQL runtime",
                "索引 pipeline",
                "MySQL",
            ],
            "notes": [
                "EXPLAIN 顯示 query planner。",
                "SQL 查詢遵循 optimizer workflow。",
                "索引可協助 runtime 過濾。",
            ],
        }
        repaired_body = {
            "summary": (
                "這組截圖整理 MySQL EXPLAIN 與 SQL 查詢的執行計畫資訊，"
                "並列出 type、key、rows 與索引資訊。"
            ),
            "concepts": ["MySQL", "EXPLAIN", "SQL", "索引", "查詢執行計畫"],
            "notes": [
                "MySQL EXPLAIN 會顯示查詢的執行計畫。",
                "SQL 查詢可搭配 EXPLAIN 觀察索引。",
                "畫面列出 type、key、rows 欄位。",
                "索引可協助查詢條件過濾。",
            ],
        }
        if not repaired_body_is_valid:
            repaired_body["summary"] = "這組截圖加入 Redis。"
        self.outputs = [json.dumps(initial_proposal)]
        if include_title_repair:
            self.outputs.append(
                json.dumps({"title": "MySQL EXPLAIN 與 SQL 索引查詢"})
            )
        self.outputs.append(json.dumps(repaired_body))

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=self.outputs.pop(0),
            token_input=30,
            token_output=25,
        )


class _SnapshotBackedNotionReaderClient(NotionReaderClient):
    def __init__(self, pages: Dict[str, InMemoryNotionPageSnapshot]) -> None:
        self._pages = pages

    def fetch_page_tree(self, page_id: str) -> NotionPageTree | None:
        snapshot = self._pages.get(page_id)
        if snapshot is None:
            return None

        blocks = [
            NotionBlockNode(
                block_id=f"{snapshot.page_id}-orig-{index}",
                block_type="paragraph",
                content_text=text,
                block_path=f"{snapshot.notion_path}/Original/{index}",
            )
            for index, text in enumerate(snapshot.original_blocks, start=1)
        ]
        for entry in snapshot.ai_supplement_entries:
            blocks.append(
                NotionBlockNode(
                    block_id=f"{snapshot.page_id}-ai-{entry.change_request_id}-title",
                    block_type="heading_3",
                    content_text=entry.topic_title,
                    block_path=entry.target_path,
                )
            )
            blocks.extend(
                NotionBlockNode(
                    block_id=(
                        f"{snapshot.page_id}-ai-{entry.change_request_id}"
                        f"-line-{line_index}"
                    ),
                    block_type="paragraph",
                    content_text=line,
                    block_path=f"{entry.target_path}/line-{line_index}",
                )
                for line_index, line in enumerate(entry.section_lines, start=1)
            )

        return NotionPageTree(
            page_id=snapshot.page_id,
            title=snapshot.title,
            notion_path=snapshot.notion_path,
            blocks=blocks,
        )


class _TimeoutNotionReaderClient(_SnapshotBackedNotionReaderClient):
    def fetch_page_tree(self, page_id: str) -> NotionPageTree | None:
        _ = page_id
        raise InfrastructureExecutionTimeout()


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


class _FailOnceEmbeddingClient(_FakeEmbeddingClient):
    def __init__(self) -> None:
        self._failed = False

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if not self._failed:
            self._failed = True
            raise EmbeddingClientError("injected re-index failure")
        return await super().embed(request)


def _build_review_tool_registry(
    snapshot_pages: Dict[str, InMemoryNotionPageSnapshot],
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(
        NotionReaderTool(_SnapshotBackedNotionReaderClient(snapshot_pages))
    )
    registry.register_tool(NotionWriterTool(InMemoryNotionWriterClient(snapshot_pages)))
    return registry


def _embedding_client_override() -> EmbeddingClient:
    return _FakeEmbeddingClient()


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            SourceDocument.__table__,
            WorkflowRun.__table__,
            ChangeRequest.__table__,
            NotionPage.__table__,
            NotionBlock.__table__,
            KnowledgeChunk.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed_source_document(
    session: Session,
    *,
    source_document_id: int,
    source_type: str,
    source_display_name: str,
    raw_text: str,
) -> None:
    session.add(
        SourceDocument(
            id=source_document_id,
            source_type=source_type,
            source_display_name=source_display_name,
            content_hash=f"hash-{source_document_id}",
            raw_text=raw_text,
        )
    )
    session.commit()


def _seed_duplicate_reference_chunk(session: Session, *, chunk_text: str, notion_path: str) -> None:
    page = NotionPage(
        id=1,
        notion_page_id="page-nlp-week5",
        title="NLP Week 5",
        notion_path="Knowledge/NLP/Week5",
    )
    session.add(page)
    session.flush()

    block = NotionBlock(
        id=1,
        notion_block_id="blk-attn",
        notion_page_id=page.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text=chunk_text,
        block_path=notion_path,
        block_order=0,
    )
    session.add(block)
    session.flush()

    session.add(
        KnowledgeChunk(
            id=1,
            source_document_id=None,
            notion_block_id=block.id,
            chunk_index=0,
            chunk_text=chunk_text,
            notion_path=notion_path,
            embedding_text=None,
            source_kind="notion",
        )
    )
    session.commit()


def _seed_notion_page(
    session: Session,
    *,
    page_db_id: int,
    notion_page_id: str,
    title: str,
    notion_path: str,
) -> None:
    session.add(
        NotionPage(
            id=page_db_id,
            notion_page_id=notion_page_id,
            title=title,
            notion_path=notion_path,
        )
    )
    session.commit()


def _seed_change_request(
    session: Session,
    *,
    change_request_id: int,
    status: str = "pending",
    target_notion_page_id: int | None = None,
    proposal_json: str = '{"title":"Draft proposal"}',
) -> None:
    session.add(
        ChangeRequest(
            id=change_request_id,
            source_document_id=None,
            target_notion_page_id=target_notion_page_id,
            status=status,
            proposal_json=proposal_json,
            failure_reason=None,
        )
    )
    session.commit()


def test_supplement_propose_api_creates_pending_change_request() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=1,
            source_type="chat_text",
            source_display_name="chat-2026-05-26",
            raw_text="Notes about positional encoding and sequence modeling trade-offs.",
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(
            _FakeProposalProvider(
                output_text=json.dumps(
                    {
                        "title": "Positional Encoding Supplement",
                        "target_path": "Knowledge/NLP/Week5/AI Supplement Zone/Positional Encoding",
                        "source": {
                            "source_type": "chat_text",
                            "source_display_name": "chat-2026-05-26",
                        },
                        "summary": "Adds concise notes about positional encoding trade-offs.",
                        "concepts": ["positional encoding", "sequence length generalization"],
                        "notes": ["Compare sinusoidal and learned embeddings briefly."],
                    }
                )
            )
        )
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/supplement/propose",
            json={
                "source_document_id": 1,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["change_request_status"] == "pending"
        assert payload["source_document_id"] == 1
        assert payload["duplicate_detected"] is False
        assert payload["duplicate_notion_path"] is None
        assert payload["provider"] == "openai"
        assert payload["model"] == "gpt-4o-mini"
        assert payload["token_input"] == 120
        assert payload["token_output"] == 90

        verify_session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, payload["change_request_id"])
            assert change_request is not None
            assert change_request.status == "pending"
            assert change_request.source_document_id == 1
            proposal_payload = json.loads(change_request.proposal_json)
            assert proposal_payload["title"] == "Positional Encoding Supplement"
            assert proposal_payload["target_path"] == "NONE (no selected target page)"

            workflow_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "supplement"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["provider_name"] == "openai"
            assert metadata["model"] == "gpt-4o-mini"
            assert metadata["prompt_id"] == "supplement_proposal"
            assert metadata["prompt_version"] == "supplement_proposal_v7"
            assert metadata["estimated_cost"] == pytest.approx(0.000072)

            # Step 28 must not write to Notion.
            assert verify_session.query(NotionPage).count() == 0
            assert verify_session.query(NotionBlock).count() == 0
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_screenshot_title_repair_only_retries_title_with_same_source_snapshot() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=25,
            source_type="screenshot",
            source_display_name="Screenshot batch (5 images)",
            raw_text=(
                "[Image 10]\nMySQL EXPLAIN 會顯示查 詢 的執 行 計 畫。\n"
                "[Image 20]\nSQL 查 詢 可搭配 EXPLAIN 觀察索 引。\n"
                "[Image 30]\n索 引 可協助查 詢 條件 過 濾。\n"
                "[Image 40]\n畫面列出 type、key、rows 欄位，並顯示索 引 是否被使用。\n"
                "[Image 50]\n來源：MySQL EXPLAIN 與 SQL 索 引 查 詢 資 訊。"
            ),
        )
    finally:
        seed_session.close()

    provider = _ScreenshotTitleRepairProvider()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(provider)
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/supplement/propose",
            json={
                "source_document_id": 25,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert len(provider.requests) == 2
        repair_request = provider.requests[1]
        assert repair_request.metadata["operation"] == "repair_screenshot_title"
        assert repair_request.max_tokens == 120
        assert "MySQL EXPLAIN" in repair_request.messages[1].content
        assert "SQL" in repair_request.messages[1].content
        assert "索引" in repair_request.messages[1].content
        assert "Redis" in repair_request.messages[1].content

        verify_session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, payload["change_request_id"])
            assert change_request is not None
            proposal = json.loads(change_request.proposal_json)
            assert proposal["title"] == "MySQL EXPLAIN 與 SQL 索引查詢"
            assert verify_session.query(SourceDocument).count() == 1

            workflow = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["source_document_id"] == 25
            assert metadata["title_repair_attempted"] is True
            assert metadata["title_repair_succeeded"] is True
            assert metadata["title_anchor_count"] >= 5
            assert metadata["matched_title_anchor_count"] == metadata["title_anchor_count"]
            assert metadata["unmatched_title_anchor_count"] == 0
            assert metadata["evidence_claim_count"] >= 9
            assert metadata["unsupported_claim_count"] == 0
            assert metadata["prompt_source_digest"] == metadata["validation_source_digest"]
            assert metadata["source_snapshot_digest"] == metadata["prompt_source_digest"]
            assert "MySQL EXPLAIN 與 SQL 索引查詢" not in workflow.metadata_json
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_screenshot_title_repair_is_bounded_to_one_attempt() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=25,
            source_type="screenshot",
            source_display_name="Screenshot batch (5 images)",
            raw_text="MySQL EXPLAIN、SQL 與索引查詢。",
        )
    finally:
        seed_session.close()

    provider = _ScreenshotTitleRepairProvider()
    provider.outputs[1] = json.dumps({"title": "Redis"})

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(provider)
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        response = TestClient(app).post(
            "/api/supplement/propose",
            json={"source_document_id": 25},
        )
        assert response.status_code == 502
        assert len(provider.requests) == 2

        session = session_factory()
        try:
            assert session.query(ChangeRequest).count() == 0
            workflow = session.get(WorkflowRun, response.json()["detail"]["workflow_run_id"])
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["title_repair_attempted"] is True
            assert metadata["title_repair_succeeded"] is False
            assert metadata["unmatched_title_anchor_count"] >= 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_screenshot_title_repair_uses_deterministic_fallback_for_general_cjk_only() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=25,
            source_type="screenshot",
            source_display_name="Screenshot batch (5 images)",
            raw_text=(
                "[Image 10]\nMySQL EXPLAIN 會顯示查 詢 的執 行 計 畫。\n"
                "[Image 20]\nSQL 查 詢 可搭配 EXPLAIN 觀察索 引。\n"
                "[Image 30]\n索 引 可協助查 詢 條件 過 濾。\n"
                "[Image 40]\n畫面列出 type、key、rows 欄位，並顯示索 引 是否被使用。\n"
                "[Image 50]\n來源：MySQL EXPLAIN 與 SQL 索 引 查 詢 資 訊。"
            ),
        )
    finally:
        seed_session.close()

    provider = _ScreenshotTitleRepairProvider()
    provider.outputs[1] = json.dumps(
        {"title": "索引 / 資料庫 / 效能分析"}
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(provider)
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        response = TestClient(app).post(
            "/api/supplement/propose",
            json={"source_document_id": 25},
        )
        assert response.status_code == 200
        assert len(provider.requests) == 2

        session = session_factory()
        try:
            change_request = session.get(ChangeRequest, response.json()["change_request_id"])
            assert change_request is not None
            proposal = json.loads(change_request.proposal_json)
            assert proposal["title"] != "索引 / 資料庫 / 效能分析"
            assert "MySQL" in proposal["title"]
            assert proposal["summary"] == (
                "這組截圖整理 MySQL EXPLAIN、SQL 與索引查詢資訊。"
            )

            workflow = session.get(WorkflowRun, response.json()["workflow_run_id"])
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["title_repair_attempted"] is True
            assert metadata["title_repair_succeeded"] is False
            assert metadata["title_fallback_used"] is True
            assert metadata["title_fallback_attempted"] is True
            assert metadata["title_fallback_succeeded"] is True
            assert metadata["title_repair_failure_reason"] == (
                "INSUFFICIENT_MATCHED_ANCHORS"
            )
            assert metadata["unmatched_high_specificity_anchor_count"] == 0
            assert metadata["unmatched_technical_identifier_count"] == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_screenshot_summary_repair_only_retries_summary_with_same_source_snapshot() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=26,
            source_type="screenshot",
            source_display_name="Screenshot batch (5 images)",
            raw_text=(
                "[Image 10]\nMySQL EXPLAIN 會顯示查 詢 的執 行 計 畫。\n"
                "[Image 20]\nSQL 查 詢 可搭配 EXPLAIN 觀察索 引。\n"
                "[Image 30]\n索 引 可協助查 詢 條件 過 濾。\n"
                "[Image 40]\n畫面列出 type、key、rows 欄位，並顯示索 引 是否被使用。\n"
                "[Image 50]\n來源：MySQL EXPLAIN 與 SQL 索 引 查 詢 資 訊。"
            ),
        )
    finally:
        seed_session.close()

    repaired_summary = (
        "這組五張截圖整理 MySQL EXPLAIN 與 SQL 查詢的執行計畫資訊，"
        "包含 type、key、rows 欄位，也說明索引是否被使用。"
    )
    provider = _ScreenshotSummaryRepairProvider(repaired_summary=repaired_summary)

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(provider)
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        response = TestClient(app).post(
            "/api/supplement/propose",
            json={"source_document_id": 26},
        )
        assert response.status_code == 200
        assert len(provider.requests) == 2
        repair_request = provider.requests[1]
        assert repair_request.metadata["operation"] == "repair_screenshot_summary"
        assert repair_request.max_tokens == 260
        assert "MySQL EXPLAIN" in repair_request.messages[1].content
        assert "Redis" not in repair_request.messages[1].content
        source_anchor = "MySQL EXPLAIN 會顯示查詢的執行計畫。"
        assert source_anchor in provider.requests[0].messages[1].content
        assert source_anchor in repair_request.messages[1].content

        session = session_factory()
        try:
            change_request = session.get(ChangeRequest, response.json()["change_request_id"])
            assert change_request is not None
            proposal = json.loads(change_request.proposal_json)
            assert proposal["title"] == "MySQL EXPLAIN 與 SQL 索引查詢"
            assert proposal["summary"] == repaired_summary
            assert proposal["concepts"] == ["MySQL", "EXPLAIN", "SQL", "索引", "查詢執行計畫"]
            assert session.query(SourceDocument).count() == 1

            workflow = session.get(WorkflowRun, response.json()["workflow_run_id"])
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["summary_repair_attempted"] is True
            assert metadata["summary_repair_succeeded"] is True
            assert metadata["extracted_claim_count"] >= metadata["matched_claim_count"] >= 1
            assert metadata["unsupported_claim_count"] == 0
            assert metadata["source_snapshot_digest"] == metadata["prompt_source_digest"]
            assert metadata["prompt_source_digest"] == metadata["validation_source_digest"]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_screenshot_summary_repair_is_bounded_to_one_attempt() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=26,
            source_type="screenshot",
            source_display_name="Screenshot batch (5 images)",
            raw_text=(
                "[Image 10]\nMySQL EXPLAIN 會顯示查 詢 的執 行 計 畫。\n"
                "[Image 20]\nSQL 查 詢 可搭配 EXPLAIN 觀察索 引。\n"
                "[Image 30]\n索 引 可協助查 詢 條件 過 濾。\n"
                "[Image 40]\n畫面列出 type、key、rows 欄位，並顯示索 引 是否被使用。\n"
                "[Image 50]\n來源：MySQL EXPLAIN 與 SQL 索 引 查 詢 資 訊。"
            ),
        )
    finally:
        seed_session.close()

    provider = _ScreenshotSummaryRepairProvider(repaired_summary="這組截圖加入 Redis。")

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(provider)
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        response = TestClient(app).post(
            "/api/supplement/propose",
            json={"source_document_id": 26},
        )
        assert response.status_code == 502
        assert len(provider.requests) == 2

        session = session_factory()
        try:
            assert session.query(ChangeRequest).count() == 0
            workflow = session.get(WorkflowRun, response.json()["detail"]["workflow_run_id"])
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["validation_field"] == "summary"
            assert metadata["summary_repair_attempted"] is True
            assert metadata["summary_repair_succeeded"] is False
            assert metadata["first_unsupported_reason"] == "NEW_TECHNICAL_IDENTIFIER"
            assert session.query(SourceDocument).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_screenshot_title_repair_can_continue_into_one_bounded_body_repair() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=27,
            source_type="screenshot",
            source_display_name="Screenshot batch (5 images)",
            raw_text=(
                "[Image 10]\nMySQL EXPLAIN 會顯示查詢的執行計畫。\n"
                "[Image 20]\nSQL 查詢可搭配 EXPLAIN 觀察索引。\n"
                "[Image 30]\n索引可協助查詢條件過濾。\n"
                "[Image 40]\n畫面列出 type、key、rows 欄位。"
            ),
        )
    finally:
        seed_session.close()

    provider = _ScreenshotBodyRepairProvider(
        initial_title="MySQL EXPLAIN 與 Redis 索引",
        include_title_repair=True,
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(provider)
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        response = TestClient(app).post(
            "/api/supplement/propose",
            json={"source_document_id": 27},
        )
        assert response.status_code == 200
        assert [request.metadata["operation"] for request in provider.requests] == [
            "propose_change_request",
            "repair_screenshot_title",
            "repair_screenshot_body",
        ]
        assert provider.requests[2].max_tokens == 1200
        assert "MySQL EXPLAIN 會顯示查詢的執行計畫。" in (
            provider.requests[2].messages[1].content
        )

        session = session_factory()
        try:
            workflow = session.get(WorkflowRun, response.json()["workflow_run_id"])
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["title_repair_attempted"] is True
            assert metadata["title_repair_succeeded"] is True
            assert metadata["title_repair_failure_reason"] is None
            assert metadata["title_fallback_attempted"] is False
            assert metadata["title_fallback_succeeded"] is False
            assert metadata["body_repair_attempted"] is True
            assert metadata["body_repair_succeeded"] is True
            assert metadata["summary_repair_attempted"] is False
            assert session.query(ChangeRequest).count() == 1
            assert session.query(SourceDocument).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_screenshot_body_repair_is_bounded_to_one_attempt() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=28,
            source_type="screenshot",
            source_display_name="Screenshot batch (5 images)",
            raw_text=(
                "MySQL EXPLAIN 會顯示查詢的執行計畫。"
                "SQL 查詢可搭配 EXPLAIN 觀察索引。"
                "索引可協助查詢條件過濾。"
                "畫面列出 type、key、rows 欄位。"
            ),
        )
    finally:
        seed_session.close()

    provider = _ScreenshotBodyRepairProvider(repaired_body_is_valid=False)

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(provider)
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        response = TestClient(app).post(
            "/api/supplement/propose",
            json={"source_document_id": 28},
        )
        assert response.status_code == 502
        assert [request.metadata["operation"] for request in provider.requests] == [
            "propose_change_request",
            "repair_screenshot_body",
        ]

        session = session_factory()
        try:
            workflow_id = response.json()["detail"]["workflow_run_id"]
            workflow = session.get(WorkflowRun, workflow_id)
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["body_repair_attempted"] is True
            assert metadata["body_repair_succeeded"] is False
            assert metadata["first_unsupported_reason"] == (
                "NEW_TECHNICAL_IDENTIFIER"
            )
            serialized_metadata = json.dumps(metadata, ensure_ascii=False)
            assert "optimizer workflow" not in serialized_metadata
            assert "這組截圖加入 Redis" not in serialized_metadata
            assert session.query(ChangeRequest).count() == 0
            assert session.query(SourceDocument).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_propose_api_uses_duplicate_reference_without_provider() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    duplicate_text = "Transformer attention uses query key value vectors for context weighting."
    duplicate_path = "Knowledge/NLP/Week5/Attention"
    try:
        _seed_source_document(
            seed_session,
            source_document_id=2,
            source_type="pdf",
            source_display_name="week5-attention.pdf",
            raw_text=duplicate_text,
        )
        _seed_duplicate_reference_chunk(
            seed_session,
            chunk_text=duplicate_text,
            notion_path=duplicate_path,
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        # No provider registered; duplicate path should bypass LLM call.
        return ProviderRouter()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post("/api/supplement/propose", json={"source_document_id": 2})

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["duplicate_detected"] is True
        assert payload["duplicate_notion_path"] == duplicate_path
        assert payload["provider"] is None
        assert payload["model"] is None

        verify_session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, payload["change_request_id"])
            assert change_request is not None
            assert change_request.status == "pending"

            proposal_payload = json.loads(change_request.proposal_json)
            assert proposal_payload["target_path"] == duplicate_path
            assert "duplicate" in proposal_payload["summary"].lower()
            assert duplicate_path in proposal_payload["notes"][1]

            workflow_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "supplement"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["provider_name"] == "openai"
            assert metadata["model"] == "gpt-4o-mini"
            assert metadata["prompt_id"] == "supplement_proposal"
            assert metadata["prompt_version"] == "supplement_proposal_v7"
            assert metadata["estimated_cost"] is None

            # No Notion write should happen. Existing seeded rows remain unchanged.
            assert verify_session.query(NotionPage).count() == 1
            assert verify_session.query(NotionBlock).count() == 1
            assert verify_session.query(KnowledgeChunk).count() == 1
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_propose_api_returns_llm_output_invalid() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=3,
            source_type="url",
            source_display_name="https://example.com/nlp-week5",
            raw_text="Article text about attention and residual connections.",
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(_FakeProposalProvider(output_text="{invalid-json}"))
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post("/api/supplement/propose", json={"source_document_id": 3})

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert detail["error_code"] == "LLM_OUTPUT_INVALID"
        assert detail["failure_reason"] == "LLM_OUTPUT_INVALID"
        assert detail["workflow_run_id"] is not None

        verify_session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "supplement"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "LLM_OUTPUT_INVALID"
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["provider_name"] == "openai"
            assert metadata["model"] == "gpt-4o-mini"
            assert metadata["prompt_id"] == "supplement_proposal"
            assert metadata["prompt_version"] == "supplement_proposal_v7"
            assert metadata["failure_stage"] == "provider_output_validation"
            assert metadata["validation_field"] == "provider_output"
            assert metadata["estimated_cost"] == pytest.approx(0.000072)
            assert verify_session.query(ChangeRequest).count() == 0
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_propose_api_returns_provider_not_found_when_provider_missing() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=4,
            source_type="chat_text",
            source_display_name="chat-2026-06-17",
            raw_text="Notes about residual connections and layer normalization.",
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        return ProviderRouter()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/supplement/propose",
            json={
                "source_document_id": 4,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            },
        )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "PROVIDER_NOT_FOUND"
        assert detail["failure_reason"] == "PROVIDER_NOT_FOUND"
        assert detail["workflow_run_id"] is not None

        verify_session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "supplement"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "PROVIDER_NOT_FOUND"
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["provider_name"] == "openai"
            assert metadata["model"] == "gpt-4o-mini"
            assert metadata["prompt_id"] == "supplement_proposal"
            assert metadata["prompt_version"] == "supplement_proposal_v7"
            assert metadata["estimated_cost"] is None
            assert verify_session.query(ChangeRequest).count() == 0
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_accept_api_appends_and_reindexes_before_accepting() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    snapshot_pages = {
        "page-accept-1": InMemoryNotionPageSnapshot(
            page_id="page-accept-1",
            title="NLP Week 5",
            notion_path="Knowledge/NLP/Week5",
            original_blocks=[
                "Attention aligns query and key vectors.",
            ],
        )
    }
    try:
        _seed_notion_page(
            seed_session,
            page_db_id=101,
            notion_page_id="page-accept-1",
            title="NLP Week 5",
            notion_path="Knowledge/NLP/Week5",
        )
        _seed_change_request(
            seed_session,
            change_request_id=11,
            status="pending",
            target_notion_page_id=101,
            proposal_json=json.dumps(
                {
                    "title": "Positional Encoding Supplement",
                    "target_path": "Knowledge/NLP/Week5/AI Supplement Zone/Positional Encoding Supplement",
                    "source": {
                        "source_type": "pdf",
                        "source_display_name": "week5-attention.pdf",
                    },
                    "summary": "Adds concise positional encoding notes for Week 5.",
                    "concepts": ["positional encoding", "length generalization"],
                    "notes": ["Compare sinusoidal and learned embeddings."],
                }
            ),
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_review_tool_registry(snapshot_pages)

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
            "/api/supplement/accept",
            json={"change_request_id": 11, "reviewer": "reviewer-a"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["change_request_id"] == 11
        assert payload["change_request_status"] == "accepted"
        assert payload["review_action"] == "accept"
        assert payload["reviewer"] == "reviewer-a"
        assert payload["reason"] is None

        verify_session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, 11)
            assert change_request is not None
            assert change_request.status == "accepted"

            workflow_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "supplement"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None

            indexing_runs = (
                verify_session.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "indexing")
                .all()
            )
            assert len(indexing_runs) == 1
            assert indexing_runs[0].status == "succeeded"
            assert indexing_runs[0].failure_reason is None
            indexing_metadata = json.loads(indexing_runs[0].metadata_json or "{}")
            assert indexing_metadata["sync_mode"] == "auto_after_accept"
            assert indexing_metadata["embedding_provider"] == "openai"
            assert indexing_metadata["embedding_model"] == "text-embedding-3-small"
            assert indexing_metadata["embedding_dimensions"] == 1536
            assert indexing_metadata["embedding_token_input"] >= 10
            assert indexing_metadata["embedding_estimated_cost"] is not None

            page = (
                verify_session.query(NotionPage)
                .filter(NotionPage.notion_page_id == "page-accept-1")
                .one_or_none()
            )
            assert page is not None
            blocks = (
                verify_session.query(NotionBlock)
                .filter(NotionBlock.notion_page_id == page.id)
                .all()
            )
            assert len(blocks) >= 5
            assert any(
                "Summary: Adds concise positional encoding notes for Week 5."
                in (block.content_text or "")
                for block in blocks
            )

            chunks = (
                verify_session.query(KnowledgeChunk)
                .filter(KnowledgeChunk.source_kind == "notion")
                .all()
            )
            assert len(chunks) >= 1
            assert any(
                "Summary: Adds concise positional encoding notes for Week 5."
                in chunk.chunk_text
                for chunk in chunks
            )
            assert all(chunk.embedding is not None for chunk in chunks)
            assert all(chunk.embedding_text is not None for chunk in chunks)
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_accept_queue_timeout_is_neutral_and_recoverable() -> None:
    session_factory = _build_session_factory()
    snapshot_pages = {
        "page-accept-timeout": InMemoryNotionPageSnapshot(
            page_id="page-accept-timeout",
            title="Timeout Page",
            notion_path="Knowledge/Timeout",
            original_blocks=["Original note remains unchanged."],
        )
    }
    seed_session = session_factory()
    try:
        _seed_notion_page(
            seed_session,
            page_db_id=113,
            notion_page_id="page-accept-timeout",
            title="Timeout Page",
            notion_path="Knowledge/Timeout",
        )
        _seed_change_request(
            seed_session,
            change_request_id=113,
            status="pending",
            target_notion_page_id=113,
            proposal_json=json.dumps(
                {
                    "title": "Timeout Supplement",
                    "target_path": "Knowledge/Timeout/AI Supplement Zone/Timeout Supplement",
                    "source": {
                        "source_type": "chat_text",
                        "source_display_name": "timeout-source",
                    },
                    "summary": "The append must remain recoverable after a queue timeout.",
                    "concepts": ["queue timeout"],
                    "notes": ["Reconcile the visible change-request identity before retry."],
                }
            ),
        )
    finally:
        seed_session.close()

    writer_client = InMemoryNotionWriterClient(snapshot_pages)

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(
            NotionReaderTool(_TimeoutNotionReaderClient(snapshot_pages))
        )
        registry.register_tool(NotionWriterTool(writer_client))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        response = TestClient(app).post(
            "/api/supplement/accept",
            json={"change_request_id": 113, "reviewer": "timeout-reviewer"},
        )

        assert response.status_code == 504
        detail = response.json()["detail"]
        assert detail["error_code"] == "QUEUE_JOB_TIMEOUT"
        assert detail["failure_reason"] == "QUEUE_JOB_TIMEOUT"

        verify_session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, 113)
            assert change_request is not None
            assert change_request.status == "pending"
            workflow = verify_session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow is not None
            assert workflow.status == "failed"
            assert workflow.failure_reason == "QUEUE_JOB_TIMEOUT"
            indexing_runs = (
                verify_session.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "indexing")
                .all()
            )
            assert len(indexing_runs) == 1
            assert indexing_runs[0].status == "failed"
            assert indexing_runs[0].failure_reason == "QUEUE_JOB_TIMEOUT"
        finally:
            verify_session.close()

        assert len(writer_client.list_operations(page_id="page-accept-timeout")) == 1
        assert len(snapshot_pages["page-accept-timeout"].ai_supplement_entries) == 1
    finally:
        app.dependency_overrides.clear()


def test_supplement_accept_api_requires_target_page_for_safe_append() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_change_request(
            seed_session,
            change_request_id=111,
            status="pending",
            target_notion_page_id=None,
            proposal_json=json.dumps(
                {
                    "title": "Missing Target Page",
                    "target_path": "Knowledge/NLP/Week5/AI Supplement Zone/Missing Target Page",
                    "source": {
                        "source_type": "chat_text",
                        "source_display_name": "chat-source",
                    },
                    "summary": "Should fail before write because target page is missing.",
                    "concepts": ["safety check"],
                    "notes": ["target_notion_page_id is required for accept append."],
                }
            ),
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_review_tool_registry({})

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/supplement/accept",
            json={"change_request_id": 111, "reviewer": "reviewer-a"},
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error_code"] == "WRITE_POLICY_VIOLATION"
        assert detail["failure_reason"] == "WRITE_POLICY_VIOLATION"
        assert detail["workflow_run_id"] is not None

        verify_session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, 111)
            assert change_request is not None
            assert change_request.status == "pending"

            indexing_runs = (
                verify_session.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "indexing")
                .all()
            )
            assert len(indexing_runs) == 0
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_accept_retry_reuses_verified_append_after_reindex_failure() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    snapshot_pages = {
        "page-accept-retry": InMemoryNotionPageSnapshot(
            page_id="page-accept-retry",
            title="Retry Page",
            notion_path="Knowledge/Retry",
            original_blocks=["Original note remains unchanged."],
        )
    }
    writer_client = InMemoryNotionWriterClient(snapshot_pages)
    embedding_client = _FailOnceEmbeddingClient()
    try:
        _seed_notion_page(
            seed_session,
            page_db_id=112,
            notion_page_id="page-accept-retry",
            title="Retry Page",
            notion_path="Knowledge/Retry",
        )
        _seed_change_request(
            seed_session,
            change_request_id=112,
            status="pending",
            target_notion_page_id=112,
            proposal_json=json.dumps(
                {
                    "title": "Retryable Supplement",
                    "target_path": "Knowledge/Retry/AI Supplement Zone/Retryable Supplement",
                    "source": {
                        "source_type": "chat_text",
                        "source_display_name": "retry-source",
                    },
                    "summary": "A supplement that can be retried safely.",
                    "concepts": ["durable append"],
                    "notes": ["Do not duplicate the visible entry."],
                }
            ),
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(
            NotionReaderTool(_SnapshotBackedNotionReaderClient(snapshot_pages))
        )
        registry.register_tool(NotionWriterTool(writer_client))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = lambda: embedding_client

    try:
        client = TestClient(app)
        first_response = client.post(
            "/api/supplement/accept",
            json={"change_request_id": 112, "reviewer": "retry-reviewer"},
        )
        assert first_response.status_code == 502
        first_detail = first_response.json()["detail"]
        assert first_detail["error_code"] == "PAGE_REINDEX_FAILED"

        verify_session = session_factory()
        try:
            first_change_request = verify_session.get(ChangeRequest, 112)
            assert first_change_request is not None
            assert first_change_request.status == "pending"
        finally:
            verify_session.close()

        second_response = client.post(
            "/api/supplement/accept",
            json={"change_request_id": 112, "reviewer": "retry-reviewer"},
        )
        assert second_response.status_code == 200
        assert second_response.json()["change_request_status"] == "accepted"

        page_snapshot = snapshot_pages["page-accept-retry"]
        assert len(page_snapshot.ai_supplement_entries) == 1
        assert len(writer_client.list_operations(page_id="page-accept-retry")) == 1
    finally:
        app.dependency_overrides.clear()


def test_supplement_reject_api_transitions_pending_to_rejected_without_notion_write() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_change_request(seed_session, change_request_id=12, status="pending")
        _seed_duplicate_reference_chunk(
            seed_session,
            chunk_text="Existing notion text",
            notion_path="Knowledge/NLP/Week5/Existing",
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_review_tool_registry({})

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        before_session = session_factory()
        try:
            before_page_count = before_session.query(NotionPage).count()
            before_block_count = before_session.query(NotionBlock).count()
            before_chunk_count = before_session.query(KnowledgeChunk).count()
        finally:
            before_session.close()

        response = client.post(
            "/api/supplement/reject",
            json={
                "change_request_id": 12,
                "reviewer": "reviewer-b",
                "reason": "Out of scope for current note.",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["change_request_status"] == "rejected"
        assert payload["review_action"] == "reject"
        assert payload["reason"] == "Out of scope for current note."

        verify_session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, 12)
            assert change_request is not None
            assert change_request.status == "rejected"

            # Step 29 reject path must not perform Notion writes.
            assert verify_session.query(NotionPage).count() == before_page_count
            assert verify_session.query(NotionBlock).count() == before_block_count
            assert verify_session.query(KnowledgeChunk).count() == before_chunk_count
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_edit_later_api_keeps_pending_status() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_change_request(seed_session, change_request_id=13, status="pending")
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_review_tool_registry({})

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/supplement/edit-later",
            json={
                "change_request_id": 13,
                "reviewer": "reviewer-c",
                "reason": "Need more examples before final decision.",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["change_request_status"] == "pending"
        assert payload["review_action"] == "edit_later"
        assert payload["reason"] == "Need more examples before final decision."

        verify_session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, 13)
            assert change_request is not None
            assert change_request.status == "pending"
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_review_api_rejects_invalid_state_transition() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_change_request(seed_session, change_request_id=14, status="accepted")
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_review_tool_registry({})

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/supplement/reject",
            json={
                "change_request_id": 14,
                "reason": "Should fail because already accepted.",
            },
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error_code"] == "INVALID_STATE_TRANSITION"
        assert detail["failure_reason"] == "UNKNOWN_ERROR"
        assert detail["workflow_run_id"] is not None

        verify_session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "supplement"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "UNKNOWN_ERROR"
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_supplement_review_api_returns_change_request_not_found() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_review_tool_registry({})

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/supplement/accept",
            json={"change_request_id": 99999},
        )
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert detail["error_code"] == "CHANGE_REQUEST_NOT_FOUND"
        assert detail["failure_reason"] == "CHANGE_REQUEST_NOT_FOUND"
        assert detail["workflow_run_id"] is not None
    finally:
        app.dependency_overrides.clear()


def test_supplement_pending_list_and_detail_expose_review_content_and_external_target() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    proposal_json = json.dumps(
        {
            "title": "Reviewable supplement",
            "target_path": "Knowledge/NLP/Week5/AI Supplement Zone/Reviewable",
            "source": {
                "source_type": "pdf",
                "source_display_name": "week5.pdf",
            },
            "summary": "A grounded review summary.",
            "concepts": ["attention"],
            "notes": ["Check the source before accepting."],
            "citations": [
                {
                    "notion_path": "Knowledge/NLP/Week5/Attention",
                    "quote": "Query and key vectors determine relevance.",
                }
            ],
        }
    )
    try:
        _seed_notion_page(
            seed_session,
            page_db_id=201,
            notion_page_id="notion-page-external-201",
            title="NLP Week 5",
            notion_path="Knowledge/NLP/Week5",
        )
        _seed_change_request(
            seed_session,
            change_request_id=201,
            status="pending",
            target_notion_page_id=201,
            proposal_json=proposal_json,
        )
        _seed_change_request(
            seed_session,
            change_request_id=202,
            status="accepted",
            target_notion_page_id=201,
            proposal_json=proposal_json,
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    try:
        client = TestClient(app)
        list_response = client.get("/api/supplement/pending")
        assert list_response.status_code == 200
        list_payload = list_response.json()
        assert list_payload["status"] == "succeeded"
        assert list_payload["count"] == 1
        item = list_payload["items"][0]
        assert item["change_request_id"] == 201
        assert item["target_notion_page_id"] == "notion-page-external-201"
        assert item["target_page"]["title"] == "NLP Week 5"
        assert item["proposal"]["summary"] == "A grounded review summary."
        assert item["citations"][0]["notion_path"] == "Knowledge/NLP/Week5/Attention"
        assert item["citations"][0]["quote"] == "Query and key vectors determine relevance."

        detail_response = client.get("/api/supplement/201")
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()
        assert detail_payload["status"] == "pending"
        assert detail_payload["proposal"]["title"] == "Reviewable supplement"
        assert detail_payload["citations"] == item["citations"]
    finally:
        app.dependency_overrides.clear()


def test_supplement_propose_resolves_external_target_and_rejects_unknown_target() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_source_document(
            seed_session,
            source_document_id=203,
            source_type="chat_text",
            source_display_name="chat-target",
            raw_text="Notes about external Notion page targeting.",
        )
        _seed_notion_page(
            seed_session,
            page_db_id=203,
            notion_page_id="notion-page-external-203",
            title="Targetable page",
            notion_path="Knowledge/Targetable",
        )
    finally:
        seed_session.close()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(
            _FakeProposalProvider(
                output_text=json.dumps(
                    {
                        "title": "Targeted proposal",
                        "target_path": "Knowledge/Targetable/AI Supplement Zone",
                        "source": {
                            "source_type": "chat_text",
                            "source_display_name": "chat-target",
                        },
                        "summary": "Targeted content.",
                        "concepts": ["targeting"],
                        "notes": ["Review before accept."],
                    }
                )
            )
        )
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/supplement/propose",
            json={
                "source_document_id": 203,
                "target_notion_page_id": "notion-page-external-203",
            },
        )
        assert response.status_code == 200
        assert response.json()["target_notion_page_id"] == "notion-page-external-203"

        verify_session = session_factory()
        try:
            stored = verify_session.get(ChangeRequest, response.json()["change_request_id"])
            assert stored is not None
            assert stored.target_notion_page_id == 203
        finally:
            verify_session.close()

        unknown_response = client.post(
            "/api/supplement/propose",
            json={
                "source_document_id": 203,
                "target_notion_page_id": "notion-page-does-not-exist",
            },
        )
        assert unknown_response.status_code == 404
        assert unknown_response.json()["detail"]["error_code"] == "NOTION_PAGE_NOT_FOUND"
        assert unknown_response.json()["detail"]["failure_reason"] == "NOTION_PAGE_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()
