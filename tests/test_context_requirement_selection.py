from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agent import (
    AgentTerminationReason,
    BoundedAgentRuntime,
    ContextualFacet,
    ContextRequirementDecision,
    EvidenceReadinessDecision,
    build_agent_tool_registry,
)
from src.agent.evidence_readiness import (
    EvidenceReadinessContractError,
    build_evidence_readiness_messages,
)
from src.providers import LLMProvider, LLMRequest, LLMResponse, ProviderRouter
from src.rag import RetrievalResult, RetrievedChunk
from src.repositories.memory_repository import LongTermMemorySnapshot
from src.conversation_context import ReferenceResolverMessage
from src.db.base import Base
from src.db.models import WorkflowRun
from src.services.workflow_run_service import WorkflowRunService


class StructuredDecisionProvider(LLMProvider):
    supports_tool_calling = True
    supports_structured_output = True

    def __init__(
        self,
        decisions: List[Dict[str, Any]],
        final_outputs: Optional[List[str]] = None,
        readiness_outputs: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.decisions = list(decisions)
        self.final_outputs = list(final_outputs or ["final answer"])
        self.readiness_outputs = list(readiness_outputs or [{"ready": True}])
        self.requests: List[LLMRequest] = []

    @property
    def name(self) -> str:
        return "structured-scripted"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.response_format is not None:
            schema_name = request.response_format["json_schema"]["name"]
            if schema_name == "reference_binding_decision":
                return LLMResponse(
                    provider=self.name,
                    model=request.model,
                    output_text="",
                    structured_output={"reference_bindings": []},
                )
            if schema_name == "evidence_readiness_decision":
                readiness = dict(
                    self.readiness_outputs.pop(0)
                    if self.readiness_outputs
                    else {"ready": True}
                )
                return LLMResponse(
                    provider=self.name,
                    model=request.model,
                    output_text="",
                    structured_output=readiness,
                )
            if not self.decisions:
                raise RuntimeError("structured decision exhausted")
            decision = dict(self.decisions.pop(0))
            return LLMResponse(
                provider=self.name,
                model=request.model,
                output_text="",
                structured_output=decision,
            )
        return LLMResponse(
            provider=self.name,
            model=request.model,
            output_text=self.final_outputs.pop(0) if self.final_outputs else "final answer",
        )


class HistorySensitiveStructuredProvider(LLMProvider):
    supports_tool_calling = True
    supports_structured_output = True

    def __init__(self) -> None:
        self.requests: List[LLMRequest] = []

    @property
    def name(self) -> str:
        return "history-sensitive-structured"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.response_format is not None:
            schema_name = request.response_format["json_schema"]["name"]
            if schema_name == "reference_binding_decision":
                return LLMResponse(
                    provider=self.name,
                    model=request.model,
                    output_text="",
                    structured_output={"reference_bindings": []},
                )
            if schema_name == "evidence_readiness_decision":
                return LLMResponse(
                    provider=self.name,
                    model=request.model,
                    output_text="",
                    structured_output={"ready": True},
                )
            return LLMResponse(
                provider=self.name,
                model=request.model,
                output_text="",
                structured_output={
                    "needs_knowledge": True,
                    "needs_memory": True,
                    "contextual_facets": [
                        {"id": "c1", "text": "company size"},
                        {"id": "c2", "text": "development preferences"},
                    ],
                    "memory_query": None,
                },
            )
        final_user_message = request.messages[1].content
        output = (
            "INSUFFICIENT_INFO"
            if "[assistant]" in final_user_message
            else "The fresh authorities support this answer."
        )
        return LLMResponse(
            provider=self.name,
            model=request.model,
            output_text=output,
        )


class ReadinessFailureProvider(StructuredDecisionProvider):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        if (
            request.response_format is not None
            and request.response_format["json_schema"]["name"]
            == "evidence_readiness_decision"
        ):
            raise RuntimeError("readiness provider failed")
        return await super().generate(request)


class ReferenceBindingProvider(LLMProvider):
    supports_tool_calling = True
    supports_structured_output = True

    def __init__(
        self,
        *,
        reference_bindings: List[Dict[str, Any]],
        decision: Dict[str, Any],
        final_output: str = "The fresh authorities support this answer.",
    ) -> None:
        self.reference_bindings = reference_bindings
        self.decision = decision
        self.final_output = final_output
        self.requests: List[LLMRequest] = []
        self.structured_outputs: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "reference-binding-scripted"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.response_format is not None:
            schema_name = request.response_format["json_schema"]["name"]
            output = (
                {"reference_bindings": self.reference_bindings}
                if schema_name == "reference_binding_decision"
                else {"ready": True}
                if schema_name == "evidence_readiness_decision"
                else dict(self.decision)
            )
            self.structured_outputs.append(output)
            return LLMResponse(
                provider=self.name,
                model=request.model,
                output_text="",
                structured_output=output,
            )
        return LLMResponse(
            provider=self.name,
            model=request.model,
            output_text=self.final_output,
        )


class FixtureRetriever:
    def __init__(
        self,
        chunks: Optional[List[RetrievedChunk]] = None,
        *,
        candidate_count: Optional[int] = None,
        accepted_evidence_count: Optional[int] = None,
        best_score: Optional[float] = None,
        relevance_floor: Optional[float] = None,
    ) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.chunks = chunks if chunks is not None else [_chunk()]
        self._has_explicit_counts = (
            candidate_count is not None or accepted_evidence_count is not None
        )
        self.candidate_count = (
            len(self.chunks) if candidate_count is None else candidate_count
        )
        self.accepted_evidence_count = (
            len(self.chunks)
            if accepted_evidence_count is None
            else accepted_evidence_count
        )
        self.best_score = best_score
        self.relevance_floor = relevance_floor

    def retrieve_with_metadata(self, **kwargs: Any) -> RetrievalResult:
        self.calls.append(kwargs)
        result_kwargs: Dict[str, Any] = {}
        if self._has_explicit_counts:
            result_kwargs = {
                "candidate_count": self.candidate_count,
                "accepted_evidence_count": self.accepted_evidence_count,
            }
        if self.best_score is not None:
            result_kwargs["best_score"] = self.best_score
        if self.relevance_floor is not None:
            result_kwargs["relevance_floor"] = self.relevance_floor
        return RetrievalResult(
            chunks=self.chunks,
            retrieval_mode="fixture",
            retrieval_fallback_reason=None,
            **result_kwargs,
        )


class FixtureMemoryService:
    def __init__(
        self,
        memories: Optional[List[LongTermMemorySnapshot]] = None,
        search_results: Optional[List[List[LongTermMemorySnapshot]]] = None,
    ) -> None:
        self.search_calls: List[Dict[str, Any]] = []
        self.memories = memories if memories is not None else [_memory("PostgreSQL")]
        self.search_results = list(search_results or [])

    async def search_memories(self, **kwargs: Any) -> List[LongTermMemorySnapshot]:
        self.search_calls.append(kwargs)
        memories = self.search_results.pop(0) if self.search_results else self.memories
        return memories[: int(kwargs["top_k"])]


class RecordingStatusSink:
    def __init__(self) -> None:
        self.phases: List[str] = []

    def emit_execution_status(self, *, phase: str) -> None:
        self.phases.append(phase)


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=1,
        chunk_index=0,
        chunk_text="The PDF describes controlled production AI agent practices.",
        notion_path="Knowledge/Production Agents",
        notion_page_id="page-1",
        source_kind="pdf",
        score=0.91,
        source_display_name="production-agents.pdf",
        locator="page 4",
    )


