from __future__ import annotations

import json
from dataclasses import dataclass

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
from src.app.api.routes.telegram import get_telegram_session_store
from src.app.legacy import app
from src.db.base import Base
from src.db.models import (
    ChangeRequest,
    KnowledgeChunk,
    NotionBlock,
    NotionPage,
    SourceDocument,
    TelegramUpdateLedger,
    WorkflowRun,
)
from src.db.session import get_db_session, get_db_session_factory, get_unit_of_work_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
    get_openai_embedding_capabilities,
)
from src.tools import (
    DisabledTelegramBotClient,
    ImageOCRParserClient,
    ImageOCRTool,
    InMemoryNotionPageSnapshot,
    InMemoryNotionWriterClient,
    InMemoryTelegramBotClient,
    NotionBlockNode,
    NotionPageTree,
    NotionReaderClient,
    NotionReaderTool,
    NotionWriterTool,
    OCRImageInput,
    PDFParserClient,
    PDFParserClientError,
    PDFParserTool,
    ParsedImageOCR,
    TelegramBotSendError,
    TelegramBotTool,
    ToolRegistry,
)
from src.services import InMemoryTelegramSessionStore


@dataclass
class _FakeParsedPDF:
    raw_text: str
    page_count: int


class _FakePDFParserClient(PDFParserClient):
    def __init__(
        self,
        *,
        raw_text: str = "",
        page_count: int = 1,
        should_fail: bool = False,
        failure_message: str = "parse failed",
    ) -> None:
        self._raw_text = raw_text
        self._page_count = page_count
        self._should_fail = should_fail
        self._failure_message = failure_message

    def parse_document(self, *, file_name: str, file_bytes: bytes):
        _ = file_name
        _ = file_bytes
        if self._should_fail:
            raise PDFParserClientError(self._failure_message)
        return _FakeParsedPDF(raw_text=self._raw_text, page_count=self._page_count)


class _FakeImageOCRParserClient(ImageOCRParserClient):
    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        lines = [
            f"OCR[{index}] {image.file_name}"
            for index, image in enumerate(images, start=1)
        ]
        return ParsedImageOCR(
            raw_text="\n".join(lines),
            image_count=len(images),
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
            token_output=80,
        )


class _FakeQAProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text="Attention aligns query and key to weight values.",
            token_input=25,
            token_output=10,
        )


class _SnapshotBackedNotionReaderClient(NotionReaderClient):
    def __init__(self, pages: dict[str, InMemoryNotionPageSnapshot]) -> None:
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


class _CallbackAckFailureTelegramClient(InMemoryTelegramBotClient):
    def answer_callback_query(self, *, callback_query_id: str, text=None) -> None:
        _ = callback_query_id
        _ = text
        raise TelegramBotSendError("Telegram callback acknowledgement timed out")


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            WorkflowRun.__table__,
            SourceDocument.__table__,
            ChangeRequest.__table__,
            KnowledgeChunk.__table__,
            NotionBlock.__table__,
            NotionPage.__table__,
            TelegramUpdateLedger.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _build_review_tool_registry(
    *,
    snapshot_pages: dict[str, InMemoryNotionPageSnapshot],
    telegram_client: InMemoryTelegramBotClient,
) -> tuple[ToolRegistry, InMemoryNotionWriterClient]:
    writer_client = InMemoryNotionWriterClient(snapshot_pages)
    registry = ToolRegistry()
    registry.register_tool(TelegramBotTool(telegram_client))
    registry.register_tool(
        NotionReaderTool(_SnapshotBackedNotionReaderClient(snapshot_pages))
    )
    registry.register_tool(NotionWriterTool(writer_client))
    return registry, writer_client


def _embedding_client_override() -> EmbeddingClient:
    return _FakeEmbeddingClient()


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


