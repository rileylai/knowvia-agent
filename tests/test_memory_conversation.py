from __future__ import annotations

import json
from typing import List

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import (
    get_current_owner_id,
    get_embedding_client,
    get_provider_router,
)
from src.app.main import app
from src.db.base import Base
from src.db.models import (
    ConversationMessage,
    ConversationSession,
    KnowledgeChunk,
    LongTermMemory,
    NotionBlock,
    NotionPage,
    SourceDocument,
    WorkflowRun,
)
from src.db.session import get_db_session, get_db_session_factory
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
)


class InsufficientProvider(LLMProvider):
    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            provider=self.name,
            model="gpt-4o-mini",
            output_text="I do not have enough information in production notes to answer safely.",
            token_input=10,
            token_output=10,
        )


class FakeEmbeddingClient(EmbeddingClient):
    @property
    def name(self) -> str:
        return "fake"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            provider=self.name,
            model="fake-model",
            embeddings=[[1.0] + [0.0] * 1535 for _ in request.inputs],
        )


class RelevanceEmbeddingClient(EmbeddingClient):
    @property
    def name(self) -> str:
        return "relevance-fake"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            provider=self.name,
            model="relevance-fake-model",
            embeddings=[self._vector(value) for value in request.inputs],
        )

    def _vector(self, value: str) -> List[float]:
        normalized = value.casefold()
        vector = [0.0] * 1536
        if "名字" in normalized or "name" in normalized:
            vector[0] = 1.0
        elif "職業" in normalized or "occupation" in normalized:
            vector[1] = 1.0
        elif "興趣" in normalized or "interest" in normalized:
            vector[2] = 1.0
        elif "哪些事情" in normalized or "remember about me" in normalized:
            vector[0] = 1.0
            vector[1] = 1.0
        else:
            vector[3] = 1.0
        return vector


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
            ConversationSession.__table__,
            ConversationMessage.__table__,
            LongTermMemory.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _db_override(session_factory):
    def override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    return override


def _seed_enterprise_chunk(session_factory, *, owner_id: str) -> None:
    session = session_factory()
    try:
        session.add(
            SourceDocument(
                id=1,
                source_type="pdf",
                source_display_name="production-notes.pdf",
                content_hash="memory-recall-enterprise-source",
                raw_text="我的職業？ enterprise production note",
                owner_scope=owner_id,
                status="indexed",
            )
        )
        session.flush()
        session.add(
            KnowledgeChunk(
                id=1,
                source_document_id=1,
                chunk_index=0,
                chunk_text="我的職業？ enterprise production note",
                notion_path="Production/Notes",
                embedding_text=json.dumps([1.0] + [0.0] * 1535),
                source_kind="pdf",
                source_display_name="production-notes.pdf",
                locator="page 1",
                owner_scope=owner_id,
                eligibility_status="eligible",
            )
        )
        session.commit()
    finally:
        session.close()


