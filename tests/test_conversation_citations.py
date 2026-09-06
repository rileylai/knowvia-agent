from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.conversation_citations import (
    ConversationCitation,
    deserialize_conversation_citations,
    serialize_conversation_citations,
)
from src.app.dependencies import get_current_owner_id, get_embedding_client, get_provider_router
from src.app.main import app
from src.db.models import ConversationMessage
from src.repositories.conversation_repository import ConversationRepository
from src.services.conversation_context import (
    ConversationContextMessage,
    assemble_conversation_context,
)
from tests.test_conversation_api import (
    CapturingProvider,
    _build_session_factory,
    _create_conversation,
    _override_database,
    _override_provider,
    _seed_knowledge,
)


def _citation(*, name: str = "agent-patterns.pdf") -> ConversationCitation:
    return ConversationCitation(
        notion_path="Knowledge/Agents/Patterns",
        page_id="page-agent-patterns",
        score=0.9132,
        source_kind="pdf",
        source_display_name=name,
        locator="page 1",
        source_url=None,
        image_index=None,
        sequence_index=None,
        original_filename=name,
    )


def test_citation_metadata_is_versioned_bounded_and_excludes_unapproved_fields() -> None:
    encoded = serialize_conversation_citations(
        [_citation(name="x" * 1000)]
    )

    assert encoded is not None
    payload = json.loads(encoded)
    assert payload["citation_metadata_version"] == 1
    assert len(payload["citations"]) == 1
    assert len(payload["citations"][0]["source_display_name"]) <= 512
    assert "chunk_text" not in payload["citations"][0]
    assert "metadata" not in payload["citations"][0]


def test_assistant_content_and_citation_metadata_are_persisted_in_one_message() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    try:
        repository = ConversationRepository(session)
        conversation = repository.create_session(owner_id="owner-a")
        repository.append_message(
            session_id=conversation.id,
            owner_id="owner-a",
            role="assistant",
            content="The grounded answer.",
            citations=[_citation()],
        )
        session.commit()

        message = session.query(ConversationMessage).one()
        assert message.content == "The grounded answer."
        assert message.metadata_json is not None
        assert json.loads(message.metadata_json)["citations"][0][
            "source_display_name"
        ] == "agent-patterns.pdf"
    finally:
        session.close()


def test_legacy_malformed_and_unsupported_metadata_fall_back_to_empty_citations() -> None:
    assert deserialize_conversation_citations(None) == []
    assert deserialize_conversation_citations("not-json") == []
    assert deserialize_conversation_citations(
        json.dumps({"citation_metadata_version": 999, "citations": [_citation().__dict__]})
    ) == []
    assert deserialize_conversation_citations(
        json.dumps({"citation_metadata_version": 1, "citations": "not-a-list"})
    ) == []


def test_session_snapshot_safely_loads_legacy_and_malformed_message_metadata() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    try:
        repository = ConversationRepository(session)
        conversation = repository.create_session(owner_id="owner-a")
        message = repository.append_message(
            session_id=conversation.id,
            owner_id="owner-a",
            role="assistant",
            content="Legacy answer.",
        )
        message.metadata_json = "not-json"
        session.commit()

        snapshot = repository.get_session(
            session_id=conversation.id,
            owner_id="owner-a",
        )
        assert snapshot is not None
        assert snapshot.messages[0].citations == []
    finally:
        session.close()


def test_grounded_citations_stay_attached_through_follow_up_and_session_reload() -> None:
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
        first_citations = first.json()["messages"][1]["citations"]
        second_messages = second.json()["messages"]
        assert first_citations
        assert second_messages[1]["citations"] == first_citations
        assert second_messages[3]["citations"]

        restored = client.get(f"/api/conversations/{session_id}")
        assert restored.status_code == 200
        restored_messages = restored.json()["messages"]
        assert restored_messages[1]["citations"] == first_citations
        assert restored_messages[3]["citations"] == second_messages[3]["citations"]
    finally:
        app.dependency_overrides.clear()


def test_message_citation_metadata_is_not_conversation_evidence() -> None:
    session_factory = _build_session_factory()
    session = session_factory()
    try:
        repository = ConversationRepository(session)
        conversation = repository.create_session(owner_id="owner-a")
        repository.append_message(
            session_id=conversation.id,
            owner_id="owner-a",
            role="assistant",
            content="Production uses MySQL.",
            citations=[_citation(name="private-source-name")],
        )
        session.commit()
        messages = repository.list_messages(
            session_id=conversation.id,
            owner_id="owner-a",
        )
        context = assemble_conversation_context(
            history=[
                ConversationContextMessage(role=message.role, content=message.content)
                for message in messages
            ],
            current_question="What database does production use?",
            max_messages=6,
            token_budget=2048,
        )

        assert "private-source-name" not in context.rendered_text
        assert "Production uses MySQL." in context.rendered_text
    finally:
        session.close()