def _seed_qa_chunks(session: Session) -> None:
    nlp_page = NotionPage(
        id=1,
        notion_page_id="page-nlp-week5",
        title="NLP Week 5",
        notion_path="Knowledge/NLP/Week5",
    )
    ml_page = NotionPage(
        id=2,
        notion_page_id="page-ml-week2",
        title="ML Week 2",
        notion_path="Knowledge/ML/Week2",
    )
    session.add_all([nlp_page, ml_page])
    session.flush()

    nlp_block = NotionBlock(
        id=1,
        notion_block_id="blk-nlp-attention",
        notion_page_id=nlp_page.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="Attention uses query key value vectors",
        block_path="Knowledge/NLP/Week5/Attention",
        block_order=0,
    )
    ml_block = NotionBlock(
        id=2,
        notion_block_id="blk-ml-attention",
        notion_page_id=ml_page.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="Attention can operate over image patches",
        block_path="Knowledge/ML/Week2/Vision Attention",
        block_order=0,
    )
    session.add_all([nlp_block, ml_block])
    session.flush()

    session.add_all(
        [
            KnowledgeChunk(
                id=1,
                source_document_id=None,
                notion_block_id=nlp_block.id,
                chunk_index=0,
                chunk_text="Attention uses query key value vectors",
                notion_path="Knowledge/NLP/Week5/Attention",
                embedding_text=None,
                source_kind="notion",
            ),
            KnowledgeChunk(
                id=2,
                source_document_id=None,
                notion_block_id=ml_block.id,
                chunk_index=0,
                chunk_text="Attention can operate over image patches",
                notion_path="Knowledge/ML/Week2/Vision Attention",
                embedding_text=None,
                source_kind="notion",
            ),
        ]
    )
    session.commit()