def test_explicit_save_cross_session_recall_and_authority_boundary() -> None:
    session_factory = _build_session_factory()
    app.dependency_overrides[get_db_session] = _db_override(session_factory)
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    app.dependency_overrides[get_provider_router] = lambda: ProviderRouter()
    app.dependency_overrides[get_current_owner_id] = lambda: "owner-a"

    try:
        client = TestClient(app)
        first_session = client.post("/api/conversations").json()["id"]
        saved = client.post(
            f"/api/conversations/{first_session}/messages",
            json={"query": "記住，我們 production 最後決定使用 pgvector。"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["memory_status"] == "saved"
        assert saved.json()["messages"][-1]["content"] == "已儲存記憶"

        second_session = client.post("/api/conversations").json()["id"]
        recalled = client.post(
            f"/api/conversations/{second_session}/messages",
            json={"query": "我們 production 最後決定用什麼？"},
        )
        assert recalled.status_code == 200
        payload = recalled.json()
        assert payload["answer"] == "我們 production 最後決定使用 pgvector。"
        assert payload["used_saved_memory"] is True
        assert payload["citations"] == []
        assert payload["messages"][-1]["used_saved_memory"] is True

        restored = client.get(f"/api/conversations/{second_session}")
        assert restored.json()["messages"][-1]["used_saved_memory"] is True

        enterprise_question = client.post(
            f"/api/conversations/{second_session}/messages",
            json={"query": "What database does production use?"},
        )
        assert enterprise_question.status_code == 200
        enterprise_payload = enterprise_question.json()
        assert enterprise_payload["insufficient_info"] is True
        assert enterprise_payload["citations"] == []
        assert enterprise_payload["used_saved_memory"] is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("save_query", "recall_query", "expected_answer"),
    (
        ("記住我的名字是Riley", "我的名字是？", "我的名字是Riley"),
        ("記住我的名字是Riley", "你記得我的名字嗎？", "我的名字是Riley"),
        ("Remember that my name is Riley.", "What is my name?", "my name is Riley."),
        (
            "記住我的職業是軟體工程師",
            "我的職業？",
            "我的職業是軟體工程師",
        ),
        (
            "記住我的職業是軟體工程師",
            "你記得我的職業嗎？",
            "我的職業是軟體工程師",
        ),
        (
            "Remember that my occupation is software engineer.",
            "What is my occupation?",
            "my occupation is software engineer.",
        ),
        (
            "記住，我們 production 最後決定使用 pgvector。",
            "我們 production 最後決定用什麼？",
            "我們 production 最後決定使用 pgvector。",
        ),
        (
            "Remember that we decided to use pgvector in production.",
            "What did we decide about production?",
            "we decided to use pgvector in production.",
        ),
        ("記住，我偏好 concise answers。", "我偏好什麼？", "我偏好 concise answers。"),
    ),
)
def test_direct_saved_memory_recall_queries(
    save_query: str,
    recall_query: str,
    expected_answer: str,
) -> None:
    session_factory = _build_session_factory()
    app.dependency_overrides[get_db_session] = _db_override(session_factory)
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    app.dependency_overrides[get_provider_router] = lambda: ProviderRouter()
    app.dependency_overrides[get_current_owner_id] = lambda: "owner-a"

    try:
        client = TestClient(app)
        first_session = client.post("/api/conversations").json()["id"]
        saved = client.post(
            f"/api/conversations/{first_session}/messages",
            json={"query": save_query},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["memory_status"] == "saved"

        second_session = client.post("/api/conversations").json()["id"]
        recalled = client.post(
            f"/api/conversations/{second_session}/messages",
            json={"query": recall_query},
        )
        assert recalled.status_code == 200, recalled.text
        payload = recalled.json()
        assert payload["answer"] == expected_answer
        assert payload["used_saved_memory"] is True
        assert payload["citations"] == []
        assert payload["messages"][-1]["used_saved_memory"] is True
    finally:
        app.dependency_overrides.clear()


def test_saved_memory_wins_when_enterprise_retrieval_has_irrelevant_evidence() -> None:
    session_factory = _build_session_factory()
    _seed_enterprise_chunk(session_factory, owner_id="owner-a")
    provider = InsufficientProvider()
    provider_router = ProviderRouter()
    provider_router.register_provider(provider)
    app.dependency_overrides[get_db_session] = _db_override(session_factory)
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    app.dependency_overrides[get_provider_router] = lambda: provider_router
    app.dependency_overrides[get_current_owner_id] = lambda: "owner-a"

    try:
        client = TestClient(app)
        first_session = client.post("/api/conversations").json()["id"]
        saved = client.post(
            f"/api/conversations/{first_session}/messages",
            json={"query": "記住我的職業是軟體工程師"},
        )
        assert saved.status_code == 200, saved.text

        second_session = client.post("/api/conversations").json()["id"]
        recalled = client.post(
            f"/api/conversations/{second_session}/messages",
            json={"query": "我的職業？"},
        )
        assert recalled.status_code == 200, recalled.text
        payload = recalled.json()
        assert payload["retrieved_chunk_count"] == 0
        assert payload["answer"] == "我的職業是軟體工程師"
        assert payload["used_saved_memory"] is True
        assert payload["citations"] == []
        assert provider.requests == []
    finally:
        app.dependency_overrides.clear()


def test_memory_recall_selects_relevant_memory_and_supports_bounded_broad_recall() -> None:
    session_factory = _build_session_factory()
    app.dependency_overrides[get_db_session] = _db_override(session_factory)
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_embedding_client] = lambda: RelevanceEmbeddingClient()
    app.dependency_overrides[get_provider_router] = lambda: ProviderRouter()
    app.dependency_overrides[get_current_owner_id] = lambda: "owner-a"

    try:
        client = TestClient(app)
        first_session = client.post("/api/conversations").json()["id"]
        for query in ("記住我的名字是Riley", "記住我的職業是軟體工程師"):
            saved = client.post(
                f"/api/conversations/{first_session}/messages",
                json={"query": query},
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["memory_status"] == "saved"

        second_session = client.post("/api/conversations").json()["id"]
        name_recall = client.post(
            f"/api/conversations/{second_session}/messages",
            json={"query": "What is my name?"},
        ).json()
        occupation_recall = client.post(
            f"/api/conversations/{second_session}/messages",
            json={"query": "你記得我的職業嗎？"},
        ).json()
        interest_recall = client.post(
            f"/api/conversations/{second_session}/messages",
            json={"query": "我的興趣？"},
        ).json()
        broad_recall = client.post(
            f"/api/conversations/{second_session}/messages",
            json={"query": "你記得我哪些事情？"},
        ).json()

        assert name_recall["answer"] == "我的名字是Riley"
        assert name_recall["used_saved_memory"] is True
        assert name_recall["citations"] == []
        assert occupation_recall["answer"] == "我的職業是軟體工程師"
        assert occupation_recall["used_saved_memory"] is True
        assert occupation_recall["citations"] == []
        assert interest_recall["insufficient_info"] is True
        assert interest_recall["used_saved_memory"] is False
        assert interest_recall["citations"] == []
        assert broad_recall["answer"] == "- 我的名字是Riley\n- 我的職業是軟體工程師"
        assert broad_recall["used_saved_memory"] is True
        assert broad_recall["citations"] == []
    finally:
        app.dependency_overrides.clear()


def test_plain_statement_does_not_create_memory() -> None:
    session_factory = _build_session_factory()
    app.dependency_overrides[get_db_session] = _db_override(session_factory)
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_embedding_client] = lambda: FakeEmbeddingClient()
    app.dependency_overrides[get_provider_router] = lambda: ProviderRouter()
    app.dependency_overrides[get_current_owner_id] = lambda: "owner-a"

    try:
        client = TestClient(app)
        session_id = client.post("/api/conversations").json()["id"]
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我們 production 使用 pgvector。"},
        )
        assert response.status_code == 200

        session = session_factory()
        try:
            assert session.query(LongTermMemory).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()
