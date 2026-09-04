from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
import fakeredis
from rq import Queue, SimpleWorker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.api.routes.telegram import get_telegram_session_store
from src.app.dependencies import (
    get_db_session_factory,
    get_embedding_client,
    get_provider_router,
    get_queue_client,
    get_tool_registry,
)
from src.app.legacy import app
from src.db.base import Base
from src.db.models import ChangeRequest, KnowledgeChunk, NotionBlock, NotionPage, SourceDocument, TelegramUpdateLedger, WorkflowRun
from src.db.session import get_db_session, get_unit_of_work_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers import LLMProvider, LLMRequest, LLMResponse, ProviderRouter
from src.services import (
    CostTracker,
    InMemoryTelegramSessionStore,
    PromptTemplateLoader,
    RedisTelegramSessionStore,
    TelegramSessionStore,
    TelegramUploadAttachment,
    TrustBoundaryService,
)
import src.worker.telegram as telegram_worker
from src.queue import RQQueueClient
from src.tools import (
    ImageOCRParserClient,
    ImageOCRTool,
    InMemoryTelegramBotClient,
    NotionReaderTool,
    NotionWriterTool,
    OCRImageInput,
    PDFParserClient,
    PDFParserTool,
    ParsedImageOCR,
    TelegramBotTool,
    TelegramBotSendError,
    ToolRegistry,
)


class _PDFParser(PDFParserClient):
    def parse_document(self, *, file_name: str, file_bytes: bytes):
        return SimpleNamespace(raw_text="PDF learning notes", page_count=1)


class _OCRParser(ImageOCRParserClient):
    def __init__(self, *, raw_text: str | None = None) -> None:
        self._raw_text = raw_text

    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        return ParsedImageOCR(
            raw_text=self._raw_text or (
                "Target selection and source preview are shown before human review.\n"
                + "\n".join(image.file_name for image in images)
            ),
            image_count=len(images),
        )


class _ProposalProvider(LLMProvider):
    def __init__(self, *, source_type: str, screenshot_count: int = 1) -> None:
        self._source_type = source_type
        self._screenshot_count = screenshot_count
        self.calls = 0

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        self.calls += 1
        is_screenshot = self._source_type == "screenshot"
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=json.dumps(
                {
                    "title": (
                        "Target selection and source preview"
                        if is_screenshot
                        else "Telegram target-aware supplement"
                    ),
                    "target_path": (
                        "Knowledge/Parent/AI Supplement Zone"
                        if self._source_type == "pdf"
                        else "Knowledge/Parent/Child/AI Supplement Zone"
                    ),
                    "source": {
                        "source_type": self._source_type,
                        "source_display_name": (
                            "lesson.pdf"
                            if self._source_type == "pdf"
                            else f"Screenshot batch ({self._screenshot_count} images)"
                        ),
                    },
                    "summary": (
                        "The screenshots show target selection and source preview "
                        "before human review."
                        if is_screenshot
                        else "Proposal created after a page button selection."
                    ),
                    "concepts": (
                        ["target selection", "source preview", "human review"]
                        if is_screenshot
                        else ["target selection"]
                    ),
                    "notes": (
                        [
                            "The source shows target selection.",
                            "The source shows source preview.",
                            "The source shows human review.",
                        ]
                        if is_screenshot
                        else ["Review before accepting."]
                    ),
                }
            ),
            token_input=10,
            token_output=10,
        )


class _InvalidTargetProposalProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        _ = request
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=json.dumps(
                {
                    "title": "Invalid target proposal",
                    "target_path": "Knowledge/Other Page/AI Supplement Zone",
                    "source": {
                        "source_type": "pdf",
                        "source_display_name": "lesson.pdf",
                    },
                    "summary": "The target is intentionally outside the selected page.",
                    "concepts": ["target validation"],
                    "notes": ["This must fail closed."],
                }
            ),
            token_input=10,
            token_output=10,
        )


class _GroundingFailureProposalProvider(LLMProvider):
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=json.dumps(
                {
                    "title": "MySQL EXPLAIN 與索引查詢",
                    "target_path": "Knowledge/Parent/Child/AI Supplement Zone",
                    "source": {
                        "source_type": "screenshot",
                        "source_display_name": "Screenshot batch (1 images)",
                    },
                    "summary": "MySQL EXPLAIN 會改善查詢效能。",
                    "concepts": ["MySQL", "EXPLAIN", "索引"],
                    "notes": [
                        "MySQL EXPLAIN 顯示查詢的執行計畫。",
                        "索引可協助查詢條件過濾。",
                        "畫面列出 type、key、rows 欄位。",
                    ],
                }
            ),
            token_input=120,
            token_output=90,
        )


class _CallbackAckTimeoutTelegramClient(InMemoryTelegramBotClient):
    def __init__(self) -> None:
        super().__init__()
        self.callback_ack_attempts = 0

    def answer_callback_query(self, *, callback_query_id: str, text=None) -> None:
        _ = callback_query_id
        _ = text
        self.callback_ack_attempts += 1
        raise TelegramBotSendError("Telegram callback acknowledgement timed out")


class _PreviewFailureTelegramClient(InMemoryTelegramBotClient):
    def __init__(self) -> None:
        super().__init__()
        self.preview_failure_attempts = 0

    def send_message(self, *, chat_id: str, text: str, reply_markup=None):
        is_review_preview = bool(reply_markup) and any(
            button.get("text") == "Accept"
            for row in reply_markup.get("inline_keyboard", [])
            for button in row
        )
        if is_review_preview:
            self.preview_failure_attempts += 1
            raise TelegramBotSendError("Telegram sendMessage timed out")
        return super().send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )


def _session_factory():
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