def _memory(content: str) -> LongTermMemorySnapshot:
    now = datetime.now(timezone.utc)
    return LongTermMemorySnapshot(
        id=1,
        owner_id="owner-a",
        memory_type="project_context",
        content=content,
        embedding_model="fixture",
        embedding_dimensions=1536,
        status="active",
        created_at=now,
        updated_at=now,
        score=0.9,
    )


def _runtime(
    provider: StructuredDecisionProvider,
    *,
    memories: Optional[List[LongTermMemorySnapshot]] = None,
    memory_search_results: Optional[List[List[LongTermMemorySnapshot]]] = None,
    chunks: Optional[List[RetrievedChunk]] = None,
    candidate_count: Optional[int] = None,
    accepted_evidence_count: Optional[int] = None,
    best_score: Optional[float] = None,
    relevance_floor: Optional[float] = None,
    max_tool_calls: int = 3,
    workflow_run_service: Optional[WorkflowRunService] = None,
) -> tuple[BoundedAgentRuntime, FixtureRetriever, FixtureMemoryService]:
    router = ProviderRouter()
    router.register_provider(provider)
    retriever = FixtureRetriever(
        chunks,
        candidate_count=candidate_count,
        accepted_evidence_count=accepted_evidence_count,
        best_score=best_score,
        relevance_floor=relevance_floor,
    )
    memory_service = FixtureMemoryService(memories, memory_search_results)
    runtime = BoundedAgentRuntime(
        provider_router=router,
        tool_registry=build_agent_tool_registry(
            retriever=retriever,
            embedding_client=None,
            memory_service=memory_service,  # type: ignore[arg-type]
        ),
        max_tool_calls=max_tool_calls,
        workflow_run_service=workflow_run_service,
    )
    return runtime, retriever, memory_service


def _run(
    runtime: BoundedAgentRuntime,
    query: str,
    *,
    owner_id: str = "owner-a",
    provider_name: str = "structured-scripted",
    conversation_context: Optional[str] = None,
    reference_resolver_history: Optional[List[Any]] = None,
    current_sequence_number: Optional[int] = None,
    history_message_count: Optional[int] = None,
    history_roles: Optional[List[str]] = None,
) -> Any:
    return asyncio.run(
        runtime.run(
            query=query,
            session_id=7,
            owner_id=owner_id,
            provider_name=provider_name,
            model="fixture-1",
            request_workflow_id="wf-context-selection",
            conversation_context=conversation_context,
            reference_resolver_history=reference_resolver_history,
            current_sequence_number=current_sequence_number,
            history_message_count=history_message_count,
            history_roles=history_roles,
        )
    )