def test_telegram_webhook_help_command_sends_reply() -> None:
    session_factory = _build_session_factory()
    telegram_client = InMemoryTelegramBotClient()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1001,
                "message": {
                    "message_id": 11,
                    "chat": {"id": 555},
                    "text": "/help",
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["handled"] is True
        assert payload["command"] == "help"
        assert payload["reply_text"].startswith("LearnLoop Agent commands:")
        assert "/pages" in payload["reply_text"]
        assert "/ingest" in payload["reply_text"]
        assert "/status" in payload["reply_text"]
        assert "/stats" in payload["reply_text"]
        assert "Status distinguishes liveness from dependency readiness" in payload[
            "reply_text"
        ]
        assert "stats show only aggregate counts and safe timestamps" in payload[
            "reply_text"
        ]
        assert "do not need to type a Notion UUID" in payload["reply_text"]
        assert payload["telegram_message_id"] == 1
        assert payload["skipped_reason"] is None
        assert payload["source_document_id"] is None
        assert payload["change_request_id"] is None
        assert payload["source_type"] is None

        duplicate_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1001,
                "message": {
                    "message_id": 11,
                    "chat": {"id": 555},
                    "text": "/help",
                },
            },
        )
        assert duplicate_response.status_code == 200
        assert duplicate_response.json() == payload

        sent_messages = telegram_client.list_sent_messages()
        assert len(sent_messages) == 1
        assert sent_messages[0].chat_id == "555"
        assert sent_messages[0].text == payload["reply_text"]

        verify_session: Session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "telegram"
            assert workflow_run.status == "succeeded"
            metadata = json.loads(workflow_run.metadata_json or "{}")
            assert metadata["operation"] == "telegram_webhook"
            assert metadata["command"] == "help"
            assert metadata["handled"] is True
            assert metadata["telegram_message_id"] == 1
            ledger = verify_session.get(TelegramUpdateLedger, 1001)
            assert ledger is not None
            assert ledger.status == "succeeded"
            assert ledger.workflow_run_id == payload["workflow_run_id"]
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_skips_non_text_message() -> None:
    session_factory = _build_session_factory()
    telegram_client = InMemoryTelegramBotClient()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1002,
                "message": {
                    "message_id": 12,
                    "chat": {"id": 888},
                    "text": "   ",
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["handled"] is False
        assert payload["command"] is None
        assert payload["reply_text"] is None
        assert payload["telegram_message_id"] is None
        assert payload["skipped_reason"] == "NO_TEXT_MESSAGE"
        assert telegram_client.list_sent_messages() == []
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_returns_service_unavailable_when_not_configured() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(DisabledTelegramBotClient()))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1003,
                "message": {
                    "message_id": 13,
                    "chat": {"id": 999},
                    "text": "/health",
                },
            },
        )

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["error_code"] == "TELEGRAM_NOT_CONFIGURED"
        assert detail["failure_reason"] == "TELEGRAM_NOT_CONFIGURED"
        assert detail["workflow_run_id"] is not None

        verify_session: Session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "telegram"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "TELEGRAM_NOT_CONFIGURED"
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_ask_returns_answer_with_scoped_notion_citation() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_qa_chunks(seed_session)
    finally:
        seed_session.close()
    telegram_client = InMemoryTelegramBotClient()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        return registry

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(_FakeQAProvider())
        return router

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1004,
                "message": {
                    "message_id": 14,
                    "chat": {"id": 777},
                    "text": (
                        "/ask --section Knowledge/NLP/Week5/Attention "
                        "Explain attention"
                    ),
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["handled"] is True
        assert payload["command"] == "ask"
        assert payload["qa_workflow_run_id"] is not None
        assert payload["insufficient_info"] is False
        assert payload["citations"] == ["Knowledge/NLP/Week5/Attention"]
        assert payload["source_document_id"] is None
        assert payload["change_request_id"] is None
        assert "Attention aligns query and key" in payload["reply_text"]
        assert "Notion citations:" in payload["reply_text"]
        assert "- Knowledge/NLP/Week5/Attention" in payload["reply_text"]
        assert "Knowledge/ML/Week2/Vision Attention" not in payload["reply_text"]

        sent_messages = telegram_client.list_sent_messages()
        assert len(sent_messages) == 1
        assert sent_messages[0].chat_id == "777"
        assert sent_messages[0].text == payload["reply_text"]

        verify_session: Session = session_factory()
        try:
            workflow_runs = verify_session.query(WorkflowRun).all()
            qa_run = next(row for row in workflow_runs if row.workflow_type == "qa")
            telegram_run = next(
                row for row in workflow_runs if row.id == payload["workflow_run_id"]
            )
            qa_metadata = json.loads(qa_run.metadata_json or "{}")
            assert qa_metadata["prompt_id"] == "qa_answer"
            assert qa_metadata["estimated_cost"] == pytest.approx(0.00000975)
            metadata = json.loads(telegram_run.metadata_json or "{}")
            assert metadata["command"] == "ask"
            assert metadata["qa_workflow_run_id"] == payload["qa_workflow_run_id"]
            assert metadata["citation_count"] == 1
            assert "citations" not in metadata
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_ask_without_question_returns_usage_reply() -> None:
    session_factory = _build_session_factory()
    telegram_client = InMemoryTelegramBotClient()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1005,
                "message": {
                    "message_id": 15,
                    "chat": {"id": 777},
                    "text": "/ask --page page-nlp-week5",
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["command"] == "ask"
        assert payload["qa_workflow_run_id"] is None
        assert payload["insufficient_info"] is None
        assert payload["citations"] == []
        assert payload["reply_text"].startswith("Usage: /ask")
        assert len(telegram_client.list_sent_messages()) == 1
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_ask_maps_qa_provider_failure() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_qa_chunks(seed_session)
    finally:
        seed_session.close()
    telegram_client = InMemoryTelegramBotClient()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 1006,
                "message": {
                    "message_id": 16,
                    "chat": {"id": 777},
                    "text": "/ask Explain attention",
                },
            },
        )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail["error_code"] == "PROVIDER_NOT_FOUND"
        assert detail["failure_reason"] == "PROVIDER_NOT_FOUND"
        assert detail["workflow_run_id"] is not None
        assert telegram_client.list_sent_messages() == []

        verify_session: Session = session_factory()
        try:
            workflow_runs = verify_session.query(WorkflowRun).all()
            qa_run = next(row for row in workflow_runs if row.workflow_type == "qa")
            telegram_run = next(
                row for row in workflow_runs if row.workflow_type == "telegram"
            )
            assert qa_run.status == "failed"
            assert qa_run.failure_reason == "PROVIDER_NOT_FOUND"
            assert telegram_run.status == "failed"
            assert telegram_run.failure_reason == "PROVIDER_NOT_FOUND"
            assert detail["workflow_run_id"] == telegram_run.id
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_accept_appends_and_reindexes_before_replying() -> None:
    session_factory = _build_session_factory()
    snapshot_pages = {
        "page-telegram-accept": InMemoryNotionPageSnapshot(
            page_id="page-telegram-accept",
            title="NLP Week 5",
            notion_path="Knowledge/NLP/Week5",
            original_blocks=["Original attention note remains unchanged."],
        )
    }
    seed_session = session_factory()
    try:
        _seed_notion_page(
            seed_session,
            page_db_id=101,
            notion_page_id="page-telegram-accept",
            title="NLP Week 5",
            notion_path="Knowledge/NLP/Week5",
        )
        _seed_change_request(
            seed_session,
            change_request_id=31,
            target_notion_page_id=101,
            proposal_json=json.dumps(
                {
                    "title": "Telegram Accept Supplement",
                    "target_path": (
                        "Knowledge/NLP/Week5/AI Supplement Zone/"
                        "Telegram Accept Supplement"
                    ),
                    "source": {
                        "source_type": "chat_text",
                        "source_display_name": "telegram-review-source",
                    },
                    "summary": "Accepted safely from Telegram review.",
                    "concepts": ["human review", "safe append"],
                    "notes": ["Re-index immediately after append."],
                }
            ),
        )
    finally:
        seed_session.close()

    telegram_client = InMemoryTelegramBotClient()
    registry, writer_client = _build_review_tool_registry(
        snapshot_pages=snapshot_pages,
        telegram_client=telegram_client,
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return registry

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
            "/api/telegram/webhook",
            json={
                "update_id": 3001,
                "message": {
                    "message_id": 31,
                    "chat": {"id": 2468},
                    "text": "/accept 31",
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["command"] == "accept"
        assert payload["change_request_id"] == 31
        assert payload["change_request_status"] == "accepted"
        assert payload["review_action"] == "accept"
        assert payload["review_workflow_run_id"] is not None
        assert "Appended to AI Supplement Zone" in payload["reply_text"]
        assert "page re-index completed" in payload["reply_text"]

        assert snapshot_pages["page-telegram-accept"].original_blocks == [
            "Original attention note remains unchanged."
        ]
        assert len(snapshot_pages["page-telegram-accept"].ai_supplement_entries) == 1
        operations = writer_client.list_operations(page_id="page-telegram-accept")
        assert len(operations) == 1
        assert operations[0].operation == "append_ai_supplement_zone"

        verify_session: Session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, 31)
            assert change_request is not None
            assert change_request.status == "accepted"

            review_run = verify_session.get(
                WorkflowRun,
                payload["review_workflow_run_id"],
            )
            assert review_run is not None
            review_metadata = json.loads(review_run.metadata_json or "{}")
            assert review_metadata["review_action"] == "accept"
            assert review_metadata["reviewer"] == "telegram-chat:2468"

            indexing_runs = (
                verify_session.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "indexing")
                .all()
            )
            assert len(indexing_runs) == 1
            indexing_metadata = json.loads(indexing_runs[0].metadata_json or "{}")
            assert indexing_metadata["sync_mode"] == "auto_after_accept"
            assert indexing_metadata["embedding_provider"] == "openai"
            assert indexing_metadata["embedding_model"] == "text-embedding-3-small"
            assert indexing_metadata["embedding_dimensions"] == 1536
            assert indexing_metadata["embedding_token_input"] >= 10
            assert indexing_metadata["embedding_estimated_cost"] is not None

            chunks = (
                verify_session.query(KnowledgeChunk)
                .filter(KnowledgeChunk.source_kind == "notion")
                .all()
            )
            assert len(chunks) >= 1
            assert all(chunk.embedding is not None for chunk in chunks)
            assert all(chunk.embedding_text is not None for chunk in chunks)

            telegram_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert telegram_run is not None
            gateway_metadata = json.loads(telegram_run.metadata_json or "{}")
            assert gateway_metadata["review_action"] == "accept"
            assert gateway_metadata["change_request_status"] == "accepted"
            assert gateway_metadata["review_workflow_run_id"] == review_run.id
            assert "reason" not in gateway_metadata
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_reject_updates_status_without_notion_write() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_change_request(seed_session, change_request_id=32)
    finally:
        seed_session.close()

    telegram_client = InMemoryTelegramBotClient()
    registry, writer_client = _build_review_tool_registry(
        snapshot_pages={},
        telegram_client=telegram_client,
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 3002,
                "message": {
                    "message_id": 32,
                    "chat": {"id": 1357},
                    "text": "/reject 32 Out of scope for this note",
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["command"] == "reject"
        assert payload["change_request_id"] == 32
        assert payload["change_request_status"] == "rejected"
        assert payload["review_action"] == "reject"
        assert payload["review_workflow_run_id"] is not None
        assert "No Notion write was performed" in payload["reply_text"]
        assert writer_client.list_operations() == []

        verify_session: Session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, 32)
            assert change_request is not None
            assert change_request.status == "rejected"
            assert (
                verify_session.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "indexing")
                .count()
                == 0
            )

            review_run = verify_session.get(
                WorkflowRun,
                payload["review_workflow_run_id"],
            )
            assert review_run is not None
            review_metadata = json.loads(review_run.metadata_json or "{}")
            assert review_metadata["reviewer"] == "telegram-chat:1357"
            assert review_metadata["reason"] == "Out of scope for this note"

            telegram_run = verify_session.get(WorkflowRun, payload["workflow_run_id"])
            assert telegram_run is not None
            gateway_metadata = json.loads(telegram_run.metadata_json or "{}")
            assert gateway_metadata["review_action"] == "reject"
            assert gateway_metadata["change_request_status"] == "rejected"
            assert "reason" not in gateway_metadata
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_accept_fails_closed_without_target_page() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_change_request(
            seed_session,
            change_request_id=33,
            target_notion_page_id=None,
            proposal_json=json.dumps(
                {
                    "title": "Missing Target",
                    "target_path": "Knowledge/NLP/AI Supplement Zone/Missing Target",
                    "source": {
                        "source_type": "chat_text",
                        "source_display_name": "telegram-review-source",
                    },
                    "summary": "Must stay pending without a target page.",
                    "concepts": ["write safety"],
                    "notes": ["Fail closed."],
                }
            ),
        )
    finally:
        seed_session.close()

    telegram_client = InMemoryTelegramBotClient()
    registry, writer_client = _build_review_tool_registry(
        snapshot_pages={},
        telegram_client=telegram_client,
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 3003,
                "message": {
                    "message_id": 33,
                    "chat": {"id": 2468},
                    "text": "/accept 33",
                },
            },
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error_code"] == "WRITE_POLICY_VIOLATION"
        assert detail["failure_reason"] == "WRITE_POLICY_VIOLATION"
        assert detail["workflow_run_id"] is not None
        assert telegram_client.list_sent_messages() == []
        assert writer_client.list_operations() == []

        verify_session: Session = session_factory()
        try:
            change_request = verify_session.get(ChangeRequest, 33)
            assert change_request is not None
            assert change_request.status == "pending"
            workflows = verify_session.query(WorkflowRun).all()
            assert any(
                row.workflow_type == "supplement"
                and row.status == "failed"
                and row.failure_reason == "WRITE_POLICY_VIOLATION"
                for row in workflows
            )
            telegram_run = next(
                row for row in workflows if row.workflow_type == "telegram"
            )
            assert telegram_run.id == detail["workflow_run_id"]
            assert telegram_run.status == "failed"
            assert telegram_run.failure_reason == "WRITE_POLICY_VIOLATION"
            assert not any(row.workflow_type == "indexing" for row in workflows)
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_reject_without_reason_returns_usage_reply() -> None:
    session_factory = _build_session_factory()
    telegram_client = InMemoryTelegramBotClient()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 3004,
                "message": {
                    "message_id": 34,
                    "chat": {"id": 1357},
                    "text": "/reject 32",
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["command"] == "reject"
        assert payload["reply_text"] == "Usage: /reject <change_request_id> <reason>"
        assert payload["change_request_id"] is None
        assert payload["change_request_status"] is None
        assert payload["review_action"] == "reject"
        assert payload["review_workflow_run_id"] is None

        verify_session: Session = session_factory()
        try:
            assert (
                verify_session.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "supplement")
                .count()
                == 0
            )
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_ingest_pdf_creates_pending_change_request() -> None:
    session_factory = _build_session_factory()
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="pdf-file-1",
        file_bytes=b"%PDF-1.4 fake content",
        file_name="lesson.pdf",
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        registry.register_tool(
            PDFParserTool(
                _FakePDFParserClient(
                    raw_text="Transformer training notes from week 5",
                    page_count=1,
                )
            )
        )
        registry.register_tool(ImageOCRTool(_FakeImageOCRParserClient()))
        return registry

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(
            _FakeProposalProvider(
                output_text=json.dumps(
                    {
                        "title": "Transformer Week 5 Supplement",
                        "target_path": "Knowledge/NLP/Week5/AI Supplement Zone/Transformer Week 5",
                        "source": {
                            "source_type": "pdf",
                            "source_display_name": "lesson.pdf",
                        },
                        "summary": "Summarizes transformer training notes from the source PDF.",
                        "concepts": ["transformer", "training stability"],
                        "notes": ["Review optimizer and warmup settings."],
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
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2001,
                "message": {
                    "message_id": 21,
                    "chat": {"id": 12345},
                    "caption": "/ingest",
                    "document": {
                        "file_id": "pdf-file-1",
                        "file_name": "lesson.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error_code"] == "NOTION_PAGES_EMPTY"
        assert detail["failure_reason"] == "NOTION_PAGE_NOT_FOUND"

        verify_session: Session = session_factory()
        try:
            source_documents = verify_session.query(SourceDocument).all()
            assert len(source_documents) == 0

            change_requests = verify_session.query(ChangeRequest).all()
            assert len(change_requests) == 0

            workflow_runs = verify_session.query(WorkflowRun).all()
            assert any(row.workflow_type == "telegram" for row in workflow_runs)
            assert not any(row.workflow_type == "ingestion" for row in workflow_runs)
            assert not any(row.workflow_type == "supplement" for row in workflow_runs)
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_ingest_pdf_returns_file_download_failed() -> None:
    session_factory = _build_session_factory()
    telegram_client = InMemoryTelegramBotClient()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        return registry

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2002,
                "message": {
                    "message_id": 22,
                    "chat": {"id": 12345},
                    "caption": "/ingest",
                    "document": {
                        "file_id": "missing-pdf-file",
                        "file_name": "lesson.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error_code"] == "NOTION_PAGES_EMPTY"
        assert detail["failure_reason"] == "NOTION_PAGE_NOT_FOUND"
        assert detail["workflow_run_id"] is not None

        verify_session: Session = session_factory()
        try:
            workflow_run = verify_session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "telegram"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "NOTION_PAGE_NOT_FOUND"
            assert verify_session.query(SourceDocument).count() == 0
            assert verify_session.query(ChangeRequest).count() == 0
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_webhook_ingest_screenshot_batch_creates_one_source_and_change_request() -> None:
    session_factory = _build_session_factory()
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="photo-a",
        file_bytes=b"image-a-bytes",
        file_name="screenshot-a.png",
    )
    telegram_client.add_file(
        file_id="photo-b",
        file_bytes=b"image-b-bytes",
        file_name="screenshot-b.png",
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        registry.register_tool(
            PDFParserTool(
                _FakePDFParserClient(
                    raw_text="fallback pdf text",
                    page_count=1,
                )
            )
        )
        registry.register_tool(ImageOCRTool(_FakeImageOCRParserClient()))
        return registry

    def _provider_router_override() -> ProviderRouter:
        router = ProviderRouter()
        router.register_provider(
            _FakeProposalProvider(
                output_text=json.dumps(
                    {
                        "title": "OCR Screenshot Supplement",
                        "target_path": "Knowledge/NLP/Week5/AI Supplement Zone/OCR Screenshot Supplement",
                        "source": {
                            "source_type": "screenshot",
                            "source_display_name": "Screenshot batch (2 images)",
                        },
                        "summary": "Summarizes OCR text extracted from screenshot batch.",
                        "concepts": ["ocr capture", "batch notes"],
                        "notes": ["Validate extracted terms before accept."],
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
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_provider_router] = _provider_router_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 2002,
                "message": {
                    "message_id": 22,
                    "chat": {"id": 54321},
                    "caption": "/ingest",
                    "photo": [
                        {
                            "file_id": "photo-a",
                            "file_unique_id": "uniq-a",
                            "width": 1200,
                            "height": 800,
                            "file_size": 80000,
                        },
                        {
                            "file_id": "photo-b",
                            "file_unique_id": "uniq-b",
                            "width": 1200,
                            "height": 800,
                            "file_size": 81000,
                        },
                    ],
                },
            },
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error_code"] == "NOTION_PAGES_EMPTY"
        assert detail["failure_reason"] == "NOTION_PAGE_NOT_FOUND"

        verify_session: Session = session_factory()
        try:
            source_documents = verify_session.query(SourceDocument).all()
            assert len(source_documents) == 0

            change_requests = verify_session.query(ChangeRequest).all()
            assert len(change_requests) == 0
        finally:
            verify_session.close()
    finally:
        app.dependency_overrides.clear()


def test_telegram_pages_targeted_ingest_preview_and_accept_e2e(monkeypatch) -> None:
    session_factory = _build_session_factory()
    snapshot_pages = {
        "page-telegram-flow": InMemoryNotionPageSnapshot(
            page_id="page-telegram-flow",
            title="Targetable NLP Page",
            notion_path="Knowledge/NLP/Targetable",
            original_blocks=["Original note stays unchanged."],
        )
    }
    seed_session = session_factory()
    try:
        _seed_notion_page(
            seed_session,
            page_db_id=91,
            notion_page_id="page-telegram-flow",
            title="Targetable NLP Page",
            notion_path="Knowledge/NLP/Targetable",
        )
    finally:
        seed_session.close()

    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="flow-pdf",
        file_bytes=b"%PDF-1.4 flow content",
        file_name="flow.pdf",
    )
    registry, writer_client = _build_review_tool_registry(
        snapshot_pages=snapshot_pages,
        telegram_client=telegram_client,
    )
    registry.register_tool(
        PDFParserTool(
            _FakePDFParserClient(
                raw_text="Targeted Telegram learning notes.",
                page_count=1,
            )
        )
    )
    registry.register_tool(ImageOCRTool(_FakeImageOCRParserClient()))
    session_store = InMemoryTelegramSessionStore()
    monkeypatch.setenv("LEARNLOOP_NOTION_CANARY_PAGE_ID", "canary-only-page")

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
                        "title": "Targeted Telegram proposal",
                            "target_path": (
                                "Knowledge/NLP/Targetable/AI Supplement Zone"
                            ),
                        "source": {
                            "source_type": "pdf",
                            "source_display_name": "flow.pdf",
                        },
                        "summary": "A proposal previewed before human acceptance.",
                        "concepts": ["target selection", "human review"],
                        "notes": ["Review the source before accepting."],
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
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_telegram_session_store] = lambda: session_store
    app.dependency_overrides[get_provider_router] = _provider_router_override
    app.dependency_overrides[get_embedding_client] = _embedding_client_override

    try:
        client = TestClient(app)
        pages_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 4001,
                "message": {
                    "message_id": 41,
                    "chat": {"id": 7777},
                    "text": "/pages",
                },
            },
        )
        assert pages_response.status_code == 200
        assert "page-telegram-flow" in pages_response.json()["reply_text"]
        assert "Knowledge/" not in pages_response.json()["reply_text"]

        ingest_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 4002,
                "message": {
                    "message_id": 42,
                    "chat": {"id": 7777},
                    "caption": "/ingest --page page-telegram-flow",
                    "document": {
                        "file_id": "flow-pdf",
                        "file_name": "flow.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )
        assert ingest_response.status_code == 200
        ingest_payload = ingest_response.json()
        assert ingest_payload["target_notion_page_id"] == "page-telegram-flow"
        assert ingest_payload["change_request_status"] is None
        assert "Target Notion page: page-telegram-flow" in ingest_payload["reply_text"]
        assert "Proposal ready for review" in ingest_payload["reply_text"]
        assert "Review with /accept" in ingest_payload["reply_text"]
        change_request_id = ingest_payload["change_request_id"]
        ready_session = session_store.find_latest_upload(
            chat_id="7777", user_id="7777"
        )
        assert ready_session is not None
        ready_session.state = "ready_for_review"

        review_message = telegram_client.list_sent_messages()[-1]
        accept_callback_data = review_message.reply_markup["inline_keyboard"][0][0][
            "callback_data"
        ]
        accept_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 4003,
                "callback_query": {
                    "id": "accept-callback-4003",
                    "from": {"id": 7777},
                    "data": accept_callback_data,
                    "message": {"message_id": 43, "chat": {"id": 7777}},
                },
            },
        )
        assert accept_response.status_code == 200
        accept_payload = accept_response.json()
        assert accept_payload["change_request_status"] == "accepted"
        assert accept_payload["review_action"] == "accept"
        assert telegram_client.list_callback_answers() == [
            {"callback_query_id": "accept-callback-4003", "text": None}
        ]
        assert len(writer_client.list_operations(page_id="page-telegram-flow")) == 1
        assert len(snapshot_pages["page-telegram-flow"].ai_supplement_entries) == 1

        duplicate_accept_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 4003,
                "callback_query": {
                    "id": "accept-callback-4003",
                    "from": {"id": 7777},
                    "data": accept_callback_data,
                    "message": {"message_id": 43, "chat": {"id": 7777}},
                },
            },
        )
        assert duplicate_accept_response.status_code == 200
        assert duplicate_accept_response.json()["change_request_status"] == "accepted"
        assert len(writer_client.list_operations(page_id="page-telegram-flow")) == 1
        assert len(snapshot_pages["page-telegram-flow"].ai_supplement_entries) == 1
        assert telegram_client.list_callback_answers() == [
            {"callback_query_id": "accept-callback-4003", "text": None}
        ]
    finally:
        app.dependency_overrides.clear()


def test_review_accept_callback_ack_failure_still_executes_business_work() -> None:
    session_factory = _build_session_factory()
    snapshot_pages = {
        "page-review-callback": InMemoryNotionPageSnapshot(
            page_id="page-review-callback",
            title="Callback Review Page",
            notion_path="Knowledge/Callback Review",
            original_blocks=["Original note remains unchanged."],
        )
    }
    seed_session = session_factory()
    try:
        _seed_notion_page(
            seed_session,
            page_db_id=92,
            notion_page_id="page-review-callback",
            title="Callback Review Page",
            notion_path="Knowledge/Callback Review",
        )
        _seed_change_request(
            seed_session,
            change_request_id=44,
            target_notion_page_id=92,
            proposal_json=json.dumps(
                {
                    "title": "Callback Accept Proposal",
                    "target_path": (
                        "Knowledge/Callback Review/AI Supplement Zone/"
                        "Callback Accept Proposal"
                    ),
                    "source": {
                        "source_type": "screenshot",
                        "source_display_name": "callback-source",
                    },
                    "summary": "Accepted from the review callback.",
                    "concepts": ["callback routing"],
                    "notes": ["Callback acknowledgement is independent."],
                }
            ),
        )
    finally:
        seed_session.close()

    telegram_client = _CallbackAckFailureTelegramClient()
    registry, writer_client = _build_review_tool_registry(
        snapshot_pages=snapshot_pages,
        telegram_client=telegram_client,
    )
    session_store = InMemoryTelegramSessionStore()
    accept_token = session_store.create_callback(
        session_id="proposal-44",
        chat_id="8888",
        user_id="8888",
        action="accept",
        change_request_id=44,
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_embedding_client] = _embedding_client_override
    app.dependency_overrides[get_telegram_session_store] = lambda: session_store

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 4401,
                "callback_query": {
                    "id": "review-ack-failure",
                    "from": {"id": 8888},
                    "data": f"ll:{accept_token}",
                    "message": {"message_id": 44, "chat": {"id": 8888}},
                },
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["business_status"] == "succeeded"
        assert payload["callback_ack_status"] == "failed"
        assert payload["review_action"] == "accept"
        assert payload["change_request_status"] == "accepted"
        assert len(writer_client.list_operations(page_id="page-review-callback")) == 1

        session = session_factory()
        try:
            change_request = session.get(ChangeRequest, 44)
            assert change_request is not None
            assert change_request.status == "accepted"
            workflow = session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow is not None
            assert workflow.status == "succeeded"
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["callback_ack_failure_reason"] == (
                "TELEGRAM_CALLBACK_ACK_FAILED"
            )
            ledger = session.get(TelegramUpdateLedger, 4401)
            assert ledger is not None
            assert ledger.status == "succeeded"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_review_reject_callback_updates_status_without_notion_write() -> None:
    session_factory = _build_session_factory()
    seed_session = session_factory()
    try:
        _seed_change_request(
            seed_session,
            change_request_id=45,
            proposal_json='{"title":"Reject callback proposal"}',
        )
    finally:
        seed_session.close()

    telegram_client = InMemoryTelegramBotClient()
    registry = ToolRegistry()
    registry.register_tool(TelegramBotTool(telegram_client))
    session_store = InMemoryTelegramSessionStore()
    reject_token = session_store.create_callback(
        session_id="proposal-45",
        chat_id="8999",
        user_id="8999",
        action="reject",
        change_request_id=45,
    )

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_embedding_client] = _embedding_client_override
    app.dependency_overrides[get_telegram_session_store] = lambda: session_store

    try:
        client = TestClient(app)
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 4501,
                "callback_query": {
                    "id": "reject-callback",
                    "from": {"id": 8999},
                    "data": f"ll:{reject_token}",
                    "message": {"message_id": 45, "chat": {"id": 8999}},
                },
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["review_action"] == "reject"
        assert payload["change_request_status"] == "rejected"
        assert telegram_client.list_callback_answers() == [
            {"callback_query_id": "reject-callback", "text": None}
        ]

        session = session_factory()
        try:
            change_request = session.get(ChangeRequest, 45)
            assert change_request is not None
            assert change_request.status == "rejected"
            assert session.query(NotionBlock).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()
