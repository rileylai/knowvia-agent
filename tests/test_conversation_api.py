from __future__ import annotations

import asyncio
import json
from typing import Optional

import pytest
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
    LongTermMemory,
    NotionBlock,
    NotionPage,
    SourceDocument,
    WorkflowRun,
)
from src.db.session import get_db_session, get_db_session_factory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    ProviderRouter,
)
from src.services.memory import MemoryService


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


class ToolCallingProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="call-1",
                        name="search_knowledge",
                        arguments={"query": "sequential workflow"},
                    )
                ],
            )
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text="The indexed note confirms the bounded agent pattern.",
        )


class ExplicitSaveProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self, *, save: bool = True) -> None:
        self.requests: list[LLMRequest] = []
        self.save = save

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1 and self.save:
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="save-call-1",
                        name="save_memory",
                        arguments={
                            "memory_type": "preference",
                            "content": "I prefer all API responses to use snake_case.",
                        },
                    )
                ],
            )
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text="Memory saved",
        )


class TransformToolCallingProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self, *, initial_answer: Optional[str] = None) -> None:
        self.requests: list[LLMRequest] = []
        self.initial_answer = initial_answer

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="knowledge-call-1",
                        name="search_knowledge",
                        arguments={"query": "sequential workflow"},
                    )
                ],
            )
        if len(self.requests) == 2:
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text=self.initial_answer
                or "Agentic AI design patterns include a bounded workflow.",
            )
        if "公司內部政策" in request.messages[-1].content:
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="knowledge-call-2",
                        name="search_knowledge",
                        arguments={"query": "文件沒說的公司內部政策"},
                    )
                ],
            )
        if request.messages[-1].role == "tool":
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="The company has an undocumented internal policy.",
            )
        if (
            "Information already present in the previous assistant answer may be restated"
            not in request.messages[0].content
        ):
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="INSUFFICIENT_INFO",
            )
        if (
            any(
                marker in request.messages[-1].content
                for marker in ("用英文重述", "用英文說")
            )
            or "in english" in request.messages[-1].content.casefold()
        ):
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="In English: agentic AI design patterns include a bounded workflow.",
            )
        if "rephrase that more simply" in request.messages[-1].content.casefold():
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="In simpler English: agentic AI can use a bounded workflow.",
            )
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text="剛剛的回答是在說，agentic AI 可以用 bounded workflow 組織執行步驟。",
        )


class MemoryRoutingProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self, *, force_preference_for_generic: bool = False) -> None:
        self.requests: list[LLMRequest] = []
        self.emitted_tool_calls: list[LLMToolCall] = []
        self.force_preference_for_generic = force_preference_for_generic

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            system = request.messages[0].content
            user = request.messages[-1].content
            if "Use search_memory for saved personal context" not in system:
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o-mini",
                    output_text="INSUFFICIENT_INFO",
                )
            generic_all_memory_query = any(
                marker in user.casefold()
                for marker in (
                    "what do you know about me",
                    "what do you remember about me",
                )
            )
            preference_category_query = any(
                marker in user.casefold()
                for marker in ("什麼偏好", "偏好有哪些", "哪些偏好", "preferences")
            ) and not generic_all_memory_query
            if preference_category_query:
                search_memory_spec = next(
                    tool
                    for tool in request.tools or []
                    if tool["function"]["name"] == "search_memory"
                )
                if (
                    "Use the memory_type filter for a requested saved-memory category"
                    not in system
                    or "memory_type"
                    not in search_memory_spec["function"]["parameters"]["properties"]
                ):
                    return LLMResponse(
                        provider="openai",
                        model="gpt-4o-mini",
                        output_text="INSUFFICIENT_INFO",
                    )
            if generic_all_memory_query:
                query = user
            elif "什麼資訊" in user:
                query = next(
                    shape
                    for shape in ("你有記住我什麼資訊？", "你記得我什麼資訊？")
                    if shape in user
                )
            elif preference_category_query:
                query = next(
                    shape
                    for shape in (
                        "我有什麼偏好？",
                        "我的偏好有哪些？",
                        "你記得我的哪些偏好？",
                        "What are my preferences?",
                        "What preferences have I saved?",
                    )
                    if shape.casefold() in user.casefold()
                )
            elif "API response" in user:
                query = "我 API response 偏好？"
            else:
                query = "我的興趣？"
            arguments = {"query": query, "top_k": 5}
            if preference_category_query or (
                generic_all_memory_query and self.force_preference_for_generic
            ):
                arguments["memory_type"] = "preference"
            tool_call = LLMToolCall(
                id="memory-call-1",
                name="search_memory",
                arguments=arguments,
            )
            self.emitted_tool_calls.append(tool_call)
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[tool_call],
            )

        memory_context = request.messages[-1].content
        if "hits=0" in memory_context:
            answer = "INSUFFICIENT_INFO"
        elif "memory_type=any" in memory_context:
            answer = "你叫 Riley，且偏好 API response 使用 snake_case。"
        else:
            answer = "你偏好 API response 使用 snake_case。"
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=answer,
        )


class PassiveMemoryProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.tools == [] and "用英文說" in request.messages[-1].content:
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="Your name is Riley.",
                finish_reason="stop",
            )
        if request.messages[-1].role != "tool":
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="INSUFFICIENT_INFO",
                finish_reason="stop",
            )
        memory_context = request.messages[-1].content
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text=(
                "你的名字是 Riley。"
                if "Riley" in memory_context
                else "你偏好 API response 使用 snake_case。"
            ),
            finish_reason="stop",
        )


class WrongToolMemoryProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="wrong-knowledge-call",
                        name="search_knowledge",
                        arguments={"query": "company preferences"},
                    )
                ],
            )
        if request.messages[-1].name == "search_knowledge":
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="INSUFFICIENT_INFO",
            )
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text="你偏好 API response 使用 snake_case。",
        )


class RewrittenMemoryQueryProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="rewritten-memory-call",
                        name="search_memory",
                        arguments={
                            "query": "company policy",
                            "top_k": 3,
                            "memory_type": "project_context",
                        },
                    )
                ],
            )
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text="你偏好 API response 使用 snake_case。",
        )


class MemoryRelevanceEmbeddingClient(EmbeddingClient):
    @property
    def name(self) -> str:
        return "memory-relevance-fake"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            provider=self.name,
            model="memory-relevance-fake-model",
            embeddings=[self._vector(value) for value in request.inputs],
        )

    @staticmethod
    def _vector(value: str) -> list[float]:
        normalized = value.casefold()
        vector = [0.0] * 1536
        if (
            "什麼資訊" in normalized
            or "what do you know about me" in normalized
            or "what do you remember about me" in normalized
        ):
            vector[0] = 1.0
            vector[2] = 1.0
        elif (
            any(
                marker in normalized
                for marker in ("什麼偏好", "偏好有哪些", "哪些偏好")
            )
            or "preferences" in normalized
        ):
            vector[0] = 1.0
            vector[1] = 1.0
        elif "api response" in normalized or "snake_case" in normalized:
            vector[0] = 1.0
        elif "簡潔" in normalized or "concise" in normalized:
            vector[1] = 1.0
        elif "名字" in normalized or "name" in normalized or "riley" in normalized:
            vector[2] = 1.0
        elif "興趣" in normalized or "interest" in normalized:
            vector[3] = 1.0
        else:
            vector[4] = 1.0
        return vector


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
            LongTermMemory.__table__,
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


def _override_provider(
    provider: LLMProvider,
    *,
    embedding_client: Optional[EmbeddingClient] = None,
) -> None:
    router = ProviderRouter()
    router.register_provider(provider)
    app.dependency_overrides[get_provider_router] = lambda: router
    app.dependency_overrides[get_embedding_client] = lambda: embedding_client


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


def _seed_saved_memories(session_factory, *contents: str) -> None:
    service = MemoryService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        embedding_client=MemoryRelevanceEmbeddingClient(),
    )
    for content in contents:
        asyncio.run(
            service.save_memory(
                owner_id="local",
                content=content,
                memory_type="preference" if "偏好" in content else "project_context",
            )
        )


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
        assert "FINAL_RESPONSE_LANGUAGE: English" in provider.requests[0].messages[0].content
        assert payload["citations"]
    finally:
        app.dependency_overrides.clear()


def test_tool_capable_provider_uses_bounded_agent_and_persists_backend_citations() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = ToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What does the sequential workflow note say?"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["answer"].startswith("The indexed note confirms")
        assert payload["citations"]
        assert len(payload["messages"][-1]["citations"]) == len(payload["citations"])
        assert len(provider.requests) == 2
        assert provider.requests[0].tools
        assert "FINAL_RESPONSE_LANGUAGE: English" in provider.requests[0].messages[0].content
        assert provider.requests[1].messages[-1].role == "tool"
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