def _workflow_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[WorkflowRun.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_context_requirement_decision_validates_bounded_contract() -> None:
    decision = ContextRequirementDecision(
        needs_knowledge=True,
        needs_memory=True,
        contextual_facets=[
            ContextualFacet(id="c1", text="company size"),
            ContextualFacet(id="c2", text="development preferences"),
        ],
    )

    assert decision.model_dump() == {
        "needs_knowledge": True,
        "needs_memory": True,
        "contextual_facets": [
            {"id": "c1", "text": "company size"},
            {"id": "c2", "text": "development preferences"},
        ],
        "memory_query": None,
    }
    assert "steps" not in ContextRequirementDecision.model_json_schema()["properties"]


def test_evidence_readiness_decision_is_strict_and_minimal() -> None:
    decision = EvidenceReadinessDecision.model_validate({"ready": True})

    assert decision.model_dump() == {"ready": True}
    schema = EvidenceReadinessDecision.response_format()["json_schema"]
    assert schema["name"] == "evidence_readiness_decision"
    assert schema["strict"] is True
    assert schema["schema"]["required"] == ["ready"]
    assert set(schema["schema"]["properties"]) == {"ready"}

    with pytest.raises(ValidationError):
        EvidenceReadinessDecision.model_validate({"ready": "true"})
    with pytest.raises(ValidationError):
        EvidenceReadinessDecision.model_validate({"ready": True, "reasoning": "enough"})


def test_readiness_input_keeps_memory_as_optional_dependency_and_not_knowledge_evidence() -> None:
    messages = build_evidence_readiness_messages(
        task="Which indexed practice fits our company?",
        reference_bindings=[],
        accepted_evidence=[
            {
                "id": "K1",
                "text": "The indexed practice requires a staged rollout.",
                "source_kind": "pdf",
                "source_display_name": "controls.pdf",
                "locator": "page 2",
            }
        ],
        memory_dependencies={
            "needs_memory": True,
            "facet_labels": ["company size"],
        },
    )

    readiness_input = "\n".join(message.content for message in messages)
    readiness_system = messages[0].content
    assert "Which indexed practice fits our company?" in readiness_input
    assert "ACCEPTED_KNOWLEDGE_EVIDENCE" in readiness_input
    assert "MEMORY_SIDE_DEPENDENCIES" in readiness_input
    assert "company size" in readiness_input
    assert "memory_dependencies_resolved" not in readiness_input
    assert "previous assistant answer" not in readiness_input
    assert "secret memory content" not in readiness_input
    assert "at least one materially complete, correct, grounded answer" in readiness_system
    assert "does not require exhaustive source coverage" in readiness_system
    assert "every query phrase" in readiness_system
    assert "must not be treated as missing Knowledge requirements" in readiness_system
    assert "Memory cannot satisfy or replace a missing Knowledge-backed claim" in readiness_system
    assert "memory_dependencies_resolved" not in readiness_system
    assert "optional supplemental context" in readiness_system
    for case_id in ("rv-001", "rv-009", "rv-027", "rv-029"):
        assert case_id not in readiness_system

    with pytest.raises(EvidenceReadinessContractError):
        build_evidence_readiness_messages(
            task="Which indexed practice fits our company?",
            reference_bindings=[],
            accepted_evidence=[{"text": "The indexed practice requires a staged rollout."}],
            memory_dependencies={
                "needs_memory": True,
                "facet_labels": ["company size"],
                "content": "secret memory content",
            },
        )

def test_knowledge_only_readiness_input_has_no_memory_resolution_requirement() -> None:
    messages = build_evidence_readiness_messages(
        task="What practice does the indexed PDF describe?",
        reference_bindings=[],
        accepted_evidence=[{"text": "The indexed practice uses staged rollout."}],
        memory_dependencies={"needs_memory": False, "facet_labels": []},
    )

    readiness_user = messages[1].content
    assert '"needs_memory": false' in readiness_user
    assert "memory_dependencies_resolved" not in readiness_user


def test_sufficient_single_evidence_runs_readiness_then_final_synthesis() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        ["The evidence supports this answer."],
        [{"ready": True}],
    )
    runtime, _, _ = _runtime(provider)

    result = _run(runtime, "What practice does the indexed PDF describe?")

    assert result.status == "succeeded"
    assert result.insufficient_info is False
    assert result.citations
    assert [request.response_format["json_schema"]["name"] for request in provider.requests[:3]] == [
        "reference_binding_decision",
        "context_requirement_decision",
        "evidence_readiness_decision",
    ]
    assert provider.requests[3].response_format is None


def test_partial_multi_evidence_is_not_ready_and_skips_final_synthesis() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        [],
        [{"ready": False}],
    )
    runtime, _, _ = _runtime(provider, chunks=[_chunk(), _chunk()])

    result = _run(runtime, "Which two controls are required by the indexed material?")

    assert result.status == "succeeded"
    assert result.insufficient_info is True
    assert result.citations == []
    assert len(provider.requests) == 3
    assert all(request.response_format is not None for request in provider.requests)


def test_complete_multi_evidence_runs_final_synthesis() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        ["The complete evidence supports this answer."],
        [{"ready": True}],
    )
    runtime, _, _ = _runtime(provider, chunks=[_chunk(), _chunk()])

    result = _run(runtime, "Which controls are described across the indexed sources?")

    assert result.insufficient_info is False
    assert result.citations
    assert provider.requests[-1].response_format is None


def test_malformed_readiness_output_fails_closed_without_final_synthesis() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        ["The final provider must not be called."],
        [{"ready": "true"}],
    )
    runtime, _, _ = _runtime(provider)

    result = _run(runtime, "What does the indexed material require?")

    assert result.status == "failed"
    assert result.termination_reason == AgentTerminationReason.PROVIDER_CONTRACT_ERROR
    assert len(provider.requests) == 3


def test_readiness_provider_failure_uses_provider_error_without_insufficient_info() -> None:
    provider = ReadinessFailureProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        ["The final provider must not be called."],
    )
    runtime, _, _ = _runtime(provider)

    result = _run(runtime, "What does the indexed material require?")

    assert result.status == "failed"
    assert result.insufficient_info is False
    assert result.termination_reason == AgentTerminationReason.PROVIDER_ERROR
    assert len(provider.requests) == 2


def test_memory_cannot_rescue_missing_knowledge_readiness() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [{"id": "c1", "text": "company size"}],
            }
        ],
        ["The final provider must not be called."],
        [{"ready": False}],
    )
    runtime, _, memory_service = _runtime(
        provider,
        chunks=[],
        candidate_count=1,
        accepted_evidence_count=0,
        memory_search_results=[[_memory("Our organization has 1000 people.")]],
    )

    result = _run(runtime, "Which indexed practice fits our company?")

    assert result.insufficient_info is True
    assert result.citations == []
    assert provider.requests[-1].response_format is not None
    assert len(memory_service.search_calls) == 1


def test_zero_accepted_evidence_bypasses_readiness_provider() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        [],
        [{"ready": False}],
    )
    runtime, _, _ = _runtime(
        provider,
        chunks=[],
        candidate_count=2,
        accepted_evidence_count=0,
    )

    result = _run(runtime, "What does the indexed material require?")

    assert result.insufficient_info is True
    assert result.citations == []
    assert len(provider.requests) == 2


def test_context_requirement_decision_rejects_malformed_memory_requirement() -> None:
    with pytest.raises(ValidationError):
        ContextRequirementDecision(
            needs_knowledge=False,
            needs_memory=True,
            memory_query="   ",
        )


def test_context_requirement_decision_rejects_conversation_dependency_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ContextRequirementDecision.model_validate(
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
                "conversation_dependency": "none",
            }
        )


def test_context_requirement_decision_accepts_bounded_contextual_facets() -> None:
    decision = ContextRequirementDecision(
        needs_knowledge=True,
        needs_memory=True,
        contextual_facets=[
            ContextualFacet(id="c1", text="company size"),
            ContextualFacet(id="c2", text="development preferences"),
        ],
    )

    assert decision.model_dump() == {
        "needs_knowledge": True,
        "needs_memory": True,
        "contextual_facets": [
            {"id": "c1", "text": "company size"},
            {"id": "c2", "text": "development preferences"},
        ],
        "memory_query": None,
    }
    assert "contextual_facets" in ContextRequirementDecision.model_json_schema()[
        "properties"
    ]
    wire_schema = ContextRequirementDecision.response_format()["json_schema"]["schema"]
    assert set(wire_schema["required"]) == {
        "needs_knowledge",
        "needs_memory",
        "contextual_facets",
        "memory_query",
    }
    assert "default" not in wire_schema["properties"]["memory_query"]