def _seed_pages(session_factory) -> None:
    session = session_factory()
    try:
        session.add_all(
            [
                NotionPage(
                    id=1,
                    notion_page_id="notion-parent-canonical",
                    title="Parent",
                    notion_path="Knowledge/Parent",
                ),
                NotionPage(
                    id=2,
                    notion_page_id="notion-child-canonical",
                    title="Child",
                    notion_path="Knowledge/Parent/Child",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


def _seed_hierarchical_pages(session_factory) -> None:
    session = session_factory()
    try:
        session.add_all(
            [
                NotionPage(
                    id=1,
                    notion_page_id="hierarchy-parent",
                    title="Parent",
                    notion_path="Knowledge/Parent",
                ),
                NotionPage(
                    id=2,
                    notion_page_id="hierarchy-child",
                    title="Child",
                    notion_path="Knowledge/Parent/Child",
                    parent_notion_page_id="hierarchy-parent",
                ),
                NotionPage(
                    id=3,
                    notion_page_id="hierarchy-grandchild",
                    title="Grandchild",
                    notion_path="Knowledge/Parent/Child/Grandchild",
                    parent_notion_page_id="hierarchy-child",
                ),
                NotionPage(
                    id=4,
                    notion_page_id="hierarchy-other",
                    title="Other root",
                    notion_path="Knowledge/Z Other root",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


def _configure_app(
    *,
    session_factory,
    telegram_client: InMemoryTelegramBotClient,
    session_store: TelegramSessionStore,
    source_type: str,
    ocr_parser: ImageOCRParserClient | None = None,
    proposal_provider: LLMProvider | None = None,
) -> None:
    registry = ToolRegistry()
    registry.register_tool(TelegramBotTool(telegram_client))
    registry.register_tool(PDFParserTool(_PDFParser()))
    registry.register_tool(ImageOCRTool(ocr_parser or _OCRParser()))
    router = ProviderRouter()
    router.register_provider(
        proposal_provider or _ProposalProvider(source_type=source_type)
    )

    def db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_provider_router] = lambda: router
    app.dependency_overrides[get_embedding_client] = lambda: None
    app.dependency_overrides[get_telegram_session_store] = lambda: session_store


def _page_callback_data(
    telegram_client: InMemoryTelegramBotClient,
    *,
    button_index: int = 0,
) -> str:
    message = telegram_client.list_sent_messages()[0]
    return message.reply_markup["inline_keyboard"][button_index][0]["callback_data"]


@pytest.mark.parametrize(
    ("source_type", "media_payload", "file_id", "button_index", "expected_page", "expected_path", "expected_db_id"),
    [
        (
            "pdf",
            {
                "document": {
                    "file_id": "ux-pdf",
                    "file_name": "lesson.pdf",
                    "mime_type": "application/pdf",
                }
            },
            "ux-pdf",
            0,
            "notion-parent-canonical",
            "Knowledge/Parent",
            1,
        ),
        (
            "screenshot",
            {
                "photo": [
                    {
                        "file_id": "ux-image",
                        "file_unique_id": "ux-image-unique",
                        "width": 1200,
                        "height": 800,
                        "file_size": 100,
                    }
                ]
            },
            "ux-image",
            1,
            "notion-child-canonical",
            "Knowledge/Parent/Child",
            2,
        ),
    ],
)
def test_upload_then_page_button_creates_target_aware_pending_proposal(
    source_type: str,
    media_payload: dict,
    file_id: str,
    button_index: int,
    expected_page: str,
    expected_path: str,
    expected_db_id: int,
) -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id=file_id,
        file_bytes=b"telegram-file-bytes",
        file_name="lesson.pdf" if source_type == "pdf" else "image.png",
    )
    session_store = InMemoryTelegramSessionStore()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=session_store,
        source_type=source_type,
    )
    try:
        client = TestClient(app)
        upload_payload = {
            "update_id": 9100 if source_type == "pdf" else 9101,
            "message": {
                "message_id": 1,
                "chat": {"id": 555},
                "from": {"id": 777},
                "caption": "/ingest",
                **media_payload,
            },
        }
        upload_response = client.post("/api/telegram/webhook", json=upload_payload)
        assert upload_response.status_code == 200
        upload_result = upload_response.json()
        assert upload_result["command"] == "ingest"
        assert upload_result["change_request_id"] is None
        assert len(telegram_client.list_sent_messages()) == 1
        keyboard = telegram_client.list_sent_messages()[0].reply_markup
        assert keyboard is not None
        assert len(keyboard["inline_keyboard"]) == 2
        callback_data = _page_callback_data(
            telegram_client,
            button_index=button_index,
        )
        assert "notion-parent-canonical" not in callback_data
        assert callback_data.startswith("ll:")

        callback_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9200 if source_type == "pdf" else 9201,
                "callback_query": {
                    "id": "callback-1",
                    "from": {"id": 777},
                    "data": callback_data,
                    "message": {"message_id": 2, "chat": {"id": 555}},
                },
            },
        )
        assert callback_response.status_code == 200
        result = callback_response.json()
        assert result["target_notion_page_id"] == expected_page
        assert result["target_set"] is True
        assert result["change_request_id"] is not None
        assert expected_path in result["reply_text"]
        review_buttons = [
            button["text"]
            for row in telegram_client.list_sent_messages()[-1].reply_markup["inline_keyboard"]
            for button in row
        ]
        assert review_buttons == ["Accept", "Reject", "Change target"]
        assert telegram_client.list_callback_answers() == [
            {"callback_query_id": "callback-1", "text": None}
        ]

        duplicate_callback_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9250 if source_type == "pdf" else 9251,
                "callback_query": {
                    "id": "callback-duplicate",
                    "from": {"id": 777},
                    "data": callback_data,
                    "message": {"message_id": 3, "chat": {"id": 555}},
                },
            },
        )
        assert duplicate_callback_response.status_code == 200
        assert duplicate_callback_response.json()["reply_text"] == (
            "This proposal is already ready for review."
        )
        assert "Proposal ready for review" not in duplicate_callback_response.json()[
            "reply_text"
        ]

        session = session_factory()
        try:
            proposals = session.query(ChangeRequest).all()
            assert len(proposals) == 1
            assert proposals[0].status == "pending"
            assert proposals[0].target_notion_page_id == expected_db_id
            proposal_payload = json.loads(proposals[0].proposal_json)
            assert proposal_payload["target_path"] == f"{expected_path}/AI Supplement Zone"
            assert session.query(SourceDocument).count() == 1
            workflow = session.get(WorkflowRun, result["workflow_run_id"])
            assert workflow is not None
            assert workflow.status == "succeeded"
            assert workflow.failure_reason is None
            workflow_metadata = json.loads(workflow.metadata_json or "{}")
            assert workflow_metadata["business_status"] == "succeeded"
            assert workflow_metadata["callback_ack_status"] == "succeeded"
            assert workflow_metadata["preview_delivery_status"] == "succeeded"
            for latency_key in (
                "download_ms",
                "ocr_ms",
                "llm_ms",
                "persist_ms",
                "preview_delivery_ms",
                "total_business_ms",
            ):
                assert latency_key in workflow_metadata
                assert workflow_metadata[latency_key] >= 0
            ledger = (
                session.query(TelegramUpdateLedger)
                .filter(TelegramUpdateLedger.update_id == (9200 if source_type == "pdf" else 9201))
                .one()
            )
            assert ledger.status == "succeeded"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_hierarchy_picker_browse_is_side_effect_free_and_final_select_runs_once() -> None:
    session_factory = _session_factory()
    _seed_hierarchical_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="hierarchy-pdf",
        file_bytes=b"telegram-file-bytes",
        file_name="lesson.pdf",
    )
    session_store = InMemoryTelegramSessionStore()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=session_store,
        source_type="pdf",
    )

    def callback(update_id: int, callback_id: str, data: str):
        return client.post(
            "/api/telegram/webhook",
            json={
                "update_id": update_id,
                "callback_query": {
                    "id": callback_id,
                    "from": {"id": 777},
                    "data": data,
                    "message": {"message_id": update_id, "chat": {"id": 555}},
                },
            },
        )

    try:
        client = TestClient(app)
        upload = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9300,
                "message": {
                    "message_id": 30,
                    "chat": {"id": 555},
                    "from": {"id": 777},
                    "caption": "/ingest",
                    "document": {
                        "file_id": "hierarchy-pdf",
                        "file_name": "lesson.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )
        assert upload.status_code == 200
        root_markup = telegram_client.list_sent_messages()[-1].reply_markup
        assert [
            row[0]["text"] for row in root_markup["inline_keyboard"]
        ] == ["📁 Parent", "📄 Other root"]

        opened_parent = callback(
            9301,
            "open-parent",
            root_markup["inline_keyboard"][0][0]["callback_data"],
        )
        assert opened_parent.status_code == 200
        assert opened_parent.json()["change_request_id"] is None
        assert "Choose this page or one of its child pages." in opened_parent.json()[
            "reply_text"
        ]
        assert [
            row[0]["text"]
            for row in telegram_client.list_sent_messages()[-1].reply_markup[
                "inline_keyboard"
            ]
        ] == ["✅ Select Parent", "📁 Child", "⬅️ Back", "🏠 Root pages"]

        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            assert session.query(ChangeRequest).count() == 0
        finally:
            session.close()

        parent_markup = telegram_client.list_sent_messages()[-1].reply_markup
        opened_child = callback(
            9302,
            "open-child",
            parent_markup["inline_keyboard"][1][0]["callback_data"],
        )
        assert opened_child.status_code == 200
        child_markup = telegram_client.list_sent_messages()[-1].reply_markup
        assert [
            row[0]["text"] for row in child_markup["inline_keyboard"]
        ] == ["✅ Select Child", "📄 Grandchild", "⬅️ Back", "🏠 Root pages"]

        returned = callback(
            9303,
            "back-to-parent",
            child_markup["inline_keyboard"][2][0]["callback_data"],
        )
        assert returned.status_code == 200
        assert "Parent" in returned.json()["reply_text"]
        assert "Grandchild" not in returned.json()["reply_text"]

        parent_markup = telegram_client.list_sent_messages()[-1].reply_markup
        rooted = callback(
            9304,
            "root-pages",
            parent_markup["inline_keyboard"][3][0]["callback_data"],
        )
        assert rooted.status_code == 200
        assert rooted.json()["change_request_id"] is None
        assert session_store.find_latest_upload(chat_id="555", user_id="777").state == (
            "awaiting_target"
        )

        root_markup = telegram_client.list_sent_messages()[-1].reply_markup
        opened_parent_again = callback(
            9305,
            "open-parent-again",
            root_markup["inline_keyboard"][0][0]["callback_data"],
        )
        assert opened_parent_again.status_code == 200
        parent_markup = telegram_client.list_sent_messages()[-1].reply_markup
        selected = callback(
            9306,
            "select-parent",
            parent_markup["inline_keyboard"][0][0]["callback_data"],
        )
        assert selected.status_code == 200
        assert selected.json()["target_notion_page_id"] == "hierarchy-parent"
        assert selected.json()["target_set"] is True

        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 1
            assert session.query(ChangeRequest).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_callback_ack_timeout_does_not_fail_committed_business_work() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = _CallbackAckTimeoutTelegramClient()
    telegram_client.add_file(
        file_id="ack-timeout-pdf",
        file_bytes=b"telegram-file-bytes",
        file_name="lesson.pdf",
    )
    store = InMemoryTelegramSessionStore()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=store,
        source_type="pdf",
    )
    try:
        client = TestClient(app)
        upload = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9700,
                "message": {
                    "message_id": 70,
                    "chat": {"id": 567},
                    "from": {"id": 787},
                    "caption": "/ingest",
                    "document": {
                        "file_id": "ack-timeout-pdf",
                        "file_name": "lesson.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )
        callback_data = _page_callback_data(telegram_client)
        callback = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9701,
                "callback_query": {
                    "id": "ack-timeout",
                    "from": {"id": 787},
                    "data": callback_data,
                    "message": {"message_id": 71, "chat": {"id": 567}},
                },
            },
        )
        assert upload.status_code == 200
        assert callback.status_code == 200
        result = callback.json()
        assert result["business_status"] == "succeeded"
        assert result["callback_ack_status"] == "failed"
        assert result["preview_delivery_status"] == "succeeded"
        assert telegram_client.callback_ack_attempts == 1

        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 1
            proposal = session.query(ChangeRequest).one()
            assert proposal.status == "pending"
            workflow = session.get(WorkflowRun, result["workflow_run_id"])
            assert workflow is not None
            assert workflow.status == "succeeded"
            assert workflow.failure_reason is None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["callback_ack_failure_reason"] == (
                "TELEGRAM_CALLBACK_ACK_FAILED"
            )
            ledger = (
                session.query(TelegramUpdateLedger)
                .filter(TelegramUpdateLedger.update_id == 9701)
                .one()
            )
            assert ledger.status == "succeeded"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_preview_delivery_failure_preserves_pending_proposal_and_replays_safely() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = _PreviewFailureTelegramClient()
    telegram_client.add_file(
        file_id="preview-failure-pdf",
        file_bytes=b"telegram-file-bytes",
        file_name="lesson.pdf",
    )
    store = InMemoryTelegramSessionStore()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=store,
        source_type="pdf",
    )
    try:
        client = TestClient(app)
        client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9710,
                "message": {
                    "message_id": 72,
                    "chat": {"id": 568},
                    "from": {"id": 788},
                    "caption": "/ingest",
                    "document": {
                        "file_id": "preview-failure-pdf",
                        "file_name": "lesson.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )
        callback_data = _page_callback_data(telegram_client)
        callback_payload = {
            "update_id": 9711,
            "callback_query": {
                "id": "preview-failure",
                "from": {"id": 788},
                "data": callback_data,
                "message": {"message_id": 73, "chat": {"id": 568}},
            },
        }
        first = client.post("/api/telegram/webhook", json=callback_payload)
        assert first.status_code == 502
        detail = first.json()["detail"]
        assert detail["error_code"] == "TELEGRAM_PREVIEW_DELIVERY_FAILED"
        assert detail["failure_reason"] == "TELEGRAM_PREVIEW_DELIVERY_FAILED"
        assert detail["business_status"] == "succeeded"
        assert detail["preview_delivery_status"] == "failed"
        assert telegram_client.preview_failure_attempts == 1
        assert any(
            message.text
            == "Proposal was created, but preview delivery failed. Please wait for recovery; do not upload again."
            for message in telegram_client.list_sent_messages()
        )

        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 1
            proposal = session.query(ChangeRequest).one()
            assert proposal.status == "pending"
            workflow = session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow is not None
            assert workflow.status == "failed"
            assert workflow.failure_reason == "TELEGRAM_PREVIEW_DELIVERY_FAILED"
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["business_status"] == "succeeded"
            assert metadata["preview_delivery_status"] == "failed"
            ledger = (
                session.query(TelegramUpdateLedger)
                .filter(TelegramUpdateLedger.update_id == 9711)
                .one()
            )
            assert ledger.status == "failed"
            failure = json.loads(ledger.failure_json or "{}")
            assert failure["failure_reason"] == "TELEGRAM_PREVIEW_DELIVERY_FAILED"
        finally:
            session.close()

        replay = client.post("/api/telegram/webhook", json=callback_payload)
        assert replay.status_code == 502
        assert replay.json()["detail"]["failure_reason"] == (
            "TELEGRAM_PREVIEW_DELIVERY_FAILED"
        )
        assert telegram_client.preview_failure_attempts == 1
        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 1
            assert session.query(ChangeRequest).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_expired_picker_callback_fails_closed_without_business_rows() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="expired-picker-pdf",
        file_bytes=b"telegram-file-bytes",
        file_name="lesson.pdf",
    )
    store = InMemoryTelegramSessionStore(ttl_seconds=1)
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=store,
        source_type="pdf",
    )
    try:
        client = TestClient(app)
        upload = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9720,
                "message": {
                    "message_id": 74,
                    "chat": {"id": 569},
                    "from": {"id": 789},
                    "caption": "/ingest",
                    "document": {
                        "file_id": "expired-picker-pdf",
                        "file_name": "lesson.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )
        callback_data = _page_callback_data(telegram_client)
        stored = store.find_latest_upload(chat_id="569", user_id="789")
        assert stored is not None
        stored.updated_at -= 2
        response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9721,
                "callback_query": {
                    "id": "expired-picker",
                    "from": {"id": 789},
                    "data": callback_data,
                    "message": {"message_id": 75, "chat": {"id": 569}},
                },
            },
        )
        assert upload.status_code == 200
        assert response.status_code == 410
        assert response.json()["detail"]["failure_reason"] == (
            "UPLOAD_SESSION_EXPIRED"
        )
        assert telegram_client.list_callback_answers() == [
            {
                "callback_query_id": "expired-picker",
                "text": "This upload session expired. Please upload the file again.",
            }
        ]
        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            assert session.query(ChangeRequest).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_redis_upload_picker_callback_restores_session_in_worker_and_creates_pending_proposal(
    monkeypatch,
) -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="redis-worker-pdf",
        file_bytes=b"telegram-file-bytes",
        file_name="lesson.pdf",
    )

    class _FakeRedisWithLocalLock:
        def __init__(self, redis_client) -> None:
            self._redis = redis_client
            self._lock = threading.RLock()

        def lock(self, *_args, **_kwargs):
            return self._lock

        def __getattr__(self, name):
            return getattr(self._redis, name)

    redis_client = fakeredis.FakeRedis()
    session_store = RedisTelegramSessionStore(
        redis_client=_FakeRedisWithLocalLock(redis_client)
    )
    queue_client = RQQueueClient(connection=redis_client)
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=session_store,
        source_type="pdf",
    )
    app.dependency_overrides[get_queue_client] = lambda: queue_client

    registry = ToolRegistry()
    registry.register_tool(TelegramBotTool(telegram_client))
    registry.register_tool(PDFParserTool(_PDFParser()))
    router = ProviderRouter()
    router.register_provider(_ProposalProvider(source_type="pdf"))
    monkeypatch.setattr(telegram_worker, "get_db_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        telegram_worker,
        "get_unit_of_work_factory",
        lambda: (lambda: SqlAlchemyUnitOfWork(session_factory)),
    )
    monkeypatch.setattr(telegram_worker, "get_tool_registry", lambda: registry)
    monkeypatch.setattr(telegram_worker, "get_provider_router", lambda: router)
    monkeypatch.setattr(telegram_worker, "get_embedding_client", lambda: None)
    monkeypatch.setattr(telegram_worker, "get_cost_tracker", lambda: CostTracker())
    monkeypatch.setattr(
        telegram_worker,
        "get_prompt_template_loader",
        lambda: PromptTemplateLoader(),
    )
    monkeypatch.setattr(
        telegram_worker,
        "get_trust_boundary",
        lambda: TrustBoundaryService(),
    )
    monkeypatch.setattr(telegram_worker, "get_queue_client", lambda: queue_client)
    monkeypatch.setattr(
        telegram_worker,
        "get_telegram_session_store",
        lambda: session_store,
    )

    upload_payload = {
        "update_id": 9500,
        "message": {
            "message_id": 50,
            "chat": {"id": 565},
            "from": {"id": 785},
            "caption": "/ingest",
            "document": {
                "file_id": "redis-worker-pdf",
                "file_name": "lesson.pdf",
                "mime_type": "application/pdf",
            },
        },
    }

    try:
        client = TestClient(app)
        upload_response = client.post("/api/telegram/webhook", json=upload_payload)
        assert upload_response.status_code == 202
        assert upload_response.json()["skipped_reason"] == "QUEUED"
        assert Queue(name="telegram", connection=redis_client).count == 1

        SimpleWorker(
            [Queue(name="telegram", connection=redis_client)],
            connection=redis_client,
        ).work(burst=True)
        picker_message = telegram_client.list_sent_messages()[-1]
        picker_token = picker_message.reply_markup["inline_keyboard"][0][0][
            "callback_data"
        ]
        restored = session_store.get_upload(
            session_id="single-update-9500",
            chat_id="565",
            user_id="785",
        )
        assert restored is not None
        assert restored.state == "awaiting_target"
        assert [item.file_id for item in restored.attachments] == [
            "redis-worker-pdf"
        ]

        callback_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9501,
                "callback_query": {
                    "id": "redis-worker-callback",
                    "from": {"id": 785},
                    "data": picker_token,
                    "message": {"message_id": 51, "chat": {"id": 565}},
                },
            },
        )
        assert callback_response.status_code == 202
        assert Queue(name="telegram", connection=redis_client).count == 1

        SimpleWorker(
            [Queue(name="telegram", connection=redis_client)],
            connection=redis_client,
        ).work(burst=True)
        assert len(telegram_client.list_sent_messages()) == 2

        session = session_factory()
        try:
            proposal = session.query(ChangeRequest).one()
            assert proposal.status == "pending"
            assert proposal.target_notion_page_id == 1
            assert session.query(SourceDocument).count() == 1
            ledgers = {
                ledger.update_id: ledger.status
                for ledger in session.query(TelegramUpdateLedger).all()
            }
            assert ledgers == {9500: "succeeded", 9501: "succeeded"}
        finally:
            session.close()

        persisted = session_store.get_upload(
            session_id="single-update-9500",
            chat_id="565",
            user_id="785",
        )
        assert persisted is not None
        assert persisted.state == "proposal_created"
        assert persisted.target_notion_page_id == "notion-parent-canonical"
        assert persisted.target_notion_path == "Knowledge/Parent"

        duplicate_upload_response = client.post(
            "/api/telegram/webhook",
            json=upload_payload,
        )
        assert duplicate_upload_response.status_code == 200
        assert duplicate_upload_response.json()["status"] == "succeeded"
        assert Queue(name="telegram", connection=redis_client).count == 0
        assert len(telegram_client.list_sent_messages()) == 2
    finally:
        app.dependency_overrides.clear()


