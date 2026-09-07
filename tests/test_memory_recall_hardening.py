from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agent import BoundedAgentRuntime, build_agent_tool_registry
from src.db.base import Base
from src.db.models import ConversationMessage, ConversationSession, LongTermMemory
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.memory import is_memory_recall_query
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
from src.repositories.memory_repository import LongTermMemorySnapshot
from src.services import memory as memory_service_module
from src.services.memory import MemoryService


class ConceptEmbeddingClient(EmbeddingClient):
    def __init__(self) -> None:
        self.inputs: List[str] = []

    @property
    def name(self) -> str:
        return "concept-fake"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.inputs.extend(request.inputs)
        return EmbeddingResponse(
            provider=self.name,
            model="concept-fake-model",
            embeddings=[self._vector(value) for value in request.inputs],
        )

    @staticmethod
    def _vector(value: str) -> List[float]:
        normalized = value.casefold()
        vector = [0.0] * 1536
        if any(marker in normalized for marker in ("database", "datastore", "資料庫")):
            vector[0] = 1.0
        if "db" in normalized:
            vector[1] = 1.0
        if any(marker in normalized for marker in ("postgresql", "postgres")):
            vector[2] = 1.0
        if "frontend" in normalized or "framework" in normalized:
            vector[3] = 1.0
        if "company size" in normalized or "公司規模" in normalized:
            vector[4] = 1.0
        return vector


def _session_factory():
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
            LongTermMemory.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _memory(content: str) -> LongTermMemorySnapshot:
    now = datetime.now(timezone.utc)
    return LongTermMemorySnapshot(
        id=1,
        owner_id="owner-a",
        memory_type="project_context",
        content=content,
        retrieval_text=content,
        embedding_model="fake",
        embedding_dimensions=1536,
        status="active",
        created_at=now,
        updated_at=now,
        score=0.9,
    )


class MemoryFixtureService:
    def __init__(self, memory: LongTermMemorySnapshot) -> None:
        self.memory = memory
        self.search_calls: List[Dict[str, Any]] = []

    async def search_memories(self, **kwargs: Any) -> List[LongTermMemorySnapshot]:
        self.search_calls.append(kwargs)
        return [self.memory]


class PromptDrivenProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: List[LLMRequest] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            if (
                "Use search_memory when the answer may be an explicitly saved personal or project fact"
                not in request.messages[0].content
            ):
                return LLMResponse(
                    provider=self.name,
                    model="scripted-1",
                    output_text="INSUFFICIENT_INFO",
                )
            return LLMResponse(
                provider=self.name,
                model="scripted-1",
                output_text="",
                tool_calls=[
                    LLMToolCall(
                        id="memory-call-1",
                        name="search_memory",
                        arguments={"query": "what database do we use?"},
                    )
                ],
            )
        return LLMResponse(
            provider=self.name,
            model="scripted-1",
            output_text="Our DB uses PostgreSQL.",
        )


@pytest.mark.parametrize(
    "query",
    (
        "what database do we use?",
        "what frontend framework do we use?",
        "what is our company size?",
    ),
)
def test_generic_and_enterprise_questions_remain_outside_deterministic_memory_classifier(
    query: str,
) -> None:
    assert is_memory_recall_query(query) is False


def test_explicit_save_keeps_original_content_and_embeds_retrieval_representation() -> None:
    session_factory = _session_factory()
    embedding_client = ConceptEmbeddingClient()
    service = MemoryService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        embedding_client=embedding_client,
    )

    result = asyncio.run(
        service.save_memory(
            owner_id="owner-a",
            content="our DB uses PostgreSQL.",
            memory_type="project_context",
        )
    )

    assert result.memory.content == "our DB uses PostgreSQL."
    assert result.memory.retrieval_text == "our database (DB) uses PostgreSQL."
    assert embedding_client.inputs[0] == result.memory.retrieval_text


def test_natural_paraphrase_and_abbreviation_queries_recall_saved_memory_without_negative_hits() -> None:
    session_factory = _session_factory()
    embedding_client = ConceptEmbeddingClient()
    service = MemoryService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        embedding_client=embedding_client,
    )
    asyncio.run(
        service.save_memory(
            owner_id="owner-a",
            content="our DB uses PostgreSQL.",
            memory_type="project_context",
        )
    )

    queries = (
        "what DB do we use?",
        "what database do we use?",
        "我們 DB 用什麼？",
        "我們公司的 database 是什麼？",
        "what datastore do we use?",
    )
    for query in queries:
        matches = asyncio.run(service.search_memories(owner_id="owner-a", query=query))
        assert [memory.content for memory in matches] == ["our DB uses PostgreSQL."]

    for query in ("what frontend framework do we use?", "what is our company size?"):
        assert asyncio.run(service.search_memories(owner_id="owner-a", query=query)) == []


def test_retrieval_representation_failure_falls_back_to_original_content(
    monkeypatch,
) -> None:
    session_factory = _session_factory()
    embedding_client = ConceptEmbeddingClient()
    service = MemoryService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        embedding_client=embedding_client,
    )

    def fail_normalization(_: str) -> str:
        raise RuntimeError("normalizer unavailable")

    monkeypatch.setattr(
        memory_service_module,
        "normalize_memory_retrieval_text",
        fail_normalization,
    )
    result = asyncio.run(
        service.save_memory(
            owner_id="owner-a",
            content="our DB uses PostgreSQL.",
            memory_type="project_context",
        )
    )

    assert result.memory.retrieval_text == "our DB uses PostgreSQL."
    assert embedding_client.inputs[0] == "our DB uses PostgreSQL."


def test_agent_routes_natural_project_fact_question_to_memory_search() -> None:
    provider = PromptDrivenProvider()
    router = ProviderRouter()
    router.register_provider(provider)
    memory_service = MemoryFixtureService(_memory("Our DB uses PostgreSQL."))
    registry = build_agent_tool_registry(
        retriever=type(
            "EmptyRetriever",
            (),
            {"retrieve_with_metadata": lambda self, **_: type("Result", (), {"chunks": []})()},
        )(),
        embedding_client=None,
        memory_service=memory_service,
    )
    runtime = BoundedAgentRuntime(provider_router=router, tool_registry=registry)

    result = asyncio.run(
        runtime.run(
            query="what database do we use?",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-memory-recall-hardening",
        )
    )

    assert result.status == "succeeded"
    assert result.used_saved_memory is True
    assert result.answer == "Our DB uses PostgreSQL."
    assert len(provider.requests) == 2
    assert provider.requests[1].messages[-1].name == "search_memory"
    assert memory_service.search_calls[0]["query"] == "what database do we use?"
