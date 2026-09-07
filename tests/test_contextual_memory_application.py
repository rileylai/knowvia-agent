from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.agent import BoundedAgentRuntime, build_agent_tool_registry
from src.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    ProviderRouter,
)
from src.rag import RetrievalResult, RetrievedChunk
from src.repositories.memory_repository import LongTermMemorySnapshot
from src.services.memory import MemoryService


CONTEXTUAL_MEMORY_GUIDANCE = "saved context would materially improve"
POST_KNOWLEDGE_DECISION_MARKER = "Before finalizing, check whether saved context affects the answer"
TASK_ORIENTED_MEMORY_QUERY = (
    "company size and development preferences relevant to production AI agent adoption"
)


class ContextualProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: List[LLMRequest] = []
        self.tool_names: List[str] = []

    @property
    def name(self) -> str:
        return "contextual-scripted"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        system = request.messages[0].content
        has_contextual_guidance = CONTEXTUAL_MEMORY_GUIDANCE in system
        if len(self.requests) == 1:
            response = _tool_call("search_knowledge", "knowledge-1")
            self.tool_names.append("search_knowledge")
            return response
        if len(self.requests) == 2 and has_contextual_guidance:
            response = _tool_call(
                "search_memory",
                "memory-1",
                query=TASK_ORIENTED_MEMORY_QUERY,
                retrieval_mode="contextual",
            )
            self.tool_names.append("search_memory")
            return response
        if len(self.requests) == 2:
            return _final_response("The PDF answer is sufficient without saved context.")
        final_context = "\n".join(message.content for message in request.messages)
        if (
            "approximately 1000 people" not in final_context
            or "SDD/TDD" not in final_context
            or "production agent" not in final_context
        ):
            return _final_response("The bounded context was incomplete.")
        return _final_response(
            "Prioritize controlled tool invocation and evaluation for this organization."
        )


class LiveLikeContextualProvider(LLMProvider):
    """Model a provider that finalizes after Knowledge unless reminded at the seam."""

    supports_tool_calling = True

    def __init__(self) -> None:
        self.requests: List[LLMRequest] = []
        self.tool_names: List[str] = []
        self.available_tool_names: List[List[str]] = []
        self.decisions: List[str] = []

    @property
    def name(self) -> str:
        return "live-like-contextual"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        self.available_tool_names.append(
            [tool["function"]["name"] for tool in request.tools or []]
        )
        if len(self.requests) == 1:
            self.decisions.append("tool_call:search_knowledge")
            response = _tool_call("search_knowledge", "knowledge-1")
            self.tool_names.append("search_knowledge")
            return response
        if len(self.requests) == 2:
            messages = "\n".join(message.content for message in request.messages)
            if POST_KNOWLEDGE_DECISION_MARKER not in messages:
                self.decisions.append("final_without_post_knowledge_contract")
                return _final_response("The PDF evidence is sufficient.")
            self.decisions.append("tool_call:search_memory")
            response = _tool_call(
                "search_memory",
                "memory-1",
                query=TASK_ORIENTED_MEMORY_QUERY,
                retrieval_mode="contextual",
            )
            self.tool_names.append("search_memory")
            return response
        self.decisions.append("final_after_contextual_memory")
        return _final_response(
            "Prioritize controlled tool invocation for this organization."
        )


class SequenceProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self, responses: List[LLMResponse]) -> None:
        self.responses = responses
        self.tool_names: List[str] = []

    @property
    def name(self) -> str:
        return "sequence-scripted"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        response = self.responses.pop(0)
        self.tool_names.extend(call.name for call in response.tool_calls)
        return response


class RecordingStatusSink:
    def __init__(self) -> None:
        self.phases: List[str] = []

    def emit_execution_status(self, *, phase: str) -> None:
        self.phases.append(phase)


class FixtureRetriever:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def retrieve_with_metadata(self, **kwargs: Any) -> RetrievalResult:
        self.calls.append(kwargs)
        return RetrievalResult(
            chunks=[
                RetrievedChunk(
                    chunk_id=1,
                    chunk_index=0,
                    chunk_text="Production agent practices require controlled tool invocation and evaluation.",
                    notion_path="Knowledge/Production Agents",
                    notion_page_id="page-1",
                    source_kind="pdf",
                    score=0.91,
                    source_display_name="production-agents.pdf",
                    locator="page 4",
                )
            ],
            retrieval_mode="fixture",
            retrieval_fallback_reason=None,
        )


class FixtureMemoryService:
    def __init__(self, memories: List[LongTermMemorySnapshot] | None = None) -> None:
        self.search_calls: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        self.memories = memories if memories is not None else [
            _memory("Our organization has approximately 1000 people.", now),
            _memory("We prefer SDD/TDD development practices.", now),
        ]

    async def search_memories(self, **kwargs: Any) -> List[LongTermMemorySnapshot]:
        self.search_calls.append(kwargs)
        return self.memories[: int(kwargs["top_k"])]