def test_tool_capable_explicit_save_uses_backend_intent_and_persists_memory() -> None:
    session_factory = _build_session_factory()
    provider = ExplicitSaveProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=FakeEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "記住，我偏好所有 API response 使用 snake_case。"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["memory_status"] == "saved"
        assert payload["answer"] == "已儲存記憶"
        assert any(
            tool["function"]["name"] == "save_memory"
            for tool in provider.requests[0].tools or []
        )
        session = session_factory()
        try:
            memories = session.query(LongTermMemory).all()
            assert [(memory.memory_type, memory.content) for memory in memories] == [
                ("preference", "我偏好所有 API response 使用 snake_case。")
            ]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_tool_capable_non_explicit_statement_cannot_save_memory() -> None:
    session_factory = _build_session_factory()
    provider = ExplicitSaveProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=FakeEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我偏好深色介面。"},
        )

        assert response.status_code == 502
        assert response.json()["detail"]["error_code"] == "AGENT_RUNTIME_FAILED"
        session = session_factory()
        try:
            assert session.query(LongTermMemory).count() == 0
            assert session.query(ConversationMessage).count() == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_explicit_save_failure_does_not_create_fake_assistant_success() -> None:
    session_factory = _build_session_factory()
    provider = ExplicitSaveProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "記住，我偏好所有 API response 使用 snake_case。"},
        )

        assert response.status_code == 502
        session = session_factory()
        try:
            assert session.query(LongTermMemory).count() == 0
            assert [message.role for message in session.query(ConversationMessage).all()] == [
                "user"
            ]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_retrying_failed_request_reuses_pending_user_message() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = FailingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        query = "What sequential workflow pattern failed?"
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": query},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": query},
        )

        assert first.status_code == 500
        assert second.status_code == 500
        session = session_factory()
        try:
            messages = session.query(ConversationMessage).all()
            assert [(message.role, message.content) for message in messages] == [
                ("user", query)
            ]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_same_session_agent_directly_rephrases_previous_answer_without_knowledge() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = TransformToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What design patterns are described for agentic AI systems?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "用中文解釋你剛剛的回答"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["answer"].startswith("剛剛的回答")
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert len(provider.requests) == 3
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("transform_query", "answer_prefix"),
    (
        ("用中文說", "剛剛的回答"),
        ("用中文說你剛才的回答", "剛剛的回答"),
        ("In English", "In English"),
        ("簡單一點", "剛剛的回答"),
    ),
)
def test_same_session_agent_supports_implicit_conversational_transforms(
    transform_query: str,
    answer_prefix: str,
) -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = TransformToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What design patterns are described for agentic AI systems?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": transform_query},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["answer"].startswith(answer_prefix)
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert provider.requests[2].tools == []
        assert provider.requests[2].tool_choice is None
    finally:
        app.dependency_overrides.clear()


