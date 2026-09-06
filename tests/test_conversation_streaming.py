from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.app.dependencies import get_current_owner_id
from src.app.main import app
from src.app.api.routes.conversations import stream_conversation_message
from src.app.schemas import ConversationMessageRequest
from src.db.models import ConversationMessage

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


def test_streaming_explicit_save_reports_save_status_and_safe_metadata() -> None:
    session_factory = _build_session_factory()
    _override_database(session_factory)
    _override_provider(
        ExplicitSaveProvider(),
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
        assert [event["payload"]["phase"] for event in events if event["event_type"] == "execution_status"] == [
            "saving_memory",
        ]
        assert events[-1]["payload"]["memory_saved"] is True
        assert events[-1]["payload"]["used_saved_memory"] is False
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