def test_contextual_facets_reject_unbounded_or_non_atomic_shapes() -> None:
    with pytest.raises(ValidationError):
        ContextRequirementDecision(
            needs_knowledge=True,
            needs_memory=True,
            contextual_facets=[
                ContextualFacet(id="c1", text="company size"),
                ContextualFacet(id="c2", text="development preferences"),
                ContextualFacet(id="c3", text="deployment constraints"),
            ],
        )

    with pytest.raises(ValidationError):
        ContextualFacet(id="c1", text="   ")

    with pytest.raises(ValidationError):
        ContextualFacet(id="c1", text="x" * 121)

    with pytest.raises(ValidationError):
        ContextualFacet(
            id="c1",
            text="company size and development preferences relevant to prioritization",
        )

    with pytest.raises(ValidationError):
        ContextualFacet(id="c1", text="search saved memory for company information")

    with pytest.raises(ValidationError):
        ContextualFacet.model_validate(
            {"id": "c1", "text": "company size", "search_memory": True}
        )

    with pytest.raises(ValidationError):
        ContextRequirementDecision(
            needs_knowledge=True,
            needs_memory=True,
            contextual_facets=[ContextualFacet(id="c1", text="company size")],
            memory_query="company context",
        )


def test_mixed_contextual_facets_execute_each_memory_query_within_three_calls() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                    {"id": "c2", "text": "development preferences"},
                ],
            }
        ],
        ["The PDF practices fit the saved company context."],
    )
    runtime, retriever, memory_service = _runtime(
        provider,
        memories=[_memory("Our organization has approximately 1000 people."), _memory("We prefer SDD/TDD.")],
    )

    result = _run(
        runtime,
        "Considering our company size and development preferences, which practices from the indexed PDF should we prioritize?",
    )

    assert result.status == "succeeded"
    assert result.tool_calls_used == 3
    assert len(retriever.calls) == 1
    assert [call["query"] for call in memory_service.search_calls] == [
        "company size",
        "development preferences",
    ]
    assert all(call["retrieval_mode"] == "contextual" for call in memory_service.search_calls)
    assert result.used_saved_memory is True
    readiness_request = next(
        request
        for request in provider.requests
        if request.response_format
        and request.response_format["json_schema"]["name"] == "evidence_readiness_decision"
    )
    assert "memory_dependencies_resolved" not in readiness_request.messages[1].content
    final_context = "\n".join(message.content for message in provider.requests[-1].messages)
    assert final_context.count("approximately 1000 people") == 1
    assert final_context.count("We prefer SDD/TDD.") == 1


def test_mixed_contextual_facets_allow_knowledge_only_fallback_when_one_memory_dependency_is_unresolved() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                    {"id": "c2", "text": "development preferences"},
                ],
            }
        ],
        ["The grounded answer uses the available company context."],
    )
    runtime, _, memory_service = _runtime(
        provider,
        memory_search_results=[[_memory("Our organization has approximately 1000 people.")], []],
    )

    result = _run(runtime, "Which indexed PDF practices should our company prioritize?")

    assert result.status == "succeeded"
    assert result.insufficient_info is False
    assert result.citations
    assert result.used_saved_memory is True
    assert len(memory_service.search_calls) == 2
    assert len(provider.requests) == 4
    assert "memory_dependencies_resolved" not in provider.requests[2].messages[1].content


def test_mixed_contextual_facets_resolve_duplicate_memory_record_per_facet() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                    {"id": "c2", "text": "development preferences"},
                ],
            }
        ],
        ["The grounded answer uses the saved company context."],
    )
    runtime, _, memory_service = _runtime(
        provider,
        memories=[
            _memory(
                "Our organization has approximately 1000 people and prefers SDD/TDD."
            )
        ],
    )

    result = _run(
        runtime,
        "Considering our company size and development preferences, which indexed PDF practices should we prioritize?",
    )

    assert result.insufficient_info is False
    assert result.used_saved_memory is True
    assert len(memory_service.search_calls) == 2
    readiness_request = next(
        request
        for request in provider.requests
        if request.response_format
        and request.response_format["json_schema"]["name"] == "evidence_readiness_decision"
    )
    assert "memory_dependencies_resolved" not in readiness_request.messages[1].content
    assert provider.requests[-1].response_format is None


def test_mixed_contextual_facets_allow_knowledge_only_fallback_when_all_memory_dependencies_are_unresolved() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                    {"id": "c2", "text": "development preferences"},
                ],
            }
        ],
        ["The answer is grounded in the indexed PDF."],
    )
    runtime, _, memory_service = _runtime(
        provider,
        memory_search_results=[[], []],
    )

    result = _run(runtime, "Which indexed PDF practices should our company prioritize?")

    assert result.status == "succeeded"
    assert result.insufficient_info is False
    assert result.citations
    assert result.used_saved_memory is False
    assert len(memory_service.search_calls) == 2
    assert len(provider.requests) == 4
    assert "memory_dependencies_resolved" not in provider.requests[2].messages[1].content

def test_context_selector_marks_previous_answer_as_non_authority() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                ],
            }
        ],
        ["The grounded answer."],
    )
    runtime, _, _ = _runtime(provider)

    result = _run(
        runtime,
        "Considering our company size, which practices should we prioritize?",
        conversation_context=(
            "[user] What practices should we prioritize?\n\n"
            "[assistant] The previous answer said to prioritize controlled rollout."
        ),
    )

    assert result.status == "succeeded"
    selector_request = provider.requests[1]
    selector_system = selector_request.messages[0].content
    assert "The exact current user message is authoritative" in selector_system
    assert "previous assistant content is not Knowledge evidence" in selector_system
    assert "previous assistant mention of saved facts is not current LongTermMemory retrieval" in selector_system
    assert "Do not set a requirement false merely because the relevant fact appeared in a previous assistant response" in selector_system
    assert "[assistant]" not in selector_request.messages[1].content


