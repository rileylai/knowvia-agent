from __future__ import annotations

import json
from typing import Optional

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
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
    NotionBlock,
    NotionPage,
    SourceDocument,
    WorkflowRun,
)
from src.db.session import get_db_session, get_db_session_factory
from src.providers import LLMProvider, LLMRequest, LLMResponse, ProviderRouter


class CapturingProvider(LLMProvider):
    def __init__(self, outputs: Optional[list[str]] = None) -> None:
        self.requests: list[LLMRequest] = []
        self.outputs = outputs or ["Grounded answer"]

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        output = self.outputs[min(len(self.requests) - 1, len(self.outputs) - 1)]
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=output,
            token_input=10,
            token_output=5,
        )


class FailingProvider(CapturingProvider):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        raise RuntimeError("provider failure")


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            ConversationSession.__table__,
            ConversationMessage.__table__,
            NotionPage.__table__,
            NotionBlock.__table__,
            SourceDocument.__table__,
            KnowledgeChunk.__table__,
            WorkflowRun.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _override_database(session_factory) -> None:
    def db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory


def _override_provider(provider: LLMProvider) -> None:
    router = ProviderRouter()
    router.register_provider(provider)
    app.dependency_overrides[get_provider_router] = lambda: router
    app.dependency_overrides[get_embedding_client] = lambda: None


def _seed_knowledge(session_factory) -> None:
    session: Session = session_factory()
    try:
        source = SourceDocument(
            id=1,
            source_type="pdf",
            source_display_name="agent-patterns.pdf",
            raw_text="Agentic AI design patterns for sequential workflow.",
            content_hash="conversation-test-source",
            owner_scope="local",
            status="indexed",
        )
        session.add(source)
        session.flush()
        session.add(
            KnowledgeChunk(
                id=1,
                source_document_id=source.id,
                chunk_index=0,
                chunk_text="Sequential workflow is supported by a bounded agent pattern.",
                source_kind="pdf",
                source_display_name="agent-patterns.pdf",
                locator="page 1",
                owner_scope="local",
                eligibility_status="eligible",
            )
        )
        session.commit()
    finally:
        session.close()


def _create_conversation(client: TestClient) -> int:
    response = client.post("/api/conversations")
    assert response.status_code == 201
    return response.json()["id"]


def test_conversation_crud_is_owner_scoped() -> None:
    session_factory = _build_session_factory()
    _override_database(session_factory)
    app.dependency_overrides[get_current_owner_id] = lambda: "owner-a"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)

        listed = client.get("/api/conversations")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [session_id]

        detail = client.get(f"/api/conversations/{session_id}")
        assert detail.status_code == 200
        assert detail.json()["messages"] == []

        app.dependency_overrides[get_current_owner_id] = lambda: "owner-b"
        assert client.get("/api/conversations").json() == []
        assert client.get(f"/api/conversations/{session_id}").status_code == 404
        assert client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "private follow-up"},
        ).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_conversation_message_persists_sequence_and_same_session_context() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = CapturingProvider(outputs=["First grounded answer", "Second grounded answer"])
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)

        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What agent design patterns support sequential workflow?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "Which sequential workflow pattern should I choose?"},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        payload = second.json()
        assert [message["role"] for message in payload["messages"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert [message["sequence_number"] for message in payload["messages"]] == [
            1,
            2,
            3,
            4,
        ]
        assert payload["title"] == "What agent design patterns support sequential wo"
        assert "First grounded answer" in provider.requests[1].messages[-1].content
        assert "Which sequential workflow pattern should I choose?" in provider.requests[1].messages[-1].content
        assert payload["citations"]
    finally:
        app.dependency_overrides.clear()


def test_provider_failure_keeps_user_message_without_fake_assistant() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = FailingProvider()
    _override_database(session_factory)
    _override_provider(provider)

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What sequential workflow pattern failed?"},
        )

        assert response.status_code == 500
        assert response.json()["detail"]["error_code"] == "QA_WORKFLOW_FAILED"

        session = session_factory()
        try:
            messages = (
                session.query(ConversationMessage)
                .order_by(ConversationMessage.sequence_number)
                .all()
            )
            assert [(message.role, message.content) for message in messages] == [
                ("user", "What sequential workflow pattern failed?")
            ]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_insufficient_info_persists_canonical_assistant_message_without_citations() -> None:
    session_factory = _build_session_factory()
    provider = CapturingProvider()
    _override_database(session_factory)
    _override_provider(provider)

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "No indexed evidence matches this question"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["insufficient_info"] is True
        assert payload["citations"] == []
        assert len(payload["messages"]) == 2
        assert payload["messages"][1]["role"] == "assistant"
        assert payload["messages"][1]["content"] == (
            "I do not have enough information in production notes to answer safely."
        )
        assert payload["messages"][1]["citations"] == []
    finally:
        app.dependency_overrides.clear()


def test_same_session_conversational_recall_uses_history_without_knowledge() -> None:
    session_factory = _build_session_factory()
    provider = CapturingProvider(outputs=['You said "hi".'])
    _override_database(session_factory)
    _override_provider(provider)

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)

        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "hi"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What did I say?"},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        payload = second.json()
        assert payload["answer"] == 'You said "hi".'
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert payload["messages"][1]["citations"] == []
        assert len(provider.requests) == 1
        assert "hi" in provider.requests[0].messages[-1].content
    finally:
        app.dependency_overrides.clear()


