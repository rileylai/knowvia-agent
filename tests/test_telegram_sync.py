from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from src.app.dependencies import (
    get_embedding_client,
    get_telegram_session_store,
    get_telegram_sync_session_store,
    get_tool_registry,
)
from src.app.main import app
from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, TelegramUpdateLedger, WorkflowRun
from src.db.session import get_db_session, get_db_session_factory, get_unit_of_work_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    get_openai_embedding_capabilities,
)
from src.services import InMemoryTelegramSessionStore, InMemoryTelegramSyncSessionStore
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


def _tree(page_id: str, title: str, block_id: str, text: str, path: str, parent: str | None = None):
    return NotionPageTree(
        page_id=page_id,
        title=title,
        notion_path=path,
        parent_notion_page_id=parent,
        blocks=[
            NotionBlockNode(
                block_id=block_id,
                block_type="paragraph",
                content_text=text,
                block_path=f"{path}/{text}",
            )
        ],
    )


def test_sync_discovers_live_hierarchy_and_reindexes_selected_page() -> None:
    session_factory = _session_factory()
    telegram_client = InMemoryTelegramBotClient()
    pages = {
        "live-root": _tree("live-root", "Root", "root-block", "Root notes", "Knowledge/Root"),
        "live-child": _tree(
            "live-child",
            "Child",
            "child-block",
            "Child notes",
            "Knowledge/Root/Child",
            parent="live-root",
        ),
    }
    session_store = InMemoryTelegramSessionStore()
    sync_store = InMemoryTelegramSyncSessionStore()

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
    app.dependency_overrides[get_telegram_sync_session_store] = lambda: sync_store

    try:
        client = TestClient(app)
        start = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 5001,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 123},
                    "from": {"id": 456},
                    "text": "/sync",
                },
            },
        )
        assert start.status_code == 200
        start_payload = start.json()
        assert start_payload["sync_status"] == "selecting"
        assert start_payload["sync_discovered_page_count"] == 2
        assert "live-root" not in start_payload["reply_text"]
        assert "Knowledge/Root/Child" in start_payload["reply_text"]
        assert all(
            "live-" not in button["callback_data"]
            for row in telegram_client.list_sent_messages()[0].reply_markup["inline_keyboard"]
            for button in row
        )

        child_callback = telegram_client.list_sent_messages()[0].reply_markup[
            "inline_keyboard"
        ][1][0]["callback_data"]
        selected = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 5002,
                "callback_query": {
                    "id": "sync-toggle",
                    "from": {"id": 456},
                    "data": child_callback,
                    "message": {"message_id": 2, "chat": {"id": 123}},
                },
            },
        )
        assert selected.status_code == 200
        assert selected.json()["sync_selected_page_count"] == 1

        confirm_callback = telegram_client.list_sent_messages()[-1].reply_markup[
            "inline_keyboard"
        ][2][0]["callback_data"]
        confirmed = client.post(
            "/api/telegram/webhook",
            json={
                "update_id": 5003,
                "callback_query": {
                    "id": "sync-confirm",
                    "from": {"id": 456},
                    "data": confirm_callback,
                    "message": {"message_id": 3, "chat": {"id": 123}},
                },
            },
        )
        assert confirmed.status_code == 200
        confirmed_payload = confirmed.json()
        assert confirmed_payload["sync_status"] == "succeeded"
        assert confirmed_payload["sync_selected_page_count"] == 1
        assert confirmed_payload["sync_succeeded_page_count"] == 1
        assert "no Notion content was written" in confirmed_payload["reply_text"]

        session = session_factory()
        try:
            indexed_page = session.query(NotionPage).filter_by(notion_page_id="live-child").one()
            assert indexed_page.notion_path == "Knowledge/Root/Child"
            assert session.query(KnowledgeChunk).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_sync_callback_claim_is_one_shot_and_user_scoped() -> None:
    store = InMemoryTelegramSessionStore()
    token = store.create_callback(
        session_id="sync-1",
        chat_id="chat-1",
        user_id="user-1",
        action="sync_confirm",
        callback_kind="operator",
    )
    assert store.claim_callback(token=token, chat_id="chat-1", user_id="user-2") is False
    assert store.claim_callback(token=token, chat_id="chat-1", user_id="user-1") is True
    assert store.claim_callback(token=token, chat_id="chat-1", user_id="user-1") is False