def test_repeated_substantive_query_reacquires_both_authorities() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                ],
            },
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                ],
            },
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                ],
            },
        ],
        [
            "The first grounded answer.",
            "The second grounded answer.",
            "The third grounded answer.",
        ],
    )
    runtime, retriever, memory_service = _runtime(provider)
    query = "Considering our company size, which practices should we prioritize?"
    second_context = "[user] What practices should we prioritize?\n\n[assistant] The first grounded answer."
    third_context = (
        f"{second_context}\n\n[user] {query}\n\n"
        "[assistant] The second grounded answer."
    )

    first = _run(runtime, query)
    second = _run(runtime, query, conversation_context=second_context)
    third = _run(runtime, query, conversation_context=third_context)

    assert first.status == second.status == third.status == "succeeded"
    assert first.insufficient_info is False
    assert second.insufficient_info is False
    assert third.insufficient_info is False
    assert len(retriever.calls) == 3
    assert len(memory_service.search_calls) == 3
    assert [request.response_format is not None for request in provider.requests] == [
        True,
        True,
        True,
        False,
        True,
        True,
        True,
        False,
        True,
        True,
        True,
        False,
    ]


def test_repeated_substantive_final_synthesis_excludes_previous_assistant() -> None:
    provider = HistorySensitiveStructuredProvider()
    router = ProviderRouter()
    router.register_provider(provider)
    retriever = FixtureRetriever()
    memory_service = FixtureMemoryService(
        memories=[
            _memory("A bounded company-size fixture."),
            _memory("A bounded development-preference fixture."),
        ]
    )
    runtime = BoundedAgentRuntime(
        provider_router=router,
        tool_registry=build_agent_tool_registry(
            retriever=retriever,
            embedding_client=None,
            memory_service=memory_service,  # type: ignore[arg-type]
        ),
        max_tool_calls=3,
    )
    query = (
        "Considering our company size and development preferences, "
        "which practices from the indexed PDF should we prioritize?"
    )
    contexts = [
        None,
        f"[user] {query}\n\n[assistant] The first grounded answer.",
        (
            f"[user] {query}\n\n[assistant] The first grounded answer.\n\n"
            f"[user] {query}\n\n[assistant] The second grounded answer."
        ),
    ]
    results = [
        asyncio.run(
            runtime.run(
                query=query,
                session_id=7,
                owner_id="owner-a",
                provider_name=provider.name,
                model="fixture-1",
                request_workflow_id=f"wf-authority-isolation-{index}",
                conversation_context=context,
            )
        )
        for index, context in enumerate(
            contexts,
            start=1,
        )
    ]

    assert all(result.status == "succeeded" for result in results)
    assert all(result.insufficient_info is False for result in results)
    assert all(
        result.termination_reason == AgentTerminationReason.COMPLETED
        for result in results
    )
    assert all(result.tool_calls_used == 3 for result in results)
    assert len(retriever.calls) == 3
    assert len(memory_service.search_calls) == 6
    selector_requests = provider.requests[1::4]
    final_requests = provider.requests[3::4]
    assert all("[assistant]" not in request.messages[1].content for request in selector_requests)
    assert all("[assistant]" not in request.messages[1].content for request in final_requests)
    assert all(query in request.messages[1].content for request in final_requests)
    assert final_requests[1].messages[1].content.count(query) == 1
    assert final_requests[2].messages[1].content.count(query) == 1
    assert all("KNOWLEDGE_CONTEXT" in request.messages[-1].content for request in final_requests)
    assert all("MEMORY_CONTEXT" in request.messages[-1].content for request in final_requests)
    assert all(
        "Previous assistant answers are not current Knowledge evidence"
        in request.messages[-1].content
        for request in final_requests
    )
    assert all(
        "Synthesize the current substantive task anew"
        in request.messages[-1].content
        for request in final_requests
    )


def test_structured_knowledge_only_executes_knowledge_once_without_memory() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        ["The PDF answer is grounded."],
    )
    runtime, retriever, memory_service = _runtime(provider)

    result = _run(runtime, "What practices are described in the indexed PDF?")

    assert result.status == "succeeded"
    assert len(retriever.calls) == 1
    assert memory_service.search_calls == []
    assert result.citations and result.citations[0].source_kind == "pdf"
    assert result.used_saved_memory is False
    assert len(provider.requests) == 4
    assert provider.requests[0].response_format["json_schema"]["name"] == "reference_binding_decision"
    assert provider.requests[1].response_format["json_schema"]["name"] == "context_requirement_decision"
    assert provider.requests[3].tools is None
    assert provider.requests[3].response_format is None


def test_structured_memory_only_executes_memory_once_without_knowledge() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": False,
                "needs_memory": True,
                "memory_query": "company database",
            }
        ],
        ["Our database is PostgreSQL."],
    )
    runtime, retriever, memory_service = _runtime(provider)

    result = _run(runtime, "What DB do we use?")

    assert result.status == "succeeded"
    assert retriever.calls == []
    assert len(memory_service.search_calls) == 1
    assert memory_service.search_calls[0]["query"] == "company database"
    assert memory_service.search_calls[0]["retrieval_mode"] == "direct"
    assert result.citations == []
    assert result.used_saved_memory is True