def test_new_session_agent_transform_cannot_use_previous_session_answer() -> None:
    session_factory = _build_session_factory()
    provider = TransformToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        first_session_id = _create_conversation(client)
        second_session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{second_session_id}/messages",
            json={"query": "用中文解釋你剛剛的回答"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["insufficient_info"] is False
        assert "沒有更早的 assistant 回答" in payload["answer"]
        assert provider.requests == []
        assert first_session_id != second_session_id
    finally:
        app.dependency_overrides.clear()


def test_new_session_implicit_transform_cannot_use_previous_session_answer() -> None:
    session_factory = _build_session_factory()
    provider = TransformToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        first_session_id = _create_conversation(client)
        second_session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{second_session_id}/messages",
            json={"query": "用中文說"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["insufficient_info"] is False
        assert "沒有更早的 assistant 回答" in payload["answer"]
        assert provider.requests == []
        assert first_session_id != second_session_id
    finally:
        app.dependency_overrides.clear()


def test_same_session_agent_rephrases_previous_answer_in_english() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = TransformToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What design patterns are described for agentic AI systems?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "rephrase that more simply"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["answer"].startswith("In simpler English")
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
    finally:
        app.dependency_overrides.clear()


def test_agent_direct_memory_recall_uses_best_result_in_next_iteration() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(
        session_factory,
        "我偏好所有 API response 使用 snake_case。",
        "我的名字是 Riley。",
    )
    provider = MemoryRoutingProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我 API response 偏好？"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert "snake_case" in payload["answer"]
        assert payload["used_saved_memory"] is True
        assert payload["citations"] == []
        assert len(provider.requests) == 2
        assert provider.requests[0].tool_choice == "auto"
        assert "FINAL_RESPONSE_LANGUAGE: Traditional Chinese" in (
            provider.requests[0].messages[0].content
        )
        assert provider.emitted_tool_calls[0].name == "search_memory"
        assert provider.emitted_tool_calls[0].arguments == {
            "query": "我 API response 偏好？",
            "top_k": 5,
        }
        assert provider.requests[1].messages[-1].role == "tool"
        memory_context = provider.requests[1].messages[-1].content
        assert "authority=saved_memory" in memory_context
        assert "mode=direct" in memory_context
        assert "requested_top_k=5" in memory_context
        assert "effective_top_k=1" in memory_context
        assert "hits=1" in memory_context
        assert "best_similarity=1.000000" in memory_context
        assert "snake_case" in memory_context
        assert "Riley" not in memory_context
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "query",
    ("你有記住我什麼資訊？", "你記得我什麼資訊？"),
)
def test_agent_broad_memory_recall_uses_bounded_multiple_results(query: str) -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(
        session_factory,
        "我偏好所有 API response 使用 snake_case。",
        "我的名字是 Riley。",
        "我的興趣是陶藝。",
    )
    provider = MemoryRoutingProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": query},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert "Riley" in payload["answer"]
        assert "snake_case" in payload["answer"]
        assert payload["used_saved_memory"] is True
        assert payload["citations"] == []
        tool_call = provider.emitted_tool_calls[0]
        assert tool_call.name == "search_memory"
        assert tool_call.arguments["query"] == query
        assert provider.requests[1].messages[-1].role == "tool"
        memory_context = provider.requests[1].messages[-1].content
        assert "mode=broad" in memory_context
        assert "requested_top_k=5" in memory_context
        assert "effective_top_k=3" in memory_context
        assert "hits=2" in memory_context
        assert "snake_case" in memory_context
        assert "Riley" in memory_context
        assert "陶藝" not in memory_context
    finally:
        app.dependency_overrides.clear()


def test_agent_generic_english_memory_recall_ignores_provider_preference_narrowing() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(
        session_factory,
        "我偏好所有 API response 使用 snake_case。",
        "我的名字是 Riley。",
        "我偏好 SDD/TDD 開發方式。",
    )
    provider = MemoryRoutingProvider(force_preference_for_generic=True)
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What do you know about me?"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert "Riley" in payload["answer"]
        assert "snake_case" in payload["answer"]
        assert payload["used_saved_memory"] is True
        assert payload["citations"] == []
        assert provider.emitted_tool_calls[0].arguments["memory_type"] == "preference"
        memory_context = provider.requests[1].messages[-1].content
        assert "mode=broad" in memory_context
        assert "memory_type=any" in memory_context
        assert "effective_top_k=3" in memory_context
        assert "Riley" in memory_context
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "query",
    (
        "我有什麼偏好？",
        "我的偏好有哪些？",
        "你記得我的哪些偏好？",
        "What are my preferences?",
        "What preferences have I saved?",
    ),
)
def test_agent_broad_preference_recall_selects_memory_tool_and_filters_type(
    query: str,
) -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(
        session_factory,
        "我偏好所有 API response 使用 snake_case。",
        "我的名字是 Riley。",
    )
    provider = MemoryRoutingProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": query},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert "snake_case" in payload["answer"]
        assert payload["used_saved_memory"] is True
        assert payload["citations"] == []
        assert len(provider.requests) == 2
        tool_call = provider.emitted_tool_calls[0]
        assert tool_call.name == "search_memory"
        assert tool_call.arguments == {
            "query": query,
            "top_k": 5,
            "memory_type": "preference",
        }
        memory_context = provider.requests[1].messages[-1].content
        assert "authority=saved_memory" in memory_context
        assert "mode=broad" in memory_context
        assert "memory_type=preference" in memory_context
        assert "effective_top_k=3" in memory_context
        assert "hits=1" in memory_context
        assert "best_similarity=0.707107" in memory_context
        assert "Riley" not in memory_context
    finally:
        app.dependency_overrides.clear()


