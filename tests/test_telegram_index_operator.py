from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from src.app.dependencies import (
    get_embedding_client,
    get_telegram_index_session_store,
    get_telegram_session_store,
    get_tool_registry,
)
from src.app.main import app
from src.db.base import Base
from src.db.models import (
    KnowledgeChunk,
    NotionBlock,
    NotionPage,
    TelegramUpdateLedger,
    WorkflowRun,
)
from src.db.session import get_db_session, get_db_session_factory, get_unit_of_work_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    get_openai_embedding_capabilities,
)
from src.services import (
    InMemoryTelegramIndexSessionStore,
    InMemoryTelegramSessionStore,
)
from src.tools import (
    InMemoryNotionReaderClient,
    InMemoryTelegramBotClient,
    NotionBlockNode,
    NotionPageTree,
    NotionReaderTool,
    TelegramBotTool,
    ToolRegistry,
)


class _Embedding(EmbeddingClient):
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


def _session_factory():
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
            TelegramUpdateLedger.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _configure(
    *,
    session_factory,
    telegram_client: InMemoryTelegramBotClient,
    pages: dict[str, NotionPageTree],
    session_store: InMemoryTelegramSessionStore,
    index_store: InMemoryTelegramIndexSessionStore,
) -> None:
    def db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def tool_registry_override() -> ToolRegistry:
        registry = ToolRegistry()
        registry.register_tool(TelegramBotTool(telegram_client))
        registry.register_tool(NotionReaderTool(InMemoryNotionReaderClient(pages)))
        return registry

    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_unit_of_work_factory] = lambda: (
        lambda: SqlAlchemyUnitOfWork(session_factory)
    )
    app.dependency_overrides[get_tool_registry] = tool_registry_override
    app.dependency_overrides[get_embedding_client] = _Embedding
    app.dependency_overrides[get_telegram_session_store] = lambda: session_store
    app.dependency_overrides[get_telegram_index_session_store] = lambda: index_store


def _page() -> NotionPageTree:
    return NotionPageTree(
        page_id="full-index-page",
        title="Full Index Page",
        notion_path="Knowledge/Full Index Page",
        blocks=[
            NotionBlockNode(
                block_id="full-index-block",
                block_type="paragraph",
                content_text="Full index content",
                block_path="Knowledge/Full Index Page/Content",
            )
        ],
    )


def test_index_full_requires_confirmation_and_status_is_read_only() -> None:
    session_factory = _session_factory()
    telegram_client = InMemoryTelegramBotClient()
    session_store = InMemoryTelegramSessionStore()
    index_store = InMemoryTelegramIndexSessionStore()
    _configure(
        session_factory=session_factory,
        telegram_client=telegram_client,
        pages={"full-index-page": _page()},
        session_store=session_store,
        index_store=index_store,
    )

    try:
        client = TestClient(app)
        warning = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 6101,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 123},
                    "from": {"id": 456},
                    "text": "/index-full",
                },
            },
        )
        assert warning.status_code == 200
        warning_payload = warning.json()
        assert warning_payload["index_status"] == "warning"
        assert warning_payload["index_workflow_run_id"] is None
        assert "unknown" in warning_payload["reply_text"]
        assert "full-index-page" not in warning_payload["reply_text"]
        keyboard = telegram_client.list_sent_messages()[0].reply_markup
        confirm_callback = keyboard["inline_keyboard"][0][0]["callback_data"]
        assert confirm_callback.startswith("ll:")
        assert "full-index-page" not in confirm_callback

        confirmed = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 6102,
                "callback_query": {
                    "id": "index-confirm",
                    "from": {"id": 456},
                    "data": confirm_callback,
                    "message": {"message_id": 2, "chat": {"id": 123}},
                },
            },
        )
        assert confirmed.status_code == 200
        confirmed_payload = confirmed.json()
        assert confirmed_payload["index_status"] == "succeeded"
        assert confirmed_payload["index_discovered_page_count"] == 1
        assert confirmed_payload["index_processed_page_count"] == 1
        workflow_run_id = confirmed_payload["index_workflow_run_id"]
        assert workflow_run_id is not None

        status = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 6103,
                "message": {
                    "message_id": 3,
                    "chat": {"id": 123},
                    "from": {"id": 456},
                    "text": f"/index-status {workflow_run_id}",
                },
            },
        )
        assert status.status_code == 200
        status_payload = status.json()
        assert status_payload["index_status"] == "succeeded"
        assert f"workflow #{workflow_run_id}" in status_payload["reply_text"]
        assert "Full Index Page" not in status_payload["reply_text"]
        assert len(telegram_client.list_sent_messages()) == 3

        duplicate = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 6104,
                "callback_query": {
                    "id": "index-confirm-duplicate",
                    "from": {"id": 456},
                    "data": confirm_callback,
                    "message": {"message_id": 4, "chat": {"id": 123}},
                },
            },
        )
        assert duplicate.status_code == 400
        assert duplicate.json()["detail"]["error_code"] == "INVALID_CALLBACK"
        session = session_factory()
        try:
            assert session.query(WorkflowRun).filter_by(workflow_type="indexing").count() == 1
            assert session.query(NotionPage).filter_by(notion_page_id="full-index-page").count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_index_full_cancel_does_not_start_index_workflow() -> None:
    session_factory = _session_factory()
    telegram_client = InMemoryTelegramBotClient()
    session_store = InMemoryTelegramSessionStore()
    index_store = InMemoryTelegramIndexSessionStore()
    _configure(
        session_factory=session_factory,
        telegram_client=telegram_client,
        pages={"full-index-page": _page()},
        session_store=session_store,
        index_store=index_store,
    )

    try:
        client = TestClient(app)
        warning = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 6201,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 123},
                    "from": {"id": 456},
                    "text": "/index-full",
                },
            },
        )
        cancel_callback = telegram_client.list_sent_messages()[0].reply_markup[
            "inline_keyboard"
        ][1][0]["callback_data"]
        cancelled = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 6202,
                "callback_query": {
                    "id": "index-cancel",
                    "from": {"id": 456},
                    "data": cancel_callback,
                    "message": {"message_id": 2, "chat": {"id": 123}},
                },
            },
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["index_status"] == "cancelled"
        session = session_factory()
        try:
            assert session.query(WorkflowRun).filter_by(workflow_type="indexing").count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()