def test_structured_mixed_execution_is_deterministic_and_uses_both_contexts() -> None:
    decision = {
        "needs_knowledge": True,
        "needs_memory": True,
        "contextual_facets": [
            {"id": "c1", "text": "company size"},
            {"id": "c2", "text": "development preferences"},
        ],
    }
    provider = StructuredDecisionProvider([decision, decision, decision], ["answer"] * 3)
    runtime, retriever, memory_service = _runtime(
        provider,
        memories=[
            _memory("Our organization has approximately 1000 people."),
            _memory("We prefer SDD/TDD."),
        ],
    )

    for _ in range(3):
        result = _run(
            runtime,
            (
                "Considering our company size and development preferences, which PDF "
                "practices should we prioritize?"
            ),
        )
        assert result.status == "succeeded"
        assert result.used_saved_memory is True

    assert len(retriever.calls) == 3
    assert len(memory_service.search_calls) == 6
    assert all(call["retrieval_mode"] == "contextual" for call in memory_service.search_calls)
    assert [call["query"] for call in memory_service.search_calls] == [
        "company size",
        "development preferences",
    ] * 3
    assert all(call["owner_id"] == "owner-a" for call in memory_service.search_calls)
    final_context = provider.requests[-1].messages[-1].content
    assert "KNOWLEDGE_CONTEXT" in final_context
    assert "MEMORY_CONTEXT" in final_context
    assert "controlled production AI agent practices" in final_context
    assert "PostgreSQL" not in final_context
    assert "SDD/TDD" in final_context


def test_mixed_local_scope_keeps_knowledge_and_memory_boundaries_separate() -> None:
    decision = {
        "needs_knowledge": True,
        "needs_memory": True,
        "contextual_facets": [{"id": "c1", "text": "company size"}],
    }
    provider = StructuredDecisionProvider([decision], ["answer"])
    local_memory = replace(
        _memory("Our organization has approximately 1000 people."),
        owner_id="local",
    )
    runtime, retriever, memory_service = _runtime(
        provider,
        memories=[local_memory],
    )

    result = _run(
        runtime,
        "Based on the indexed PDF, what should an organization our size prioritize?",
        owner_id="local",
    )

    assert result.status == "succeeded"
    assert retriever.calls[0]["owner_scope"] == "local"
    assert memory_service.search_calls[0]["owner_id"] == "local"
    assert result.citations
    assert result.used_saved_memory is True


def test_knowledge_hit_memory_no_hit_allows_knowledge_only_final_synthesis() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                ],
            }
        ],
        ["Answer from the PDF only."],
    )
    runtime, retriever, memory_service = _runtime(provider, memories=[])

    result = _run(runtime, "Based on the PDF, what should we prioritize?")

    assert result.status == "succeeded"
    assert retriever.calls
    assert memory_service.search_calls
    assert result.insufficient_info is False
    assert result.citations
    assert result.used_saved_memory is False
    assert len(provider.requests) == 4


def test_knowledge_miss_memory_hit_cannot_substitute_enterprise_evidence() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                ],
            }
        ],
        ["A memory-based answer that must be rejected."],
    )
    runtime, _, _ = _runtime(
        provider,
        memories=[_memory("Our company has 1000 people.")],
        chunks=[],
    )

    result = _run(runtime, "Based on the indexed PDF, what should our company prioritize?")

    assert result.status == "succeeded"
    assert result.insufficient_info is True
    assert result.citations == []
    assert result.used_saved_memory is False


def test_structured_decision_fails_closed_before_any_tool_on_malformed_output() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": "yes",
                "needs_memory": False,
                "memory_query": None,
            }
        ]
    )
    runtime, retriever, memory_service = _runtime(provider)

    result = _run(runtime, "What is in the indexed PDF?")

    assert result.status == "failed"
    assert result.termination_reason == AgentTerminationReason.PROVIDER_ERROR
    assert retriever.calls == []
    assert memory_service.search_calls == []
    assert len(provider.requests) == 2


def test_mixed_required_context_fails_closed_when_tool_budget_is_one() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                    {"id": "c2", "text": "development preferences"},
                ],
            }
        ]
    )
    runtime, retriever, memory_service = _runtime(provider, max_tool_calls=1)

    result = _run(runtime, "Which PDF practices fit our company?")

    assert result.status == "failed"
    assert result.termination_reason == AgentTerminationReason.MAX_TOOL_CALLS
    assert retriever.calls == []
    assert memory_service.search_calls == []
    assert len(provider.requests) == 2


def test_structured_context_execution_emits_each_search_and_one_generation() -> None:
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                    {"id": "c2", "text": "development preferences"},
                ],
            }
        ]
    )
    runtime, _, _ = _runtime(provider)
    sink = RecordingStatusSink()

    asyncio.run(
        runtime.run(
            query="Which PDF practices fit our company?",
            session_id=7,
            owner_id="owner-a",
            provider_name="structured-scripted",
            model="fixture-1",
            request_workflow_id="wf-context-selection-sse",
            event_sink=sink,
        )
    )

    assert sink.phases == ["searching_knowledge", "searching_memory", "generating"]


def test_workflow_metadata_projects_bounded_context_execution_without_content() -> None:
    workflow_service = WorkflowRunService(_workflow_session_factory())
    decision = {
        "needs_knowledge": True,
        "needs_memory": True,
        "contextual_facets": [
            {"id": "c1", "text": "private context"},
        ],
    }
    assistant_text = "private assistant answer that must not be persisted"
    memory_text = "private saved memory that must not be persisted"
    provider = StructuredDecisionProvider([decision], [assistant_text])
    runtime, _, _ = _runtime(
        provider,
        memories=[_memory(memory_text)],
        workflow_run_service=workflow_service,
    )
    query = "private user query that must not be persisted"
    knowledge_text = _chunk().chunk_text

    result = _run(
        runtime,
        query,
        conversation_context="[user] earlier question\n\n[assistant] earlier answer",
        history_message_count=3,
        history_roles=["user", "assistant", "user"],
    )

    workflow = workflow_service.get_workflow_run(result.workflow_run_id)
    assert workflow is not None
    metadata = json.loads(workflow.metadata_json)
    serialized_metadata = workflow.metadata_json
    assert metadata["history_message_count"] == 3
    assert metadata["history_roles"] == ["user", "assistant", "user"]
    assert metadata["context_requirement"] == {
        "needs_knowledge": True,
        "needs_memory": True,
        "contextual_facet_count": 1,
        "memory_query_present": False,
    }
    assert metadata["tool_names_used"] == ["search_knowledge", "search_memory"]
    assert metadata["retrieved_chunk_count"] == 1
    assert metadata["memory_retrieval_hit_count"] == 1
    assert metadata["memory_retrieval_mode"] == "contextual"
    assert metadata["memory_required_dependency_count"] == 1
    assert metadata["memory_resolved_dependency_count"] == 1
    assert metadata["memory_dependencies_resolved"] is True
    assert metadata["used_saved_memory"] is True
    assert metadata["provider_termination_type"] == "final_text"
    assert metadata["termination_reason"] == "completed"
    assert metadata["conversation_transform"] is False
    assert metadata["insufficient_info_source"] is None
    for private_value in (
        query,
        "earlier question",
        "earlier answer",
        assistant_text,
        memory_text,
        "private context",
        knowledge_text,
    ):
        assert private_value not in serialized_metadata