def test_fabricated_provider_target_is_ignored_without_retry_duplicates() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="invalid-target-pdf",
        file_bytes=b"telegram-file-bytes",
        file_name="lesson.pdf",
    )
    session_store = InMemoryTelegramSessionStore()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=session_store,
        source_type="pdf",
    )
    invalid_router = ProviderRouter()
    invalid_router.register_provider(_InvalidTargetProposalProvider())
    app.dependency_overrides[get_provider_router] = lambda: invalid_router

    try:
        client = TestClient(app)
        upload_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9600,
                "message": {
                    "message_id": 60,
                    "chat": {"id": 566},
                    "from": {"id": 786},
                    "caption": "/ingest",
                    "document": {
                        "file_id": "invalid-target-pdf",
                        "file_name": "lesson.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )
        assert upload_response.status_code == 200
        callback_data = _page_callback_data(telegram_client, button_index=0)

        callback_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9601,
                "callback_query": {
                    "id": "invalid-target-callback",
                    "from": {"id": 786},
                    "data": callback_data,
                    "message": {"message_id": 61, "chat": {"id": 566}},
                },
            },
        )
        assert callback_response.status_code == 200
        result = callback_response.json()
        assert result["change_request_id"] is not None
        assert telegram_client.list_callback_answers() == [
            {"callback_query_id": "invalid-target-callback", "text": None}
        ]

        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 1
            assert session.query(ChangeRequest).count() == 1
            proposal = session.query(ChangeRequest).one()
            proposal_payload = json.loads(proposal.proposal_json)
            assert proposal_payload["source"] == {
                "source_type": "pdf",
                "source_display_name": "lesson.pdf",
            }
            assert proposal_payload["target_path"] == (
                "Knowledge/Parent/AI Supplement Zone"
            )
            succeeded_ledger = (
                session.query(TelegramUpdateLedger)
                .filter(TelegramUpdateLedger.update_id == 9601)
                .one()
            )
            assert succeeded_ledger.status == "succeeded"
        finally:
            session.close()

        duplicate_callback_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9601,
                "callback_query": {
                    "id": "invalid-target-callback-retry",
                    "from": {"id": 786},
                    "data": callback_data,
                    "message": {"message_id": 62, "chat": {"id": 566}},
                },
            },
        )
        assert duplicate_callback_response.status_code == 200
        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 1
            assert session.query(ChangeRequest).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_grounding_failure_metadata_reaches_outer_telegram_workflow() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="grounding-failure-image",
        file_bytes=b"telegram-image-bytes",
        file_name="mysql.png",
    )
    proposal_provider = _GroundingFailureProposalProvider()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=InMemoryTelegramSessionStore(),
        source_type="screenshot",
        ocr_parser=_OCRParser(
            raw_text=(
                "MySQL EXPLAIN 會顯示查詢的執行計畫。"
                "索引可協助查詢條件過濾。"
                "畫面列出 type、key、rows 欄位。"
            )
        ),
        proposal_provider=proposal_provider,
    )

    try:
        client = TestClient(app)
        upload_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9700,
                "message": {
                    "message_id": 70,
                    "chat": {"id": 566},
                    "from": {"id": 786},
                    "caption": "/ingest",
                    "photo": [
                        {
                            "file_id": "grounding-failure-image",
                            "file_unique_id": "grounding-failure-image-unique",
                            "width": 1200,
                            "height": 800,
                            "file_size": 100,
                        }
                    ],
                },
            },
        )
        assert upload_response.status_code == 200
        callback_data = _page_callback_data(telegram_client, button_index=1)

        callback_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9701,
                "callback_query": {
                    "id": "grounding-failure-callback",
                    "from": {"id": 786},
                    "data": callback_data,
                    "message": {"message_id": 71, "chat": {"id": 566}},
                },
            },
        )
        assert callback_response.status_code == 502
        detail = callback_response.json()["detail"]
        assert detail["error_code"] == "LLM_OUTPUT_INVALID"
        assert detail["failure_reason"] == "LLM_OUTPUT_INVALID"
        workflow_id = detail["workflow_run_id"]

        session = session_factory()
        try:
            workflow = session.get(WorkflowRun, workflow_id)
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json or "{}")
            assert metadata["failure_stage"] == "proposal_validation"
            assert metadata["validation_field"] == "summary"
            assert metadata["validator_version"] == "screenshot_grounding_v4"
            assert metadata["validation_granularity"] == (
                "summary_sentence_list_item_v1"
            )
            assert metadata["source_document_id"] == 1
            assert metadata["source_attachment_count"] == 1
            assert metadata["session_retry_available"] is True
            assert metadata["llm_ms"] >= 0
            assert metadata["source_normalized_char_count"] > 0
            assert metadata["candidate_field_char_count"] > 0
            assert metadata["evidence_claim_count"] >= 1
            assert metadata["extracted_claim_count"] >= metadata["matched_claim_count"] >= 1
            assert metadata["unsupported_claim_count"] >= 1
            assert metadata["first_unsupported_claim_index"] is not None
            assert metadata["first_unsupported_reason"] is not None
            assert metadata["failed_field_count"] >= 1
            assert metadata["failed_validation_unit_count"] >= 1
            assert metadata["failed_logical_region_count"] >= 1
            assert metadata["failed_logical_regions"]
            assert metadata["failure_reason_counts"]
            assert metadata["title_anchor_count"] >= 2
            assert metadata["matched_title_anchor_count"] == metadata["title_anchor_count"]
            assert metadata["unmatched_title_anchor_count"] == 0
            assert metadata["numeric_anchor_count"] == 0
            assert metadata["unmatched_numeric_anchor_count"] == 0
            assert metadata["prompt_source_digest"] == metadata["validation_source_digest"]
            assert metadata["source_snapshot_digest"] == metadata["prompt_source_digest"]
            assert len(metadata["source_snapshot_digest"]) == 64
            assert "改善查詢效能" not in workflow.metadata_json
            assert len(proposal_provider.requests) == 1
            prompt_source = proposal_provider.requests[0].messages[1].content
            assert "MySQL EXPLAIN 會顯示查詢的執行計畫。" in prompt_source
            assert "畫面列出 type、key、rows 欄位。" in prompt_source
            assert session.query(ChangeRequest).count() == 0
            assert session.query(SourceDocument).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_caption_command_and_duplicate_update_id_do_not_duplicate_upload_or_preview() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(file_id="caption-pdf", file_bytes=b"pdf", file_name="a.pdf")
    store = InMemoryTelegramSessionStore()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=store,
        source_type="pdf",
    )
    payload = {
        "update_id": 9300,
        "message": {
            "message_id": 3,
            "chat": {"id": 556},
            "from": {"id": 778},
            "caption": "/ingest",
            "document": {
                "file_id": "caption-pdf",
                "file_name": "a.pdf",
                "mime_type": "application/pdf",
            },
        },
    }
    try:
        client = TestClient(app)
        first = client.post("/api/telegram/webhook", json=payload)
        duplicate = client.post("/api/telegram/webhook", json=payload)
        assert first.status_code == duplicate.status_code == 200
        assert first.json() == duplicate.json()
        assert len(telegram_client.list_sent_messages()) == 1
        assert store.find_latest_upload(chat_id="556", user_id="778") is not None
    finally:
        app.dependency_overrides.clear()