def test_agent_broad_preference_recall_returns_at_most_three_memories() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(
        session_factory,
        "我偏好 API response 使用 snake_case。",
        "我偏好 API response keys 使用 snake_case。",
        "我偏好 API response examples 使用 snake_case。",
        "我偏好 API response docs 使用 snake_case。",
        "我的名字是 Riley。",
    )
    provider = MemoryRoutingProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我有什麼偏好？"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["used_saved_memory"] is True
        memory_context = provider.requests[1].messages[-1].content
        assert "mode=broad" in memory_context
        assert "effective_top_k=3" in memory_context
        assert "hits=3" in memory_context
        assert "Riley" not in memory_context
    finally:
        app.dependency_overrides.clear()


def test_clear_memory_intent_uses_safe_fallback_when_provider_selects_no_tool() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(
        session_factory,
        "我偏好所有 API response 使用 snake_case。",
        "我的名字是 Riley。",
    )
    provider = PassiveMemoryProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我有什麼偏好？"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert "snake_case" in payload["answer"]
        assert "Riley" not in payload["answer"]
        assert payload["used_saved_memory"] is True
        assert payload["citations"] == []
        assert len(provider.requests) == 2
        assert provider.requests[0].tools
        assert provider.requests[1].messages[-1].role == "tool"
        assert provider.requests[1].messages[-1].name == "search_memory"
        memory_context = provider.requests[1].messages[-1].content
        assert "mode=broad" in memory_context
        assert "memory_type=preference" in memory_context
        assert "effective_top_k=3" in memory_context
        assert "hits=1" in memory_context
        db = session_factory()
        try:
            workflow = (
                db.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "agent")
                .order_by(WorkflowRun.id.desc())
                .first()
            )
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json)
            assert metadata["available_tool_count"] == 3
            assert metadata["available_tool_names"] == [
                "save_memory",
                "search_knowledge",
                "search_memory",
            ]
            assert metadata["provider_termination_type"] == "final_text"
            assert metadata["provider_termination_types"] == [
                "insufficient_info",
                "final_text",
            ]
            assert metadata["tool_names_used"] == ["search_memory"]
            assert metadata["deterministic_memory_fallback_used"] is True
            assert metadata["memory_retrieval_mode"] == "broad"
            assert metadata["memory_type_filter"] == "preference"
            assert metadata["memory_effective_top_k"] == 3
            assert metadata["memory_retrieval_hit_count"] == 1
            assert metadata["memory_best_similarity"] == pytest.approx(0.707107)
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_provider_insufficient_for_enterprise_query_does_not_force_backend_tool() -> None:
    session_factory = _build_session_factory()
    provider = PassiveMemoryProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What is the company retention policy?"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["insufficient_info"] is True
        assert response.json()["used_saved_memory"] is False
        assert response.json()["citations"] == []
        assert len(provider.requests) == 1
        assert provider.requests[0].tools
    finally:
        app.dependency_overrides.clear()


def test_clear_memory_intent_recovers_when_provider_selects_wrong_tool() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(session_factory, "我偏好所有 API response 使用 snake_case。")
    provider = WrongToolMemoryProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我有什麼偏好？"},
        )

        assert response.status_code == 200, response.text
        assert "snake_case" in response.json()["answer"]
        assert response.json()["used_saved_memory"] is True
        assert response.json()["citations"] == []
        assert len(provider.requests) == 3
        assert provider.requests[2].messages[-1].name == "search_memory"
        db = session_factory()
        try:
            workflow = (
                db.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "agent")
                .order_by(WorkflowRun.id.desc())
                .first()
            )
            metadata = json.loads(workflow.metadata_json)
            assert metadata["tool_names_used"] == [
                "search_knowledge",
                "search_memory",
            ]
            assert metadata["deterministic_memory_fallback_used"] is True
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_clear_memory_tool_uses_backend_original_query_semantics() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(
        session_factory,
        "我偏好所有 API response 使用 snake_case。",
        "我的名字是 Riley。",
    )
    provider = RewrittenMemoryQueryProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我有什麼偏好？"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["used_saved_memory"] is True
        assert response.json()["citations"] == []
        memory_context = provider.requests[1].messages[-1].content
        assert "mode=broad" in memory_context
        assert "memory_type=preference" in memory_context
        assert "hits=1" in memory_context
        assert "Riley" not in memory_context
    finally:
        app.dependency_overrides.clear()