def test_same_session_workflow_ids_are_independently_queryable() -> None:
    workflow_service = WorkflowRunService(_workflow_session_factory())
    decision = {
        "needs_knowledge": True,
        "needs_memory": False,
        "memory_query": None,
    }
    provider = StructuredDecisionProvider(
        [decision, decision], ["turn one", "turn two"]
    )
    runtime, _, _ = _runtime(provider, workflow_run_service=workflow_service)
    query = "repeat this bounded query"

    turn_one = _run(
        runtime,
        query,
        history_message_count=1,
        history_roles=["user"],
    )
    turn_two = _run(
        runtime,
        query,
        conversation_context="[user] repeat this bounded query\n\n[assistant] turn one",
        history_message_count=3,
        history_roles=["user", "assistant", "user"],
    )

    assert turn_one.workflow_run_id > 0
    assert turn_two.workflow_run_id > 0
    assert turn_one.workflow_run_id != turn_two.workflow_run_id
    first_workflow = workflow_service.get_workflow_run(turn_one.workflow_run_id)
    second_workflow = workflow_service.get_workflow_run(turn_two.workflow_run_id)
    assert first_workflow is not None
    assert second_workflow is not None
    assert json.loads(first_workflow.metadata_json)["history_roles"] == ["user"]
    assert json.loads(second_workflow.metadata_json)["history_roles"] == [
        "user",
        "assistant",
        "user",
    ]


def test_insufficient_info_source_distinguishes_backend_guard_and_provider_sentinel() -> None:
    workflow_service = WorkflowRunService(_workflow_session_factory())
    guard_provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company context"},
                ],
            }
        ],
        ["The provider answer must be rejected."],
    )
    guard_runtime, _, _ = _runtime(
        guard_provider,
        memories=[_memory("saved company context")],
        chunks=[],
        workflow_run_service=workflow_service,
    )
    guarded_result = _run(guard_runtime, "What should the company prioritize?")
    guarded_workflow = workflow_service.get_workflow_run(guarded_result.workflow_run_id)
    assert guarded_workflow is not None
    guarded_metadata = json.loads(guarded_workflow.metadata_json)
    assert guarded_result.insufficient_info is True
    assert guarded_result.provider is None
    assert guarded_result.used_saved_memory is False
    assert len(guard_provider.requests) == 2
    assert guarded_metadata["insufficient_info_source"] == "knowledge_required_but_missing"

    sentinel_provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        ["INSUFFICIENT_INFO"],
    )
    sentinel_runtime, _, _ = _runtime(
        sentinel_provider,
        chunks=[_chunk()],
        workflow_run_service=workflow_service,
    )
    sentinel_result = _run(sentinel_runtime, "What does the indexed PDF say?")
    sentinel_workflow = workflow_service.get_workflow_run(
        sentinel_result.workflow_run_id
    )
    assert sentinel_workflow is not None
    sentinel_metadata = json.loads(sentinel_workflow.metadata_json)
    assert sentinel_result.status == "failed"
    assert sentinel_result.termination_reason == AgentTerminationReason.PROVIDER_CONTRACT_ERROR
    assert sentinel_metadata["insufficient_info_source"] == "provider_sentinel"


def test_raw_knowledge_candidates_with_no_accepted_evidence_are_insufficient() -> None:
    workflow_service = WorkflowRunService(_workflow_session_factory())
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        ["The provider answer must be rejected."],
    )
    runtime, _, _ = _runtime(
        provider,
        chunks=[],
        candidate_count=2,
        accepted_evidence_count=0,
        workflow_run_service=workflow_service,
    )

    result = _run(runtime, "What does the indexed PDF say?")

    workflow = workflow_service.get_workflow_run(result.workflow_run_id)
    assert workflow is not None
    metadata = json.loads(workflow.metadata_json)
    assert result.status == "succeeded"
    assert result.insufficient_info is True
    assert metadata["knowledge_candidate_count"] == 2
    assert metadata["knowledge_accepted_evidence_count"] == 0
    assert metadata["knowledge_context_count"] == 0
    assert metadata["insufficient_info_source"] == "knowledge_required_but_missing"


def test_knowledge_gate_short_circuits_final_provider_when_no_evidence_is_accepted() -> None:
    workflow_service = WorkflowRunService(_workflow_session_factory())
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": False,
                "memory_query": None,
            }
        ],
        ["The final provider must not be called."],
    )
    runtime, _, _ = _runtime(
        provider,
        chunks=[],
        candidate_count=2,
        accepted_evidence_count=0,
        best_score=0.267197,
        relevance_floor=0.30,
        workflow_run_service=workflow_service,
    )

    result = _run(runtime, "What is our 2027 acquisition budget?")
    workflow = workflow_service.get_workflow_run(result.workflow_run_id)
    assert workflow is not None
    metadata = json.loads(workflow.metadata_json)

    assert result.status == "succeeded"
    assert result.insufficient_info is True
    assert result.citations == []
    assert result.provider is None
    assert len(provider.requests) == 2
    assert metadata["knowledge_candidate_count"] == 2
    assert metadata["knowledge_accepted_evidence_count"] == 0
    assert metadata["knowledge_context_count"] == 0
    assert metadata["knowledge_best_score"] == pytest.approx(0.267197)
    assert metadata["knowledge_relevance_floor"] == pytest.approx(0.30)


