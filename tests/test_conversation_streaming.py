from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.app.dependencies import get_current_owner_id
from src.app.main import app
from src.app.api.routes.conversations import stream_conversation_message
from src.app.schemas import ConversationMessageRequest
from src.db.models import ConversationMessage, LongTermMemory
from src.providers import LLMProvider, LLMRequest, LLMResponse, LLMToolCall

from test_conversation_api import (
    CapturingProvider,
    ExplicitSaveProvider,
    FailingProvider,
    MemoryRelevanceEmbeddingClient,
    ToolCallingProvider,
    _build_session_factory,
    _create_conversation,
    _override_database,
    _override_provider,
    _seed_knowledge,
    _seed_saved_memories,
    FakeEmbeddingClient,
)


def _events(response) -> list[dict]:
    frames = [frame for frame in response.text.split("\n\n") if frame.strip()]
    parsed = []
    for frame in frames:
        lines = frame.splitlines()
        event_type = next(line.split(":", 1)[1].strip() for line in lines if line.startswith("event:"))
        data = next(line.split(":", 1)[1].strip() for line in lines if line.startswith("data:"))
        payload = json.loads(data)
        parsed.append({"event_type": event_type, **payload})
    return parsed


class ContextualStreamingProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []
        self.tool_names: list[str] = []

    @property
    def name(self) -> str:
        return "openai"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            response = LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="knowledge-1",
                        name="search_knowledge",
                        arguments={"query": "sequential workflow", "top_k": 5},
                    )
                ],
            )
            self.tool_names.append("search_knowledge")
            return response
        if len(self.requests) == 2:
            if "saved context would materially improve" not in request.messages[0].content:
                return LLMResponse(
                    provider="openai",
                    model="gpt-4o-mini",
                    output_text="The PDF evidence is sufficient.",
                )
            response = LLMResponse(
                provider="openai",
                model="gpt-4o-mini",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="memory-1",
                        name="search_memory",
                        arguments={
                            "query": (
                                "organization size and development practices relevant "
                                "to adopting production AI agent practices"
                            ),
                            "top_k": 5,
                            "retrieval_mode": "contextual",
                        },
                    )
                ],
            )
            self.tool_names.append("search_memory")
            return response
        return LLMResponse(
            provider="openai",
            model="gpt-4o-mini",
            output_text="Prioritize controlled tool invocation for this organization.",
        )


