from __future__ import annotations

import asyncio
from typing import List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import ConversationMessage, ConversationSession, LongTermMemory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.memory import (
    detect_explicit_save_intent,
    is_broad_memory_recall_query,
    is_memory_recall_query,
)
from src.providers import EmbeddingClient, EmbeddingClientError, EmbeddingRequest, EmbeddingResponse
from src.services.memory import MemoryService


class FakeEmbeddingClient(EmbeddingClient):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: List[str] = []

    @property
    def name(self) -> str:
        return "fake"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.calls.extend(request.inputs)
        if self.fail:
            raise EmbeddingClientError("embedding unavailable")
        embeddings = []
        for value in request.inputs:
            vector = [0.0] * 1536
            vector[0 if "pgvector" in value.casefold() else 1] = 1.0
            embeddings.append(vector)
        return EmbeddingResponse(
            provider=self.name,
            model=request.model or "fake-model",
            embeddings=embeddings,
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
        tables=[ConversationSession.__table__, ConversationMessage.__table__, LongTermMemory.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _build_service(session_factory, embedding_client):
    return MemoryService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        embedding_client=embedding_client,
    )


def test_detect_explicit_save_intent_rejects_plain_statement() -> None:
    assert detect_explicit_save_intent("我們 production 使用 pgvector。") is None


def test_detect_explicit_save_intent_classifies_decision() -> None:
    intent = detect_explicit_save_intent("記住，我們 production 最後決定使用 pgvector。")

    assert intent is not None
    assert intent.content == "我們 production 最後決定使用 pgvector。"
    assert intent.memory_type == "decision"


def test_detect_explicit_save_intent_classifies_preference_and_fallback_context() -> None:
    preference = detect_explicit_save_intent("Remember that I prefer concise answers.")
    context = detect_explicit_save_intent("記住，我們 production 使用 pgvector。")

    assert preference is not None
    assert preference.memory_type == "preference"
    assert context is not None
    assert context.memory_type == "project_context"


@pytest.mark.parametrize(
    ("query", "expected"),
    (
        ("你記得我的名字嗎？", True),
        ("我的職業？", True),
        ("你記得我的職業？", True),
        ("What is my name?", True),
        ("What is my occupation?", True),
        ("What did we decide about production?", True),
        ("What database does production use?", False),
    ),
)
def test_memory_recall_query_uses_bounded_personal_question_shapes(
    query: str,
    expected: bool,
) -> None:
    assert is_memory_recall_query(query) is expected


def test_saved_information_question_is_broad_memory_recall() -> None:
    assert is_broad_memory_recall_query("你記得我什麼資訊？") is True


def test_save_memory_persists_allowed_type_and_embedding_metadata() -> None:
    session_factory = _build_session_factory()
    embedding_client = FakeEmbeddingClient()
    service = _build_service(session_factory, embedding_client)

    result = asyncio.run(service.save_memory(
        owner_id="owner-a",
        content="我們 production 使用 pgvector。",
        memory_type="project_context",
    ))

    assert result.status == "saved"
    assert result.memory.memory_type == "project_context"
    assert result.memory.embedding_model == "text-embedding-3-small"
    assert result.memory.embedding_dimensions == 1536
    assert len(embedding_client.calls) == 1


def test_save_memory_rejects_unsupported_type() -> None:
    session_factory = _build_session_factory()
    service = _build_service(session_factory, FakeEmbeddingClient())

    with pytest.raises(Exception, match="memory_type is not supported"):
        import asyncio

        asyncio.run(
            service.save_memory(
                owner_id="owner-a",
                content="Use pgvector in production.",
                memory_type="fact",
            )
        )


def test_embedding_failure_does_not_create_memory() -> None:
    session_factory = _build_session_factory()
    service = _build_service(session_factory, FakeEmbeddingClient(fail=True))

    with pytest.raises(Exception):
        asyncio.run(service.save_memory(
            owner_id="owner-a",
            content="Use pgvector in production.",
            memory_type="decision",
        ))

    session = session_factory()
    try:
        assert session.query(LongTermMemory).count() == 0
    finally:
        session.close()


def test_exact_duplicate_returns_existing_without_reembedding() -> None:
    session_factory = _build_session_factory()
    embedding_client = FakeEmbeddingClient()
    service = _build_service(session_factory, embedding_client)
    first = asyncio.run(service.save_memory(
        owner_id="owner-a",
        content="Use pgvector in production.",
        memory_type="decision",
    ))

    duplicate = asyncio.run(service.save_memory(
        owner_id="owner-a",
        content="  Use   pgvector in production. ",
        memory_type="decision",
    ))

    assert first.status == "saved"
    assert duplicate.status == "already_saved"
    assert duplicate.memory.id == first.memory.id
    assert len(embedding_client.calls) == 1


def test_memory_search_and_delete_are_owner_scoped_and_durable() -> None:
    session_factory = _build_session_factory()
    embedding_client = FakeEmbeddingClient()
    service = _build_service(session_factory, embedding_client)
    saved = asyncio.run(service.save_memory(
        owner_id="owner-a",
        content="Use pgvector in production.",
        memory_type="decision",
    ))
    asyncio.run(service.save_memory(
        owner_id="owner-b",
        content="Use pgvector in production.",
        memory_type="decision",
    ))

    owner_a_matches = asyncio.run(service.search_memories(
        owner_id="owner-a",
        query="What database choice did we make?",
        query_embedding=[1.0] + [0.0] * 1535,
    ))
    owner_b_list = service.list_memories(owner_id="owner-b")

    assert [memory.id for memory in owner_a_matches] == [saved.memory.id]
    assert len(owner_b_list) == 1
    assert service.delete_memory(owner_id="owner-b", memory_id=saved.memory.id) is False
    assert service.delete_memory(owner_id="owner-a", memory_id=saved.memory.id) is True
    assert service.list_memories(owner_id="owner-a") == []
    assert asyncio.run(service.search_memories(
        owner_id="owner-a",
        query="What database choice did we make?",
        query_embedding=[1.0] + [0.0] * 1535,
    )) == []


def test_memory_search_applies_direct_and_broad_relevance_selection() -> None:
    session_factory = _build_session_factory()
    service = _build_service(session_factory, RelevanceEmbeddingClient())
    asyncio.run(service.save_memory(
        owner_id="owner-a",
        content="我的名字是Riley",
    ))
    asyncio.run(service.save_memory(
        owner_id="owner-a",
        content="我的職業是軟體工程師",
    ))

    name_matches = asyncio.run(service.search_memories(
        owner_id="owner-a",
        query="What is my name?",
    ))
    occupation_matches = asyncio.run(service.search_memories(
        owner_id="owner-a",
        query="你記得我的職業嗎？",
    ))
    interest_matches = asyncio.run(service.search_memories(
        owner_id="owner-a",
        query="我的興趣？",
    ))
    broad_matches = asyncio.run(service.search_memories(
        owner_id="owner-a",
        query="你記得我哪些事情？",
    ))

    assert [memory.content for memory in name_matches] == ["我的名字是Riley"]
    assert [memory.content for memory in occupation_matches] == ["我的職業是軟體工程師"]
    assert interest_matches == []
    assert [memory.content for memory in broad_matches] == [
        "我的名字是Riley",
        "我的職業是軟體工程師",
    ]