def test_change_target_button_updates_pending_target_without_accepting() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    telegram_client.add_file(
        file_id="change-target-pdf",
        file_bytes=b"pdf",
        file_name="lesson.pdf",
    )
    store = InMemoryTelegramSessionStore()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=store,
        source_type="pdf",
    )
    try:
        client = TestClient(app)
        upload = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9320,
                "message": {
                    "message_id": 20,
                    "chat": {"id": 558},
                    "from": {"id": 780},
                    "caption": "/ingest",
                    "document": {
                        "file_id": "change-target-pdf",
                        "file_name": "lesson.pdf",
                        "mime_type": "application/pdf",
                    },
                },
            },
        )
        assert upload.status_code == 200
        picker_token = telegram_client.list_sent_messages()[0].reply_markup[
            "inline_keyboard"
        ][0][0]["callback_data"]
        selected = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9321,
                "callback_query": {
                    "id": "select-parent",
                    "from": {"id": 780},
                    "data": picker_token,
                    "message": {"message_id": 21, "chat": {"id": 558}},
                },
            },
        )
        assert selected.status_code == 200
        review_markup = telegram_client.list_sent_messages()[-1].reply_markup
        change_token = review_markup["inline_keyboard"][1][0]["callback_data"]
        picker = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9322,
                "callback_query": {
                    "id": "change-target",
                    "from": {"id": 780},
                    "data": change_token,
                    "message": {"message_id": 22, "chat": {"id": 558}},
                },
            },
        )
        assert picker.status_code == 200
        assert "Page " not in picker.json()["reply_text"]
        change_picker_markup = telegram_client.list_sent_messages()[-1].reply_markup
        assert all(
            button["text"] not in {"➡️ Next", "⬅️ Previous"}
            for row in change_picker_markup["inline_keyboard"]
            for button in row
        )
        child_token = change_picker_markup[
            "inline_keyboard"
        ][1][0]["callback_data"]
        resolved_change_picker = store.resolve_callback(
            token=child_token[3:], chat_id="558", user_id="780"
        )
        assert resolved_change_picker is not None
        assert resolved_change_picker.action == "select_target"
        assert resolved_change_picker.picker_mode == "change_target"
        changed = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9323,
                "callback_query": {
                    "id": "select-child",
                    "from": {"id": 780},
                    "data": child_token,
                    "message": {"message_id": 23, "chat": {"id": 558}},
                },
            },
        )
        assert changed.status_code == 200
        assert changed.json()["target_notion_page_id"] == "notion-child-canonical"
        assert changed.json()["target_set"] is True
        session = session_factory()
        try:
            proposal = session.query(ChangeRequest).one()
            assert proposal.status == "pending"
            assert proposal.target_notion_page_id == 2
            assert session.query(SourceDocument).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_media_group_store_is_idempotent_and_concurrency_safe() -> None:
    store = InMemoryTelegramSessionStore()
    first = store.upsert_upload(
        session_id="group-1",
        chat_id="1",
        user_id="2",
        media_group_id="album-1",
        attachments=[
            TelegramUploadAttachment(
                kind="photo",
                file_id="a",
                file_unique_id="unique-a",
                message_id=30,
            )
        ],
        command_text="/ingest",
    )
    assert len(first.attachments) == 1
    with ThreadPoolExecutor(max_workers=4) as executor:
        settle_claims = list(
            executor.map(
                lambda _: store.claim_settle(
                    session_id="group-1",
                    chat_id="1",
                    user_id="2",
                ),
                range(4),
            )
        )
    assert settle_claims.count(True) == 1
    store.upsert_upload(
        session_id="group-1",
        chat_id="1",
        user_id="2",
        media_group_id="album-1",
        attachments=[
            TelegramUploadAttachment(
                kind="photo",
                file_id="a-retry",
                file_unique_id="unique-a",
                message_id=30,
            ),
            TelegramUploadAttachment(kind="photo", file_id="b", message_id=10),
        ],
        command_text=None,
    )
    # A later update refreshes the debounce version instead of being ignored
    # merely because the first delayed settle job was already scheduled.
    assert store.claim_settle(session_id="group-1", chat_id="1", user_id="2") is True
    assert store.claim_settled(
        session_id="group-1",
        chat_id="1",
        user_id="2",
        settle_version=999,
    ) is False
    assert store.claim_settled(
        session_id="group-1",
        chat_id="1",
        user_id="2",
        settle_version=1,
    ) is False
    assert store.claim_settled(
        session_id="group-1",
        chat_id="1",
        user_id="2",
        settle_version=2,
    ) is True
    assert store.claim_picker(session_id="group-1", chat_id="1", user_id="2") is True
    assert store.claim_picker(session_id="group-1", chat_id="1", user_id="2") is False
    session = store.get_upload(session_id="group-1", chat_id="1", user_id="2")
    assert session is not None
    assert {item.file_id for item in session.attachments} == {"a", "b"}
    assert [item.file_id for item in session.attachments] == ["b", "a"]
    assert session.state == "awaiting_target"
    assert store.claim_target(
        session_id="group-1",
        chat_id="1",
        user_id="2",
        target_notion_page_id="canonical-page",
        target_notion_path="Knowledge/Page",
    )[0] == "new"
    assert store.claim_target(
        session_id="group-1",
        chat_id="1",
        user_id="2",
        target_notion_page_id="canonical-page",
        target_notion_path="Knowledge/Page",
    )[0] == "in_progress"