def test_streaming_knowledge_flow_has_bounded_ordered_events_and_persists_once() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    _override_database(session_factory)
    canonical_answer = ("Grounded streaming answer. " * 8).strip()
    _override_provider(CapturingProvider(outputs=[canonical_answer]))
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": "What does the sequential workflow note say?"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _events(response)
        event_types = [event["event_type"] for event in events]
        assert event_types[:2] == [
            "execution_status",
            "execution_status",
        ]
        assert event_types[-2:] == ["citations", "done"]
        assert set(event_types[2:-2]) == {"answer_delta"}
        assert [event["payload"]["phase"] for event in events[:2]] == [
            "searching_knowledge",
            "generating",
        ]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert len({event["run_id"] for event in events}) == 1
        assert sum(event["event_type"] == "done" for event in events) == 1
        assert "".join(event["payload"]["text"] for event in events if event["event_type"] == "answer_delta") == (
            canonical_answer
        )
        assert len([event for event in events if event["event_type"] == "answer_delta"]) > 1
        assert events[-1]["payload"]["message_id"] > 0

        session = session_factory()
        try:
            messages = session.query(ConversationMessage).order_by(ConversationMessage.sequence_number).all()
            assert [message.role for message in messages] == [
                "user",
                "assistant",
            ]
            assert len(json.loads(messages[1].metadata_json)["citations"]) == 1
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_streaming_agent_tool_status_is_bounded_and_does_not_expose_tool_data() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    _override_database(session_factory)
    _override_provider(ToolCallingProvider())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": "What does the sequential workflow note say?"},
        )

        events = _events(response)
        assert [event["payload"]["phase"] for event in events if event["event_type"] == "execution_status"] == [
            "searching_knowledge",
            "generating",
        ]
        assert "arguments" not in response.text
        assert "tool_calls" not in response.text
        assert "raw_response" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_streaming_memory_recall_reports_used_memory_without_enterprise_citations() -> None:
    session_factory = _build_session_factory()
    _seed_saved_memories(session_factory, "我偏好 API response 使用 snake_case。")
    _override_database(session_factory)
    _override_provider(
        CapturingProvider(outputs=["unused"]),
        embedding_client=MemoryRelevanceEmbeddingClient(),
    )
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": "What are my preferences?"},
        )

        events = _events(response)
        assert [event["payload"]["phase"] for event in events if event["event_type"] == "execution_status"] == [
            "searching_memory",
            "generating",
        ]
        assert not any(event["event_type"] == "citations" for event in events)
        assert events[-1]["payload"]["used_saved_memory"] is True
        assert events[-1]["payload"]["memory_saved"] is False
        session = session_factory()
        try:
            assistant = session.query(ConversationMessage).filter_by(role="assistant").one()
            metadata = json.loads(assistant.metadata_json)
            assert metadata["used_saved_memory"] is True
            assert metadata["citations"] == []
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_streaming_contextual_memory_flow_reports_both_searches_and_keeps_authorities_separate() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    _seed_saved_memories(
        session_factory,
        "Our organization has approximately 1000 people.",
        "We prefer SDD/TDD development practices.",
    )
    provider = ContextualStreamingProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=MemoryRelevanceEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={
                "query": (
                    "Based on the production AI agent practices in the indexed PDF, "
                    "what should our company pay particular attention to when adopting them?"
                )
            },
        )

        assert response.status_code == 200, response.text
        events = _events(response)
        phases = [
            event["payload"]["phase"]
            for event in events
            if event["event_type"] == "execution_status"
        ]
        assert phases == [
            "searching_knowledge",
            "searching_memory",
            "generating",
        ]
        assert provider.tool_names == ["search_knowledge", "search_memory"]
        assert any(event["event_type"] == "citations" for event in events)
        assert events[-1]["event_type"] == "done"
        assert events[-1]["payload"]["used_saved_memory"] is True
        assert sum(event["event_type"] == "done" for event in events) == 1
        assert "arguments" not in response.text
        assert "tool_calls" not in response.text
        assert "raw_response" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_streaming_explicit_save_reports_save_status_and_safe_metadata() -> None:
    session_factory = _build_session_factory()
    _override_database(session_factory)
    provider = ExplicitSaveProvider()
    _override_provider(
        provider,
        embedding_client=FakeEmbeddingClient(),
    )
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": "記住，我偏好所有 API response 使用 snake_case。"},
        )

        events = _events(response)
        assert provider.requests == []
        assert [event["payload"]["phase"] for event in events if event["event_type"] == "execution_status"] == [
            "saving_memory",
        ]
        assert not any(
            event["payload"].get("phase") == "generating"
            for event in events
            if event["event_type"] == "execution_status"
        )
        assert events[-1]["event_type"] == "done"
        assert events[-1]["payload"]["memory_saved"] is True
        assert events[-1]["payload"]["used_saved_memory"] is False
    finally:
        app.dependency_overrides.clear()


def test_streaming_explicit_save_uses_backend_intent_for_company_statement() -> None:
    session_factory = _build_session_factory()
    provider = ExplicitSaveProvider(
        memory_type="company",
        content="provider supplied content",
    )
    _override_database(session_factory)
    _override_provider(provider, embedding_client=FakeEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": "記住我的公司叫做Knowvia"},
        )

        events = _events(response)
        assert provider.requests == []
        assert [event["payload"]["phase"] for event in events if event["event_type"] == "execution_status"] == [
            "saving_memory",
        ]
        assert events[-1]["event_type"] == "done"
        assert events[-1]["payload"]["memory_saved"] is True
        assert events[-1]["payload"]["memory_already_saved"] is False

        inspector = client.get("/api/memories")
        assert inspector.status_code == 200
        assert [(memory["memory_type"], memory["content"]) for memory in inspector.json()] == [
            ("project_context", "我的公司叫做Knowvia")
        ]

        session = session_factory()
        try:
            memories = session.query(LongTermMemory).all()
            assert [(memory.memory_type, memory.content) for memory in memories] == [
                ("project_context", "我的公司叫做Knowvia")
            ]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("query", "expected_content"),
    [
        (
            "記住，我們公司的 AI Agent 主要用來協助員工搜尋企業知識、整理資訊並回答內部問題",
            "我們公司的 AI Agent 主要用來協助員工搜尋企業知識、整理資訊並回答內部問題",
        ),
        (
            "記住，我們的 Agent 需要整合 PDF、網站、圖片以及公司內部文件等不同知識來源",
            "我們的 Agent 需要整合 PDF、網站、圖片以及公司內部文件等不同知識來源",
        ),
        (
            "記住，我們希望 Agent 回答企業問題時要有資料來源，不能只依靠模型自己的知識回答",
            "我們希望 Agent 回答企業問題時要有資料來源，不能只依靠模型自己的知識回答",
        ),
    ],
)
def test_streaming_explicit_save_uses_same_deterministic_path_for_a_b_c(
    query: str,
    expected_content: str,
) -> None:
    session_factory = _build_session_factory()
    provider = ExplicitSaveProvider()
    _override_database(session_factory)
    _override_provider(provider, embedding_client=FakeEmbeddingClient())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": query},
        )

        assert response.status_code == 200, response.text
        events = _events(response)
        assert provider.requests == []
        assert [
            event["payload"]["phase"]
            for event in events
            if event["event_type"] == "execution_status"
        ] == ["saving_memory"]
        assert not any(
            event["payload"].get("phase") == "generating"
            for event in events
            if event["event_type"] == "execution_status"
        )
        assert events[-1]["event_type"] == "done"
        assert events[-1]["payload"]["memory_saved"] is True

        session = session_factory()
        try:
            memories = session.query(LongTermMemory).all()
            assert [(memory.memory_type, memory.content) for memory in memories] == [
                ("project_context", expected_content)
            ]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_streaming_explicit_save_failure_does_not_call_provider_or_fake_success() -> None:
    session_factory = _build_session_factory()
    provider = ExplicitSaveProvider()
    _override_database(session_factory)
    _override_provider(provider)
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": "記住，我偏好所有 API response 使用 snake_case。"},
        )

        assert response.status_code == 200
        events = _events(response)
        assert provider.requests == []
        assert [
            event["payload"]["phase"]
            for event in events
            if event["event_type"] == "execution_status"
        ] == ["saving_memory"]
        assert events[-1]["event_type"] == "error"
        assert events[-1]["payload"]["error_code"] == "MEMORY_EMBEDDING_FAILED"
        assert not any(event["event_type"] == "done" for event in events)
    finally:
        app.dependency_overrides.clear()


