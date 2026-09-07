from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from src.agent import (
    AgentTerminationReason,
    BoundedAgentRuntime,
    KnowledgeSearchTool,
    MemorySearchTool,
    MemorySaveTool,
    build_agent_tool_registry,
)
from src.providers import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    ProviderRouter,
)
from src.rag import RetrievalResult, RetrievedChunk
from src.repositories.memory_repository import LongTermMemorySnapshot


class ScriptedProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(self, responses: List[LLMResponse]) -> None:
        self.responses = responses
        self.requests: List[LLMRequest] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self.responses:
            raise RuntimeError("script exhausted")
        return self.responses.pop(0)


class TimelineProvider(ScriptedProvider):
    def __init__(self, responses: List[LLMResponse], timeline: List[str]) -> None:
        super().__init__(responses)
        self.timeline = timeline

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.timeline.append(f"provider-{len(self.requests) + 1}")
        return await super().generate(request)


class RecordingStatusSink:
    def __init__(self, timeline: List[str]) -> None:
        self.timeline = timeline

    def emit_execution_status(self, *, phase: str) -> None:
        self.timeline.append(f"status-{phase}")


class FakeRetriever:
    def __init__(self, chunks: List[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: List[Dict[str, Any]] = []

    def retrieve_with_metadata(self, **kwargs: Any) -> RetrievalResult:
        self.calls.append(kwargs)
        return RetrievalResult(
            chunks=self.chunks,
            retrieval_mode="lexical_fallback",
            retrieval_fallback_reason=None,
        )


class FakeMemoryService:
    def __init__(self, memories: Optional[List[LongTermMemorySnapshot]] = None) -> None:
        self.memories = memories or []
        self.saved: List[Dict[str, Any]] = []

    async def search_memories(self, **kwargs: Any) -> List[LongTermMemorySnapshot]:
        return self.memories

    async def save_memory(self, **kwargs: Any):
        self.saved.append(kwargs)
        memory = LongTermMemorySnapshot(
            id=1,
            owner_id=kwargs["owner_id"],
            memory_type=kwargs["memory_type"],
            content=kwargs["content"],
            embedding_model="fake",
            embedding_dimensions=1536,
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        return type("SaveResult", (), {"status": "saved", "memory": memory})()


class SlowMemoryService(FakeMemoryService):
    async def search_memories(self, **kwargs: Any) -> List[LongTermMemorySnapshot]:
        await asyncio.sleep(0.05)
        return []


def _provider_router(responses: List[LLMResponse]) -> tuple[ProviderRouter, ScriptedProvider]:
    provider = ScriptedProvider(responses)
    router = ProviderRouter()
    router.register_provider(provider)
    return router, provider


def _runtime(
    *,
    responses: List[LLMResponse],
    memories: Optional[List[LongTermMemorySnapshot]] = None,
    chunks: Optional[List[RetrievedChunk]] = None,
    max_tool_calls: int = 3,
    max_iterations: int = 6,
    tool_timeout_seconds: float = 1.0,
    context_char_budget: int = 16000,
):
    router, provider = _provider_router(responses)
    memory_service = FakeMemoryService(memories)
    registry = build_agent_tool_registry(
        retriever=FakeRetriever(chunks or []),
        embedding_client=None,
        memory_service=memory_service,
    )
    runtime = BoundedAgentRuntime(
        provider_router=router,
        tool_registry=registry,
        max_tool_calls=max_tool_calls,
        max_iterations=max_iterations,
        tool_timeout_seconds=tool_timeout_seconds,
        context_char_budget=context_char_budget,
    )
    return runtime, provider, memory_service


def _call(name: str, arguments: Dict[str, Any], call_id: str = "call-1") -> LLMResponse:
    return LLMResponse(
        provider="scripted",
        model="scripted-1",
        output_text="",
        tool_calls=[LLMToolCall(id=call_id, name=name, arguments=arguments)],
    )


def _memory(content: str) -> LongTermMemorySnapshot:
    return LongTermMemorySnapshot(
        id=1,
        owner_id="owner-a",
        memory_type="project_context",
        content=content,
        embedding_model="fake",
        embedding_dimensions=1536,
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        score=0.9,
    )


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=1,
        chunk_index=0,
        chunk_text="The production workflow uses a bounded agent.",
        notion_path="Knowledge/Runtime/Agent",
        notion_page_id="page-1",
        source_kind="pdf",
        score=0.91,
        source_display_name="agent.pdf",
        locator="page 2",
    )


def test_agent_registry_exposes_only_allowlisted_tools_and_rejects_unknown() -> None:
    runtime, _, _ = _runtime(responses=[])

    assert runtime.tool_registry.list_tool_names() == [
        "save_memory",
        "search_knowledge",
        "search_memory",
    ]
    with pytest.raises(Exception, match="Tool is not allowed"):
        asyncio.run(
            runtime.tool_registry.call_tool(
                "delete_memory",
                context=runtime.tool_context(owner_id="owner-a"),
                arguments={},
            )
        )


def test_invalid_tool_arguments_fail_before_adapter_execution() -> None:
    runtime, _, _ = _runtime(responses=[])

    result = asyncio.run(
        runtime.tool_registry.call_tool(
            "search_knowledge",
            context=runtime.tool_context(owner_id="owner-a"),
            arguments={"query": "x", "top_k": 999},
        )
    )

    assert result.is_error is True
    assert result.error_code == "invalid_arguments"


def test_search_memory_rejects_unsupported_memory_type() -> None:
    runtime, _, _ = _runtime(responses=[])

    result = asyncio.run(
        runtime.tool_registry.call_tool(
            "search_memory",
            context=runtime.tool_context(owner_id="owner-a"),
            arguments={"query": "What are my preferences?", "memory_type": "fact"},
        )
    )

    assert result.is_error is True
    assert result.error_code == "invalid_arguments"


def test_agent_chains_memory_then_knowledge_and_keeps_authorities_separate() -> None:
    runtime, provider, _ = _runtime(
        responses=[
            _call("search_memory", {"query": "What did we decide?"}),
            _call("search_knowledge", {"query": "What does the production note say?"}, "call-2"),
            LLMResponse(
                provider="scripted",
                model="scripted-1",
                output_text="The production note confirms the bounded agent.",
            ),
        ],
        memories=[_memory("We decided to use a bounded agent.")],
        chunks=[_chunk()],
    )

    result = asyncio.run(
        runtime.run(
            query="What does the production note say?",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-1",
        )
    )

    assert result.status == "succeeded"
    assert result.termination_reason == AgentTerminationReason.COMPLETED
    assert result.tool_calls_used == 2
    assert result.used_saved_memory is True
    assert result.citations[0].source_kind == "pdf"
    assert len(provider.requests) == 3
    assert provider.requests[1].messages[-1].role == "tool"
    assert "saved_memory" in provider.requests[1].messages[-1].content


def test_generating_status_precedes_final_provider_generation() -> None:
    timeline: List[str] = []
    provider = TimelineProvider(
        [
            _call("search_knowledge", {"query": "bounded workflow"}),
            LLMResponse(
                provider="scripted",
                model="scripted-1",
                output_text="The production workflow uses a bounded agent.",
            ),
        ],
        timeline,
    )
    router = ProviderRouter()
    router.register_provider(provider)
    runtime = BoundedAgentRuntime(
        provider_router=router,
        tool_registry=build_agent_tool_registry(
            retriever=FakeRetriever([_chunk()]),
            embedding_client=None,
            memory_service=FakeMemoryService(),
        ),
    )

    result = asyncio.run(
        runtime.run(
            query="What is the bounded workflow?",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-generating",
            event_sink=RecordingStatusSink(timeline),
        )
    )

    assert result.status == "succeeded"
    assert timeline.index("status-searching_knowledge") < timeline.index("status-generating")
    assert timeline.index("status-generating") < timeline.index("provider-2")


def test_non_explicit_save_is_rejected_even_when_provider_requests_it() -> None:
    runtime, _, memory_service = _runtime(
        responses=[_call("save_memory", {"memory_type": "decision", "content": "secret"})]
    )

    result = asyncio.run(
        runtime.run(
            query="We use pgvector in production.",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-2",
        )
    )

    assert result.status == "failed"
    assert result.termination_reason == AgentTerminationReason.PERMISSION_DENIED
    assert memory_service.saved == []


def test_fourth_tool_call_is_not_executed() -> None:
    runtime, _, _ = _runtime(
        responses=[
            _call("search_memory", {"query": "a"}, "call-1"),
            _call("search_memory", {"query": "b"}, "call-2"),
            _call("search_memory", {"query": "c"}, "call-3"),
            _call("search_memory", {"query": "d"}, "call-4"),
        ],
    )

    result = asyncio.run(
        runtime.run(
            query="remember",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-3",
        )
    )

    assert result.status == "failed"
    assert result.tool_calls_used == 3
    assert result.termination_reason == AgentTerminationReason.MAX_TOOL_CALLS


def test_max_iterations_terminates_without_a_final_answer() -> None:
    runtime, _, _ = _runtime(
        responses=[_call("search_memory", {"query": "a"})],
        max_iterations=1,
    )

    result = asyncio.run(
        runtime.run(
            query="remember",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-4",
        )
    )

    assert result.status == "failed"
    assert result.termination_reason == AgentTerminationReason.MAX_ITERATIONS


def test_no_knowledge_evidence_returns_insufficient_info_without_citations() -> None:
    runtime, _, _ = _runtime(
        responses=[
            _call("search_knowledge", {"query": "unsupported question"}),
            LLMResponse(
                provider="scripted",
                model="scripted-1",
                output_text="A model guess that must be rejected.",
            ),
        ],
        chunks=[],
    )

    result = asyncio.run(
        runtime.run(
            query="unsupported question",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-5",
        )
    )

    assert result.status == "succeeded"
    assert result.insufficient_info is True
    assert result.citations == []


def test_explicit_save_tool_reuses_backend_save_policy() -> None:
    runtime, _, memory_service = _runtime(
        responses=[
            _call(
                "save_memory",
                {
                    "memory_type": "decision",
                    "content": "We use pgvector in production.",
                },
            ),
            LLMResponse(
                provider="scripted",
                model="scripted-1",
                output_text="Memory saved",
            ),
        ]
    )

    result = asyncio.run(
        runtime.run(
            query="Remember that we use pgvector in production.",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-6",
            explicit_save_allowed=True,
            explicit_save_content="We use pgvector in production.",
            explicit_save_memory_type="decision",
        )
    )

    assert result.status == "succeeded"
    assert result.memory_status == "saved"
    assert result.used_saved_memory is False
    assert memory_service.saved[0]["owner_id"] == "owner-a"


def test_explicit_save_recovers_from_provider_memory_type_drift() -> None:
    runtime, _, memory_service = _runtime(
        responses=[
            _call(
                "save_memory",
                {
                    "memory_type": "company",
                    "content": "provider supplied content",
                },
            ),
            LLMResponse(
                provider="scripted",
                model="scripted-1",
                output_text="Memory saved",
            ),
        ]
    )

    result = asyncio.run(
        runtime.run(
            query="記住我的公司叫做Knowvia",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-7",
            explicit_save_allowed=True,
            explicit_save_content="我的公司叫做Knowvia",
            explicit_save_memory_type="project_context",
        )
    )

    assert result.status == "succeeded"
    assert result.memory_status == "saved"
    assert memory_service.saved[0]["memory_type"] == "project_context"
    assert memory_service.saved[0]["content"] == "我的公司叫做Knowvia"


def test_tool_timeout_terminates_without_retry() -> None:
    router, provider = _provider_router([_call("search_memory", {"query": "slow"})])
    memory_service = SlowMemoryService()
    registry = build_agent_tool_registry(
        retriever=FakeRetriever([]),
        embedding_client=None,
        memory_service=memory_service,
    )
    runtime = BoundedAgentRuntime(
        provider_router=router,
        tool_registry=registry,
        tool_timeout_seconds=0.001,
    )

    result = asyncio.run(
        runtime.run(
            query="slow",
            session_id=7,
            owner_id="owner-a",
            provider_name="scripted",
            model="scripted-1",
            request_workflow_id="wf-agent-7",
        )
    )

    assert result.status == "failed"
    assert result.termination_reason == AgentTerminationReason.TOOL_TIMEOUT
    assert len(provider.requests) == 1