def test_three_media_group_updates_use_one_picker_and_one_business_batch(
    monkeypatch,
) -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    for message_id in (30, 10, 20):
        telegram_client.add_file(
            file_id=f"group-image-{message_id}",
            file_bytes=f"image-{message_id}".encode(),
            file_name=f"image-{message_id}.png",
        )

    class _FakeRedisWithLocalLock:
        def __init__(self, redis_client) -> None:
            self._redis = redis_client
            self._lock = threading.RLock()

        def lock(self, *_args, **_kwargs):
            return self._lock

        def __getattr__(self, name):
            return getattr(self._redis, name)

    redis_client = fakeredis.FakeRedis()
    session_store = RedisTelegramSessionStore(
        redis_client=_FakeRedisWithLocalLock(redis_client)
    )
    queue_client = RQQueueClient(connection=redis_client)
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=session_store,
        source_type="screenshot",
    )
    app.dependency_overrides[get_queue_client] = lambda: queue_client

    registry = ToolRegistry()
    registry.register_tool(TelegramBotTool(telegram_client))
    registry.register_tool(ImageOCRTool(_OCRParser()))
    router = ProviderRouter()
    proposal_provider = _ProposalProvider(source_type="screenshot", screenshot_count=3)
    router.register_provider(proposal_provider)
    monkeypatch.setattr(telegram_worker, "get_db_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        telegram_worker,
        "get_unit_of_work_factory",
        lambda: (lambda: SqlAlchemyUnitOfWork(session_factory)),
    )
    monkeypatch.setattr(telegram_worker, "get_tool_registry", lambda: registry)
    monkeypatch.setattr(telegram_worker, "get_provider_router", lambda: router)
    monkeypatch.setattr(telegram_worker, "get_embedding_client", lambda: None)
    monkeypatch.setattr(telegram_worker, "get_cost_tracker", lambda: CostTracker())
    monkeypatch.setattr(
        telegram_worker,
        "get_prompt_template_loader",
        lambda: PromptTemplateLoader(),
    )
    monkeypatch.setattr(
        telegram_worker,
        "get_trust_boundary",
        lambda: TrustBoundaryService(),
    )
    monkeypatch.setattr(telegram_worker, "get_queue_client", lambda: queue_client)
    monkeypatch.setattr(
        telegram_worker,
        "get_telegram_session_store",
        lambda: session_store,
    )

    try:
        client = TestClient(app)
        for update_id, message_id in enumerate((30, 10, 20), start=9600):
            response = client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": update_id,
                    "message": {
                        "message_id": message_id,
                        "chat": {"id": 566},
                        "from": {"id": 786},
                        "caption": "/ingest",
                        "media_group_id": "album-three",
                        "photo": [
                            {
                                "file_id": f"group-image-{message_id}",
                                "file_unique_id": f"group-unique-{message_id}",
                                "width": 1200,
                                "height": 800,
                                "file_size": 100,
                            }
                        ],
                    },
                },
            )
            assert response.status_code == 202

        SimpleWorker(
            [Queue(name="telegram", connection=redis_client)],
            connection=redis_client,
        ).work(burst=True)
        time.sleep(1.1)
        SimpleWorker(
            [Queue(name="telegram", connection=redis_client)],
            connection=redis_client,
        ).work(burst=True, with_scheduler=True)

        picker_messages = [
            message
            for message in telegram_client.list_sent_messages()
            if message.reply_markup is not None
            and message.text.startswith("File received. Choose")
        ]
        assert len(picker_messages) == 1
        # The session id is derived from the media_group_id; inspect it through
        # the user-scoped latest pointer so the UI never needs that id.
        session = session_store.find_latest_upload(chat_id="566", user_id="786")
        assert session is not None
        assert session.state == "awaiting_target"
        assert [item.message_id for item in session.attachments] == [10, 20, 30]
        assert [item.file_id for item in session.attachments] == [
            "group-image-10",
            "group-image-20",
            "group-image-30",
        ]

        picker_data = picker_messages[0].reply_markup["inline_keyboard"][1][0][
            "callback_data"
        ]
        callback_response = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 9700,
                "callback_query": {
                    "id": "group-picker",
                    "from": {"id": 786},
                    "data": picker_data,
                    "message": {"message_id": 31, "chat": {"id": 566}},
                },
            },
        )
        assert callback_response.status_code == 202
        SimpleWorker(
            [Queue(name="telegram", connection=redis_client)],
            connection=redis_client,
        ).work(burst=True)

        session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 1
            source_document = session.query(SourceDocument).one()
            raw_text = source_document.raw_text
            assert raw_text.index("image-10.png") < raw_text.index("image-20.png")
            assert raw_text.index("image-20.png") < raw_text.index("image-30.png")
            assert session.query(ChangeRequest).count() == 1
            assert proposal_provider.calls == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_expired_session_and_cross_user_lookup_fail_closed() -> None:
    store = InMemoryTelegramSessionStore(ttl_seconds=1)
    session = store.upsert_upload(
        session_id="single-1",
        chat_id="chat-a",
        user_id="user-a",
        media_group_id=None,
        attachments=[TelegramUploadAttachment(kind="photo", file_id="a")],
        command_text="/ingest",
    )
    session.updated_at -= 2
    assert store.get_upload(session_id="single-1", chat_id="chat-a", user_id="user-a") is None
    assert store.find_latest_upload(chat_id="chat-a", user_id="user-a") is None
    assert store.get_upload(session_id="single-1", chat_id="chat-b", user_id="user-b") is None