def test_same_session_agent_rephrases_chinese_answer_in_english() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = TransformToolCallingProvider(
        initial_answer="Agentic AI 的設計模式包含 bounded workflow。"
    )
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What design patterns are described for agentic AI systems?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "用英文重述你剛剛的回答"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["answer"].startswith("In English")
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert "Agentic AI 的設計模式" in provider.requests[2].messages[-1].content
        assert provider.requests[2].tools == []
    finally:
        app.dependency_overrides.clear()


def test_same_session_agent_translates_previous_answer_with_equivalent_wording() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = TransformToolCallingProvider(
        initial_answer="你的名字是 Riley。"
    )
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What design patterns are described for agentic AI systems?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "用英文說你剛才的回答"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["answer"].startswith("In English")
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert "你的名字是 Riley" in provider.requests[2].messages[-1].content
        assert provider.requests[2].tools == []
        db = session_factory()
        try:
            workflow = (
                db.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "agent")
                .order_by(WorkflowRun.id.desc())
                .first()
            )
            assert workflow is not None
            metadata = json.loads(workflow.metadata_json)
            assert metadata["available_tool_count"] == 0
            assert metadata["available_tool_names"] == []
            assert metadata["conversation_transform"] is True
            assert metadata["conversation_authority_available"] is True
            assert metadata["provider_termination_type"] == "final_text"
            assert metadata["provider_termination_types"] == ["final_text"]
            assert metadata["tool_names_used"] == []
            assert metadata["used_saved_memory"] is False
            assert metadata["citation_count"] == 0
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_same_session_saved_memory_answer_can_be_translated_in_english() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(session_factory, "我的名字是 Riley。")
    provider = PassiveMemoryProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我的名字是什麼？"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "用英文說你剛才的回答"},
        )

        assert first.status_code == 200, first.text
        assert first.json()["used_saved_memory"] is True
        assert second.status_code == 200, second.text
        assert second.json()["answer"] == "Your name is Riley."
        assert second.json()["citations"] == []
        assert provider.requests[-1].tools == []
        assert "你的名字是 Riley" in provider.requests[-1].messages[-1].content
    finally:
        app.dependency_overrides.clear()


def test_new_session_equivalent_transform_wording_cannot_use_previous_answer() -> None:
    session_factory = _build_session_factory()
    provider = PassiveMemoryProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        first_session_id = _create_conversation(client)
        second_session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{second_session_id}/messages",
            json={"query": "用英文說你剛才的回答"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["insufficient_info"] is False
        assert "There is no earlier assistant answer" in response.json()["answer"]
        assert provider.requests == []
        assert first_session_id != second_session_id
    finally:
        app.dependency_overrides.clear()


def test_same_session_agent_simplifies_previous_answer_without_new_knowledge() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = TransformToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What design patterns are described for agentic AI systems?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "簡單解釋你剛剛說的"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["answer"].startswith("剛剛的回答")
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert provider.requests[2].tools == []
    finally:
        app.dependency_overrides.clear()


def test_same_session_agent_simplifies_previous_answer_with_equivalent_wording() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = TransformToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What design patterns are described for agentic AI systems?"},
        )
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "簡單講一下你剛才的答案"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["answer"].startswith("剛剛的回答")
        assert payload["insufficient_info"] is False
        assert payload["citations"] == []
        assert provider.requests[2].tools == []
    finally:
        app.dependency_overrides.clear()


def test_same_session_unsupported_new_enterprise_claim_still_fails_closed() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    provider = TransformToolCallingProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        first = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "What design patterns are described for agentic AI systems?"},
        )
        session = session_factory()
        try:
            session.query(KnowledgeChunk).delete()
            session.commit()
        finally:
            session.close()
        second = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "再補充文件沒說的公司內部政策"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        payload = second.json()
        assert payload["insufficient_info"] is True
        assert payload["citations"] == []
        assert provider.requests[2].tools
        assert provider.requests[2].tool_choice == "auto"
    finally:
        app.dependency_overrides.clear()


def test_agent_memory_no_hit_does_not_inject_unrelated_memory() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(
        session_factory,
        "我偏好所有 API response 使用 snake_case。",
        "我的名字是 Riley。",
    )
    provider = MemoryRoutingProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages",
            json={"query": "我的興趣？"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["insufficient_info"] is True
        assert payload["used_saved_memory"] is False
        assert payload["citations"] == []
        memory_context = provider.requests[1].messages[-1].content
        assert "hits=0" in memory_context
        assert "snake_case" not in memory_context
        assert "Riley" not in memory_context
    finally:
        app.dependency_overrides.clear()