def test_streaming_insufficient_info_has_no_citations_event() -> None:
    session_factory = _build_session_factory()
    _override_database(session_factory)
    _override_provider(CapturingProvider(outputs=["INSUFFICIENT_INFO"]))
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": "No indexed evidence matches this question"},
        )

        events = _events(response)
        assert not any(event["event_type"] == "citations" for event in events)
        assert events[-1]["payload"]["insufficient_info"] is True
        assert events[-1]["payload"]["termination_reason"] == "insufficient_info"
    finally:
        app.dependency_overrides.clear()


def test_streaming_provider_failure_emits_error_without_done_or_fake_assistant() -> None:
    session_factory = _build_session_factory()
    _seed_knowledge(session_factory)
    _override_database(session_factory)
    _override_provider(FailingProvider())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        client = TestClient(app)
        session_id = _create_conversation(client)
        response = client.post(
            f"/api/conversations/{session_id}/messages/stream",
            json={"query": "What sequential workflow pattern failed?"},
        )

        events = _events(response)
        assert events[-1]["event_type"] == "error"
        assert events[-1]["payload"]["error_code"] == "QA_WORKFLOW_FAILED"
        assert "Traceback" not in response.text
        assert not any(event["event_type"] == "done" for event in events)

        session = session_factory()
        try:
            assert [message.role for message in session.query(ConversationMessage).all()] == ["user"]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_streaming_invalid_session_emits_bounded_error_without_done() -> None:
    session_factory = _build_session_factory()
    _override_database(session_factory)
    _override_provider(CapturingProvider())
    app.dependency_overrides[get_current_owner_id] = lambda: "local"

    try:
        response = TestClient(app).post(
            "/api/conversations/999999/messages/stream",
            json={"query": "missing session"},
        )

        events = _events(response)
        assert events[-1]["event_type"] == "error"
        assert events[-1]["payload"] == {
            "error_code": "CONVERSATION_UNAVAILABLE",
            "message": "Conversation is unavailable.",
        }
        assert not any(event["event_type"] == "done" for event in events)
    finally:
        app.dependency_overrides.clear()


def test_stream_disconnect_cancels_inflight_orchestrator_without_emitting_success() -> None:
    class BlockingOrchestrator:
        def __init__(self) -> None:
            self.cancelled = False

        async def send_message(self, **_kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def exercise_disconnect() -> bool:
        orchestrator = BlockingOrchestrator()

        class DisconnectedRequest:
            state = SimpleNamespace(workflow_id="")

            async def is_disconnected(self) -> bool:
                return True

        with patch(
            "src.app.api.routes.conversations._build_conversation_orchestrator",
            return_value=orchestrator,
        ):
            response = await stream_conversation_message(
                session_id=1,
                payload=ConversationMessageRequest(query="disconnect me"),
                request=DisconnectedRequest(),
                db_session=None,
                db_session_factory=None,
                unit_of_work_factory=None,
                embedding_client=None,
                provider_router=None,
                cost_tracker=None,
                prompt_template_loader=None,
                memory_service=None,
                owner_id="local",
            )
            try:
                await response.body_iterator.__anext__()
            except StopAsyncIteration:
                pass
        return orchestrator.cancelled

    assert asyncio.run(exercise_disconnect()) is True