def _memory(content: str, now: datetime, *, score: float = 0.91) -> LongTermMemorySnapshot:
    return LongTermMemorySnapshot(
        id=1 if "1000" in content else 2,
        owner_id="owner-a",
        memory_type="project_context",
        content=content,
        embedding_model="fixture",
        embedding_dimensions=1536,
        status="active",
        created_at=now,
        updated_at=now,
        score=score,
    )


def _tool_call(
    name: str,
    call_id: str,
    *,
    query: str = "indexed production AI agent practices",
    retrieval_mode: str | None = None,
) -> LLMResponse:
    arguments: Dict[str, Any] = {"query": query, "top_k": 5}
    if retrieval_mode is not None:
        arguments["retrieval_mode"] = retrieval_mode
    return LLMResponse(
        provider="contextual-scripted",
        model="fixture-1",
        output_text="",
        tool_calls=[
            LLMToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
            )
        ],
    )


def _final_response(text: str) -> LLMResponse:
    return LLMResponse(
        provider="contextual-scripted",
        model="fixture-1",
        output_text=text,
    )


def _build_runtime(
    provider: LLMProvider,
    *,
    memory_service: FixtureMemoryService,
    retriever: FixtureRetriever | None = None,
) -> BoundedAgentRuntime:
    router = ProviderRouter()
    router.register_provider(provider)
    return BoundedAgentRuntime(
        provider_router=router,
        tool_registry=build_agent_tool_registry(
            retriever=retriever or FixtureRetriever(),
            embedding_client=None,
            memory_service=memory_service,  # type: ignore[arg-type]
        ),
        max_tool_calls=3,
        max_iterations=6,
    )


def test_mixed_knowledge_task_applies_relevant_saved_context_before_final_answer() -> None:
    provider = ContextualProvider()
    router = ProviderRouter()
    router.register_provider(provider)
    retriever = FixtureRetriever()
    memory_service = FixtureMemoryService()
    runtime = BoundedAgentRuntime(
        provider_router=router,
        tool_registry=build_agent_tool_registry(
            retriever=retriever,
            embedding_client=None,
            memory_service=memory_service,  # type: ignore[arg-type]
        ),
        max_tool_calls=3,
        max_iterations=6,
    )
    event_sink = RecordingStatusSink()

    result = asyncio.run(
        runtime.run(
            query=(
                "Based on the production AI agent practices in the indexed PDF, "
                "what should our company pay particular attention to when adopting them?"
            ),
            session_id=7,
            owner_id="owner-a",
            provider_name=provider.name,
            model="fixture-1",
            request_workflow_id="wf-contextual-memory",
            event_sink=event_sink,
        )
    )

    assert result.status == "succeeded"
    assert result.tool_calls_used == 2
    assert result.used_saved_memory is True
    assert provider.tool_names == [
        "search_knowledge",
        "search_memory",
    ]
    assert memory_service.search_calls == [
        {
            "owner_id": "owner-a",
            "query": TASK_ORIENTED_MEMORY_QUERY,
            "top_k": 3,
            "memory_type": None,
            "retrieval_mode": "contextual",
        }
    ]
    assert result.citations and result.citations[0].source_kind == "pdf"
    final_context = "\n".join(message.content for message in provider.requests[-1].messages)
    assert "Production agent practices require controlled tool invocation" in final_context
    assert "approximately 1000 people" in final_context
    assert "SDD/TDD" in final_context
    assert "authority=saved_memory" in provider.requests[-1].messages[-1].content
    assert "citations" not in provider.requests[-1].messages[-1].content.casefold()
    assert "searching_knowledge" in event_sink.phases
    assert "searching_memory" in event_sink.phases


def test_live_like_provider_rechecks_contextual_memory_before_finalizing() -> None:
    provider = LiveLikeContextualProvider()
    memory_service = FixtureMemoryService()

    result = asyncio.run(
        _build_runtime(provider, memory_service=memory_service).run(
            query=(
                "Considering our company size and development preferences, which practices "
                "from the indexed PDF should we prioritize?"
            ),
            session_id=7,
            owner_id="owner-a",
            provider_name=provider.name,
            model="fixture-1",
            request_workflow_id="wf-contextual-live-like",
        )
    )

    assert result.used_saved_memory is True, {
        "iteration_1_decision": provider.decisions[0],
        "iteration_2_decision": provider.decisions[1],
        "search_memory_available": "search_memory" in provider.available_tool_names[1],
        "selected_tools": provider.tool_names,
        "finalization_reason": provider.decisions[-1],
    }
    assert provider.tool_names == ["search_knowledge", "search_memory"]
    assert provider.decisions == [
        "tool_call:search_knowledge",
        "tool_call:search_memory",
        "final_after_contextual_memory",
    ]
    assert "search_memory tool remains available" in provider.requests[1].messages[0].content
    assert "Knowledge evidence is now available" in provider.requests[1].messages[0].content
    assert memory_service.search_calls[0]["query"] == TASK_ORIENTED_MEMORY_QUERY
    assert memory_service.search_calls[0]["memory_type"] is None