def test_same_session_recall_remembers_previous_assistant_answer_without_citations() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = CapturingProvider(
        outputs=[
            "The suitable pattern is the multi-agent sequential pattern.",
            "I chose the multi-agent sequential pattern.",
        ]
    )
    _override_database(session_factory)
    _override_provider(provider)

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)

        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What agent design patterns support sequential workflow?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "Which pattern did you choose in your previous answer?"},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        payload = second.json()
        assert "multi-agent sequential pattern" in payload["answer"]
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert len(provider.requests) == 2
        assert "The suitable pattern is the multi-agent sequential pattern." in (
            provider.requests[1].messages[-1].content
        )
    finally:
        app.dependency_overrides.clear()


def test_same_session_recall_explains_previous_recommendation_without_citations() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = CapturingProvider(
        outputs=[
            "I recommended the multi-agent sequential pattern because each approval step must run in order.",
            "I recommended it because each approval step must run in order.",
        ]
    )
    _override_database(session_factory)
    _override_provider(provider)

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)

        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What agent design patterns support sequential workflow?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "so why you recommended this pattern"},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        payload = second.json()
        assert "each approval step must run in order" in payload["answer"]
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert len(provider.requests) == 2
        assert provider.requests[1].metadata["prompt_id"] == "conversation_recall"
        assert "not enterprise evidence" in provider.requests[1].messages[0].content
    finally:
        app.dependency_overrides.clear()


def test_conversational_recall_with_no_history_does_not_call_provider() -> None:
    session_factory = _build_session_factory()
    provider = CapturingProvider(outputs=["unexpected provider answer"])
    _override_database(session_factory)
    _override_provider(provider)

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What did I say?"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == (
            "There are no earlier user messages in this conversation."
        )
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert provider.requests == []
    finally:
        app.dependency_overrides.clear()


def test_new_session_conversational_recall_cannot_see_another_session_history() -> None:
    session_factory = _build_session_factory()
    provider = CapturingProvider()
    _override_database(session_factory)
    _override_provider(provider)

    try:
        client = TestClient(app)
        first_session_id = _create_conversation(client)
        second_session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{first_session_id}/messages",
            json={"query": "hi"},
        )
        second = client.post(
            f"/api/conversations/{second_session_id}/messages",
            json={"query": "What did I say?"},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        payload = second.json()
        assert payload["answer"] == (
            "There are no earlier user messages in this conversation."
        )
        assert "earlier user messages" in payload["answer"]
        assert provider.requests == []
    finally:
        app.dependency_overrides.clear()


def test_previous_assistant_claim_does_not_become_enterprise_evidence() -> None:
    session_factory = _build_session_factory()
    _override_database(session_factory)
    _override_provider(CapturingProvider(outputs=["should not be called"]))

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        session = session_factory()
        try:
            session.add(
                ConversationMessage(
                    id=1,
                    session_id=session_id,
                    role="assistant",
                    content="Production uses MySQL.",
                    sequence_number=1,
                )
            )
            session.commit()
        finally:
            session.close()

        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What database does production use?"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["insufficient_info"] is True
        assert payload["citations"] == []
        assert payload["answer"] == (
            "I do not have enough information in production notes to answer safely."
        )
    finally:
        app.dependency_overrides.clear()