def test_accepted_knowledge_and_memory_provider_sentinel_is_contract_failure() -> None:
    workflow_service = WorkflowRunService(_workflow_session_factory())
    provider = StructuredDecisionProvider(
        [
            {
                "needs_knowledge": True,
                "needs_memory": True,
                "contextual_facets": [
                    {"id": "c1", "text": "company size"},
                ],
            }
        ],
        ["INSUFFICIENT_INFO"],
    )
    runtime, _, _ = _runtime(
        provider,
        chunks=[_chunk()],
        memories=[_memory("Our company prefers controlled rollout.")],
        workflow_run_service=workflow_service,
    )

    result = _run(runtime, "Which practices should we prioritize?")

    workflow = workflow_service.get_workflow_run(result.workflow_run_id)
    assert workflow is not None
    metadata = json.loads(workflow.metadata_json)
    assert result.status == "failed"
    assert result.insufficient_info is False
    assert result.termination_reason == AgentTerminationReason.PROVIDER_CONTRACT_ERROR
    assert metadata["knowledge_accepted_evidence_count"] == 1
    assert metadata["provider_termination_type"] == "insufficient_info"
    assert metadata["insufficient_info_source"] == "provider_sentinel"
    assert workflow.failure_reason == "LLM_OUTPUT_INVALID"


def test_repeated_self_contained_request_keeps_selector_and_final_free_of_raw_history() -> None:
    query = (
        "Considering our company size and development preferences, "
        "which practices from the indexed PDF should we prioritize?"
    )
    provider = ReferenceBindingProvider(
        reference_bindings=[],
        decision={
            "needs_knowledge": True,
            "needs_memory": True,
            "contextual_facets": [
                {"id": "c1", "text": "company size"},
                {"id": "c2", "text": "development preferences"},
            ],
            "memory_query": None,
        },
    )
    router = ProviderRouter()
    router.register_provider(provider)
    retriever = FixtureRetriever()
    memory_service = FixtureMemoryService(
        memories=[_memory("company context"), _memory("development context")]
    )
    runtime = BoundedAgentRuntime(
        provider_router=router,
        tool_registry=build_agent_tool_registry(
            retriever=retriever,
            embedding_client=None,
            memory_service=memory_service,  # type: ignore[arg-type]
        ),
        max_tool_calls=3,
    )

    for index in range(4):
        result = _run(
            runtime,
            query,
            provider_name=provider.name,
            reference_resolver_history=[
                ReferenceResolverMessage(
                    message_id=10 + index,
                    sequence_number=1 + index,
                    role="assistant",
                    content="Incidental previous answer must stay resolver-only.",
                )
            ],
            current_sequence_number=2 + index,
        )
        assert result.status == "succeeded"
        assert result.insufficient_info is False

    resolver_requests = provider.requests[0::4]
    selector_requests = provider.requests[1::4]
    final_requests = provider.requests[3::4]
    assert len(resolver_requests) == len(selector_requests) == len(final_requests) == 4
    assert provider.structured_outputs[0::3] == [
        {"reference_bindings": []}
    ] * 4
    assert all(query in request.messages[1].content for request in selector_requests)
    assert all(query in request.messages[1].content for request in final_requests)
    assert all("Incidental previous answer" not in request.messages[1].content for request in selector_requests)
    assert all("Incidental previous answer" not in request.messages[1].content for request in final_requests)
    assert all("[assistant]" not in request.messages[1].content for request in selector_requests)
    assert all("[assistant]" not in request.messages[1].content for request in final_requests)
    assert len(retriever.calls) == 4
    assert len(memory_service.search_calls) == 8


def test_referential_request_uses_only_backend_validated_binding_for_context_and_retrieval() -> None:
    provider = ReferenceBindingProvider(
        reference_bindings=[
            {
                "current_span": "the second one",
                "source_message_id": 41,
                "source_span": "Tool Boundaries",
            }
        ],
        decision={
            "needs_knowledge": True,
            "needs_memory": False,
            "contextual_facets": [],
            "memory_query": None,
        },
    )
    runtime, retriever, _ = _runtime(provider, chunks=[_chunk()])
    history = [
        ReferenceResolverMessage(
            message_id=40,
            sequence_number=1,
            role="user",
            content="Which practices should we compare?",
        ),
        ReferenceResolverMessage(
            message_id=41,
            sequence_number=2,
            role="assistant",
            content="1. Deterministic Control Flow\n2. Tool Boundaries",
        ),
    ]

    result = _run(
        runtime,
        "What about the second one?",
        provider_name=provider.name,
        reference_resolver_history=history,
        current_sequence_number=3,
    )

    assert result.status == "succeeded"
    assert result.insufficient_info is False
    assert retriever.calls[0]["query_text"] == (
        "What about the second one?\nReference: Tool Boundaries"
    )
    selector_request = provider.requests[1]
    final_request = provider.requests[3]
    assert "What about the second one?" in selector_request.messages[1].content
    assert "Tool Boundaries" in selector_request.messages[1].content
    assert "Deterministic Control Flow" not in selector_request.messages[1].content
    assert "1. Deterministic Control Flow" not in final_request.messages[1].content
    assert "2. Tool Boundaries" not in final_request.messages[1].content
    assert '"current_span": "the second one"' in final_request.messages[-1].content
    assert "CONVERSATION_REFERENCE_CONTEXT" not in "\n".join(
        message.content for message in final_request.messages
    )


def test_validated_reference_binding_cannot_rescue_missing_knowledge() -> None:
    provider = ReferenceBindingProvider(
        reference_bindings=[
            {
                "current_span": "the second one",
                "source_message_id": 41,
                "source_span": "Tool Boundaries",
            }
        ],
        decision={
            "needs_knowledge": True,
            "needs_memory": False,
            "contextual_facets": [],
            "memory_query": None,
        },
    )
    runtime, retriever, memory_service = _runtime(provider, chunks=[])

    result = _run(
        runtime,
        "What about the second one?",
        provider_name=provider.name,
        reference_resolver_history=[
            ReferenceResolverMessage(
                message_id=41,
                sequence_number=2,
                role="assistant",
                content="Tool Boundaries",
            )
        ],
        current_sequence_number=3,
    )

    assert result.status == "succeeded"
    assert result.insufficient_info is True
    assert result.citations == []
    assert result.used_saved_memory is False
    assert len(retriever.calls) == 1
    assert memory_service.search_calls == []
    assert len(provider.requests) == 2