def test_knowledge_only_task_does_not_force_memory_lookup() -> None:
    provider = SequenceProvider([
        _tool_call("search_knowledge", "knowledge-only-1"),
        _final_response("The PDF recommends controlled tool invocation."),
    ])
    memory_service = FixtureMemoryService()
    retriever = FixtureRetriever()

    result = asyncio.run(
        _build_runtime(
            provider,
            memory_service=memory_service,
            retriever=retriever,
        ).run(
            query="What practices does the indexed PDF recommend?",
            session_id=7,
            owner_id="owner-a",
            provider_name=provider.name,
            model="fixture-1",
            request_workflow_id="wf-knowledge-only",
        )
    )

    assert result.status == "succeeded"
    assert provider.tool_names == ["search_knowledge"]
    assert retriever.calls
    assert memory_service.search_calls == []
    assert result.used_saved_memory is False
    assert result.citations and result.citations[0].source_kind == "pdf"


def test_memory_only_task_does_not_require_knowledge_lookup() -> None:
    provider = SequenceProvider([
        _tool_call("search_memory", "memory-only-1", query="What DB do we use?"),
        _final_response("Our DB is PostgreSQL."),
    ])
    memory_service = FixtureMemoryService()
    retriever = FixtureRetriever()

    result = asyncio.run(
        _build_runtime(
            provider,
            memory_service=memory_service,
            retriever=retriever,
        ).run(
            query="What DB do we use?",
            session_id=7,
            owner_id="owner-a",
            provider_name=provider.name,
            model="fixture-1",
            request_workflow_id="wf-memory-only",
        )
    )

    assert result.status == "succeeded"
    assert provider.tool_names == ["search_memory"]
    assert retriever.calls == []
    assert memory_service.search_calls[0]["owner_id"] == "owner-a"
    assert memory_service.search_calls[0]["retrieval_mode"] == "direct"
    assert result.used_saved_memory is True
    assert result.citations == []


def test_contextual_task_keeps_knowledge_answer_when_memory_has_no_relevant_match() -> None:
    provider = SequenceProvider([
        _tool_call("search_knowledge", "knowledge-1"),
        _tool_call(
            "search_memory",
            "memory-1",
            query=TASK_ORIENTED_MEMORY_QUERY,
            retrieval_mode="contextual",
        ),
        _final_response("The PDF practices apply without saved company context."),
    ])
    memory_service = FixtureMemoryService(memories=[])

    result = asyncio.run(
        _build_runtime(provider, memory_service=memory_service).run(
            query="How should our company apply this PDF?",
            session_id=7,
            owner_id="owner-a",
            provider_name=provider.name,
            model="fixture-1",
            request_workflow_id="wf-contextual-no-memory",
        )
    )

    assert result.status == "succeeded"
    assert provider.tool_names == ["search_knowledge", "search_memory"]
    assert result.used_saved_memory is False
    assert result.citations and result.citations[0].source_kind == "pdf"
    assert memory_service.search_calls[0]["retrieval_mode"] == "contextual"


class FixtureMemoryRepository:
    def __init__(self, matches: List[LongTermMemorySnapshot]) -> None:
        self.matches = matches
        self.calls: List[Dict[str, Any]] = []

    def search_by_vector(self, **kwargs: Any) -> List[LongTermMemorySnapshot]:
        self.calls.append(kwargs)
        return self.matches[: kwargs["top_k"]]


class FixtureUnitOfWork:
    def __init__(self, repository: FixtureMemoryRepository) -> None:
        self.memories = repository

    def __enter__(self) -> "FixtureUnitOfWork":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_contextual_memory_selection_keeps_existing_relevance_gate_and_owner_scope() -> None:
    now = datetime.now(timezone.utc)
    repository = FixtureMemoryRepository([
        _memory("Our organization has approximately 1000 people.", now, score=0.91),
        _memory("We prefer SDD/TDD development practices.", now, score=0.82),
        _memory("Unrelated personal preference.", now, score=0.39),
    ])
    service = MemoryService(
        unit_of_work_factory=lambda: FixtureUnitOfWork(repository),  # type: ignore[arg-type]
        embedding_client=None,
    )

    matches = asyncio.run(
        service.search_memories(
            owner_id="owner-a",
            query=TASK_ORIENTED_MEMORY_QUERY,
            query_embedding=[1.0],
            top_k=3,
            retrieval_mode="contextual",
        )
    )

    assert [memory.content for memory in matches] == [
        "Our organization has approximately 1000 people.",
        "We prefer SDD/TDD development practices.",
    ]
    assert repository.calls[0]["owner_id"] == "owner-a"
    assert repository.calls[0]["top_k"] == 3