def test_expired_upload_command_returns_clear_error_and_requires_reupload() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    store = InMemoryTelegramSessionStore(ttl_seconds=1)
    session = store.upsert_upload(
        session_id="expired-upload",
        chat_id="557",
        user_id="779",
        media_group_id=None,
        attachments=[TelegramUploadAttachment(kind="pdf", file_id="expired")],
        command_text="/ingest",
    )
    session.updated_at -= 2
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=store,
        source_type="pdf",
    )
    try:
        response = TestClient(app).post(
            "/api/telegram/webhook",
            json={
                "update_id": 9350,
                "message": {
                    "message_id": 5,
                    "chat": {"id": 557},
                    "from": {"id": 779},
                    "text": "/ingest",
                },
            },
        )
        assert response.status_code == 410
        assert response.json()["detail"]["error_code"] == "UPLOAD_SESSION_EXPIRED"
        assert response.json()["detail"]["failure_reason"] == "UPLOAD_SESSION_EXPIRED"
        assert telegram_client.list_sent_messages()[-1].text.endswith(
            "Please upload a PDF or image first."
        )
    finally:
        app.dependency_overrides.clear()


def test_redis_session_and_callback_mapping_are_ttl_and_user_scoped() -> None:
    class _FakeRedisWithLocalLock:
        def __init__(self) -> None:
            self._redis = fakeredis.FakeRedis()
            self._lock = threading.RLock()

        def lock(self, *_args, **_kwargs):
            return self._lock

        def __getattr__(self, name):
            return getattr(self._redis, name)

    redis_client = _FakeRedisWithLocalLock()
    store = RedisTelegramSessionStore(redis_client=redis_client, ttl_seconds=60)
    store.upsert_upload(
        session_id="redis-session",
        chat_id="chat-redis",
        user_id="user-redis",
        media_group_id=None,
        attachments=[TelegramUploadAttachment(kind="pdf", file_id="pdf")],
        command_text="/ingest",
    )
    token = store.create_callback(
        session_id="redis-session",
        chat_id="chat-redis",
        user_id="user-redis",
        action="select_target",
        target_notion_page_id="canonical-external-page-id",
        target_notion_path="Knowledge/Parent",
    )
    assert "canonical-external-page-id" not in token
    assert store.resolve_callback(
        token=token,
        chat_id="chat-redis",
        user_id="user-redis",
    ).target_notion_page_id == "canonical-external-page-id"
    assert store.resolve_callback(
        token=token,
        chat_id="other-chat",
        user_id="other-user",
    ) is None
    resolved = store.resolve_callback(
        token=token,
        chat_id="chat-redis",
        user_id="user-redis",
    )
    assert resolved.callback_kind == "picker"
    redis_client.setex(
        "learnloop:telegram:callback:chat-redis:user-redis:legacy-review-token",
        60,
        json.dumps(
            {
                "session_id": "proposal-88",
                "action": "accept",
                "change_request_id": 88,
            }
        ),
    )
    legacy = store.resolve_callback(
        token="legacy-review-token",
        chat_id="chat-redis",
        user_id="user-redis",
    )
    assert legacy is not None
    assert legacy.callback_kind == "review"
    redis_client.setex(
        "learnloop:telegram:callback:chat-redis:user-redis:legacy-picker-token",
        60,
        json.dumps(
            {
                "session_id": "proposal-88",
                "action": "change_target_select",
                "change_request_id": 88,
                "target_notion_page_id": "canonical-external-page-id",
            }
        ),
    )
    legacy_picker = store.resolve_callback(
        token="legacy-picker-token",
        chat_id="chat-redis",
        user_id="user-redis",
    )
    assert legacy_picker is not None
    assert legacy_picker.callback_kind == "picker"
    assert legacy_picker.target_notion_page_id == "canonical-external-page-id"
    assert redis_client.ttl("learnloop:telegram:upload:chat-redis:user-redis:redis-session") > 0


def test_invalid_callback_is_rejected_without_creating_proposal() -> None:
    session_factory = _session_factory()
    _seed_pages(session_factory)
    telegram_client = InMemoryTelegramBotClient()
    store = InMemoryTelegramSessionStore()
    _configure_app(
        session_factory=session_factory,
        telegram_client=telegram_client,
        session_store=store,
        source_type="pdf",
    )
    try:
        response = TestClient(app).post(
            "/api/telegram/webhook",
            json={
                "update_id": 9400,
                "callback_query": {
                    "id": "invalid-callback",
                    "from": {"id": 1},
                    "data": "ll:not-a-real-session-token",
                    "message": {"message_id": 4, "chat": {"id": 2}},
                },
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error_code"] == "INVALID_CALLBACK"
        assert response.json()["detail"]["failure_reason"] == "INVALID_CALLBACK"
        session = session_factory()
        try:
            assert session.query(ChangeRequest).count() == 0
            assert telegram_client.list_sent_messages() == []
            assert telegram_client.list_callback_answers() == [
                {
                    "callback_query_id": "invalid-callback",
                    "text": "This button is invalid or expired. Please upload the file again.",
                }
            ]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()
