from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import yaml
from mcp.shared.memory import create_connected_server_and_client_session

from src.agent import BoundedAgentRuntime, build_agent_tool_registry
from src.providers import LLMProvider, LLMRequest, LLMResponse, LLMToolCall, ProviderRouter
from src.rag import RetrievalResult, RetrievedChunk
from src.repositories.memory_repository import LongTermMemorySnapshot
from src.services.execution_events import iter_answer_deltas
from src.conversation_context import (
    ConversationContextMessage,
    assemble_conversation_context,
)
from src.mcp.server import NativeMCPServer

from .golden_set import GoldenScenario, load_golden_set


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "atlas.yaml"


class ScriptedProvider(LLMProvider):
    supports_tool_calling = True

    def __init__(
        self,
        responses: Sequence[LLMResponse],
        *,
        structured_output: bool = False,
    ) -> None:
        self._responses = list(copy.deepcopy(list(responses)))
        self.requests: List[LLMRequest] = []
        self.scripted_tool_names: List[str] = []
        self.supports_structured_output = structured_output

    @property
    def name(self) -> str:
        return "eval-scripted"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("script exhausted")
        response = self._responses.pop(0)
        self.scripted_tool_names.extend(call.name for call in response.tool_calls)
        return response


class FixtureRetriever:
    def __init__(
        self,
        chunks: Sequence[RetrievedChunk],
        *,
        reject_all_evidence: bool = False,
    ) -> None:
        self.chunks = list(chunks)
        self.reject_all_evidence = reject_all_evidence
        self.calls: List[Dict[str, Any]] = []

    def retrieve_with_metadata(self, **kwargs: Any) -> RetrievalResult:
        self.calls.append(kwargs)
        source_kinds = kwargs.get("source_kinds")
        chunks = [
            chunk
            for chunk in self.chunks
            if not source_kinds or chunk.source_kind in source_kinds
        ]
        if self.reject_all_evidence:
            return RetrievalResult(
                chunks=[],
                retrieval_mode="pgvector_exact_cosine",
                retrieval_fallback_reason=None,
                candidate_count=len(chunks),
                accepted_evidence_count=0,
                best_score=0.267197,
                relevance_floor=0.30,
            )
        return RetrievalResult(
            chunks=chunks[: int(kwargs.get("top_k", 5))],
            retrieval_mode="lexical_fallback",
            retrieval_fallback_reason=None,
        )


@dataclass(frozen=True)
class SaveResult:
    status: str
    memory: LongTermMemorySnapshot


class FixtureMemoryService:
    def __init__(
        self,
        memories: Sequence[LongTermMemorySnapshot] = (),
        search_results: Sequence[Sequence[LongTermMemorySnapshot]] = (),
    ) -> None:
        self.memories = list(memories)
        self.search_results = [list(result) for result in search_results]
        self.search_calls: List[Dict[str, Any]] = []
        self.save_calls: List[Dict[str, Any]] = []

    async def search_memories(self, **kwargs: Any) -> List[LongTermMemorySnapshot]:
        self.search_calls.append(kwargs)
        memories = self.search_results.pop(0) if self.search_results else self.memories
        return list(memories[: int(kwargs.get("top_k", 5))])

    async def save_memory(self, **kwargs: Any) -> SaveResult:
        self.save_calls.append(kwargs)
        existing = next(
            (
                memory
                for memory in self.memories
                if memory.owner_id == kwargs["owner_id"]
                and memory.memory_type == kwargs["memory_type"]
                and memory.content == kwargs["content"]
            ),
            None,
        )
        if existing is not None:
            return SaveResult(status="already_saved", memory=existing)
        now = datetime.now(timezone.utc)
        memory = LongTermMemorySnapshot(
            id=len(self.memories) + 1,
            owner_id=kwargs["owner_id"],
            memory_type=kwargs["memory_type"],
            content=kwargs["content"],
            embedding_model="eval-fake",
            embedding_dimensions=1536,
            status="active",
            created_at=now,
            updated_at=now,
            score=0.99,
        )
        self.memories.append(memory)
        return SaveResult(status="saved", memory=memory)


class EventCollector:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def emit_execution_status(self, *, phase: str) -> None:
        self.events.append({"event_type": "execution_status", "phase": phase})


def run_golden_set(
    *,
    path: Optional[Path] = None,
    scenario_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    golden_set = load_golden_set(path)
    selected = set(scenario_ids) if scenario_ids is not None else None
    scenarios = [
        scenario for scenario in golden_set.scenarios
        if selected is None or scenario.id in selected
    ]
    results = [_run_scenario(scenario) for scenario in scenarios]
    passed = sum(1 for result in results if result["passed"])
    total = len(results)
    return {
        "version": golden_set.version,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "results": results,
    }


def _run_scenario(scenario: GoldenScenario) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    failure_reason: Optional[str] = None
    try:
        actual = _dispatch(scenario)
        checks = actual["checks"]
        passed = all(check["passed"] for check in checks)
        if not passed:
            failure_reason = "one or more observable contract checks failed"
    except Exception as exc:  # keep reports bounded and free of provider output
        passed = False
        failure_reason = type(exc).__name__
    return {
        "id": scenario.id,
        "category": scenario.category,
        "passed": passed,
        "checks": checks,
        "failure_reason": failure_reason,
    }


def _dispatch(scenario: GoldenScenario) -> Dict[str, Any]:
    if scenario.id in {"conversation-same-session", "session-new-chat-isolation"}:
        return _run_session_scenario(scenario)
    if scenario.id == "mcp-discovery":
        return _run_mcp_discovery()
    if scenario.id == "mcp-unauthorized-save":
        return _run_mcp_unauthorized_save()
    if scenario.id.startswith("sse-"):
        return _run_sse_scenario(scenario)
    return _run_agent_scenario(scenario)


def _run_agent_scenario(scenario: GoldenScenario) -> Dict[str, Any]:
    setup = scenario.setup
    chunks = _fixture_chunks(setup.get("evidence", "none"))
    knowledge_gate_scenario = scenario.id == "unsupported-enterprise-fact-rejected-by-knowledge-gate"
    retriever = FixtureRetriever(
        chunks,
        reject_all_evidence=knowledge_gate_scenario,
    )
    memories = _fixture_memories(setup.get("saved_memory"))
    memory_search_results: Sequence[Sequence[LongTermMemorySnapshot]] = ()
    if scenario.id == "contextual-facet-partial-memory-miss":
        memory_search_results = (memories, ())
    elif scenario.id == "contextual-facet-all-memory-miss":
        memory_search_results = ((), ())
    memory_service = FixtureMemoryService(memories, memory_search_results)
    provider = ScriptedProvider(
        _provider_script(scenario),
        structured_output=(
            scenario.id.startswith("context-requirement-")
            or scenario.id.startswith("contextual-facet-")
            or scenario.id in {
                "standalone-conversation-dependency-none",
                "repeated-standalone-dependency-none",
                "referential-substantive-dependency-required",
            }
            or knowledge_gate_scenario
        ),
    )
    runtime = _runtime(provider, retriever, memory_service)
    explicit_save = setup.get("explicit_save")
    conversation_context = None
    substantive_conversation_context = None
    substantive_conversation_reference_context = None
    if scenario.id == "standalone-conversation-dependency-none":
        conversation_context = (
            "[user] Previous substantive question\n\n"
            "[assistant] Previous grounded answer.\n\n"
            f"[user] {scenario.user_input}"
        )
        substantive_conversation_context = "[user] Previous substantive question"
    elif scenario.id == "referential-substantive-dependency-required":
        conversation_context = (
            "[user] Older task\n\n[assistant] Older answer.\n\n"
            "[user] Which practices should we compare?\n\n"
            "[assistant] The second option is controlled rollout.\n\n"
            f"[user] {scenario.user_input}"
        )
        substantive_conversation_context = (
            "[user] Older task\n\n[assistant] Older answer.\n\n"
            "[user] Which practices should we compare?"
        )
        substantive_conversation_reference_context = (
            "[user] Which practices should we compare?\n\n"
            "[assistant] The second option is controlled rollout."
        )

    def run_once(index: int, *, history: Optional[str] = None) -> Any:
        return asyncio.run(
            runtime.run(
                query=scenario.user_input,
                session_id=7,
                owner_id="demo-owner",
                provider_name=provider.name,
                model="eval-scripted-1",
                request_workflow_id=f"eval-{scenario.id}-{index}",
                conversation_context=history if history is not None else conversation_context,
                substantive_conversation_context=substantive_conversation_context,
                substantive_conversation_reference_context=(
                    substantive_conversation_reference_context
                ),
                explicit_save_allowed=isinstance(explicit_save, dict),
                explicit_save_content=(
                    explicit_save.get("content") if isinstance(explicit_save, dict) else None
                ),
                explicit_save_memory_type=(
                    explicit_save.get("memory_type") if isinstance(explicit_save, dict) else None
                ),
            )
        )

    results = []
    if scenario.id == "repeated-standalone-dependency-none":
        for index in range(4):
            history = "\n\n".join(
                (
                    *(f"[user] Prior substantive question {turn}" for turn in range(index)),
                    *(f"[assistant] Prior grounded answer {turn}" for turn in range(index)),
                    f"[user] {scenario.user_input}",
                )
            )
            results.append(run_once(index, history=history))
    else:
        results.append(run_once(0))
    result = results[-1]
    expected = scenario.expected
    checks = [
        _check(
            expected.get("expected_tool") is None
            or expected["expected_tool"] in provider.scripted_tool_names,
            "expected_tool",
            "tool selection matched" if expected.get("expected_tool") else "not required",
        ),
        _check(
            expected.get("expected_termination") is None
            or result.termination_reason.value == expected["expected_termination"],
            "termination_reason",
            result.termination_reason.value,
        ),
        _check(
            expected.get("expected_memory_use") is None
            or result.used_saved_memory is expected["expected_memory_use"],
            "used_saved_memory",
            str(result.used_saved_memory).lower(),
        ),
        _check(
            expected.get("expected_citation_kind") is None
            or any(
                citation.source_kind == expected["expected_citation_kind"]
                for citation in result.citations
            ),
            "citation_kind",
            result.citations[0].source_kind if result.citations else "none",
        ),
        _check(
            not result.insufficient_info or not result.citations,
            "insufficient_info_has_no_citations",
            str(len(result.citations)),
        ),
        _check(
            result.tool_calls_used <= int(expected.get("max_tool_calls", 3)),
            "tool_call_bound",
            str(result.tool_calls_used),
        ),
    ]
    if scenario.id in {
        "standalone-conversation-dependency-none",
        "repeated-standalone-dependency-none",
    }:
        final_requests = provider.requests[1::2]
        checks.extend(
            [
                _check(
                    all(item.status == "succeeded" and not item.insufficient_info for item in results),
                    "standalone_completion",
                    str([item.termination_reason.value for item in results]),
                ),
                _check(
                    all(item.tool_calls_used == 3 for item in results),
                    "bounded_tool_calls",
                    str([item.tool_calls_used for item in results]),
                ),
                _check(
                    bool(
                        provider.requests
                        and provider.requests[0].response_format
                        and "conversation_dependency"
                        in provider.requests[0].response_format["json_schema"]["schema"]["required"]
                    ),
                    "selector_dependency_field",
                    "required",
                ),
                _check(
                    all(
                        "Prior substantive" not in request.messages[1].content
                        and "Prior grounded" not in request.messages[1].content
                        for request in final_requests
                    ),
                    "final_history_absent",
                    "absent",
                ),
                _check(
                    len(retriever.calls) == len(results),
                    "fresh_knowledge_each_turn",
                    str(len(retriever.calls)),
                ),
                _check(
                    len(memory_service.search_calls) == len(results) * 2,
                    "fresh_memory_each_turn",
                    str(len(memory_service.search_calls)),
                ),
            ]
        )
    if scenario.id == "referential-substantive-dependency-required":
        final_request = provider.requests[-1]
        final_content = final_request.messages[1].content
        checks.extend(
            [
                _check(
                    "CONVERSATION_REFERENCE_CONTEXT" in final_content,
                    "reference_context_label",
                    "present",
                ),
                _check(
                    "Which practices should we compare?" in final_content
                    and "The second option is controlled rollout." in final_content
                    and "Older task" not in final_content,
                    "most_recent_completed_turn_only",
                    "recent pair only",
                ),
            ]
        )
    if scenario.id == "citation-pdf":
        checks.append(_check(_has_locator(result), "pdf_locator", "present"))
    if scenario.id == "citation-url":
        checks.append(_check(bool(result.citations and result.citations[0].source_url), "url_locator", "source_url present"))
    if scenario.id == "citation-image":
        checks.append(_check(_has_image_provenance(result), "image_provenance", "present"))
    if scenario.id == "memory-explicit-save":
        checks.extend(
            [
                _check(result.memory_status == "saved", "memory_saved", str(result.memory_status)),
                _check(len(memory_service.memories) == 1, "saved_memory_count", str(len(memory_service.memories))),
            ]
        )
    if scenario.id == "memory-non-explicit-protection":
        checks.append(_check(not memory_service.save_calls, "no_implicit_write", str(len(memory_service.save_calls))))
    if scenario.id == "memory-cross-session-recall":
        checks.append(_check(bool(memory_service.search_calls), "memory_search_executed", str(len(memory_service.search_calls))))
    if scenario.id == "authority-knowledge-and-memory":
        checks.extend(
            [
                _check("search_memory" in provider.scripted_tool_names, "memory_tool", "selected"),
                _check("search_knowledge" in provider.scripted_tool_names, "knowledge_tool", "selected"),
            ]
        )
    if scenario.id == "knowledge-only-does-not-force-memory":
        checks.append(
            _check(
                not memory_service.search_calls,
                "memory_not_forced",
                str(len(memory_service.search_calls)),
            )
        )
    if scenario.id == "contextual-memory-application":
        memory_search = memory_service.search_calls[0] if memory_service.search_calls else {}
        checks.extend(
            [
                _check("search_memory" in provider.scripted_tool_names, "memory_tool", "selected"),
                _check("search_knowledge" in provider.scripted_tool_names, "knowledge_tool", "selected"),
                _check(
                    memory_search.get("retrieval_mode") == "contextual",
                    "contextual_memory_mode",
                    str(memory_search.get("retrieval_mode")),
                ),
                _check(
                    "organization" in str(memory_search.get("query", "")).casefold()
                    and "production" in str(memory_search.get("query", "")).casefold(),
                    "task_oriented_memory_query",
                    "present" if memory_search else "missing",
                ),
            ]
        )
    if scenario.id == "unsupported-enterprise-fact-rejected-by-knowledge-gate":
        checks.extend(
            [
                _check(
                    len(provider.requests) == 1,
                    "final_synthesis_not_called",
                    str(len(provider.requests)),
                ),
                _check(
                    len(retriever.calls) == 1,
                    "knowledge_retrieval_executed",
                    str(len(retriever.calls)),
                ),
            ]
        )
    if scenario.id.startswith("context-requirement-") or scenario.id.startswith("contextual-facet-"):
        decision_response = provider.requests[0] if provider.requests else None
        expected_knowledge = scenario.id != "context-requirement-memory-only"
        expected_memory = scenario.id != "context-requirement-knowledge-only"
        checks.extend(
            [
                _check(
                    bool(provider.requests and provider.requests[0].response_format),
                    "structured_selector_request",
                    "present" if provider.requests and provider.requests[0].response_format else "missing",
                ),
                _check(
                    bool(retriever.calls) is expected_knowledge,
                    "knowledge_execution",
                    str(len(retriever.calls)),
                ),
                _check(
                    bool(memory_service.search_calls) is expected_memory,
                    "memory_execution",
                    str(len(memory_service.search_calls)),
                ),
                _check(
                    decision_response is not None,
                    "selector_response_recorded",
                    "present" if decision_response is not None else "missing",
                ),
            ]
        )
        if scenario.id.startswith("contextual-facet-"):
            queries = [call.get("query") for call in memory_service.search_calls]
            expected_queries = ["company size", "development preferences"]
            checks.extend(
                [
                    _check(
                        len(memory_service.search_calls) == 2,
                        "two_contextual_memory_searches",
                        str(len(memory_service.search_calls)),
                    ),
                    _check(
                        queries == expected_queries,
                        "bounded_facet_queries",
                        str(queries),
                    ),
                    _check(
                        all(
                            call.get("retrieval_mode") == "contextual"
                            for call in memory_service.search_calls
                        ),
                        "contextual_memory_mode",
                        str(
                            [
                                call.get("retrieval_mode")
                                for call in memory_service.search_calls
                            ]
                        ),
                    ),
                ]
            )
    if scenario.id == "tool-invalid-arguments":
        checks.append(_check(not retriever.calls, "retriever_not_called", str(len(retriever.calls))))
    if scenario.id == "agent-max-tool-calls":
        checks.append(_check(result.tool_calls_used == 3, "three_tool_calls", str(result.tool_calls_used)))
    return {"checks": checks}


def _run_session_scenario(scenario: GoldenScenario) -> Dict[str, Any]:
    setup = scenario.setup
    history = [
        ConversationContextMessage(role=item["role"], content=item["content"])
        for item in setup.get("history", [])
    ]
    context = assemble_conversation_context(
        history=history,
        current_question=scenario.user_input,
        max_messages=6,
        token_budget=2048,
    )
    rendered = context.rendered_text
    previous_content = setup.get("previous_session_content", "")
    expected_present = scenario.id == "conversation-same-session"
    checks = [
        _check(
            ("Friday" in rendered) is expected_present,
            "same_session_context",
            "present" if "Friday" in rendered else "absent",
        ),
        _check(scenario.user_input in rendered, "current_question_context", "present"),
        _check(
            not previous_content or previous_content not in rendered,
            "session_isolation",
            "isolated",
        ),
    ]
    return {"checks": checks}


def _run_mcp_discovery() -> Dict[str, Any]:
    server, _, _ = _build_mcp_server()

    async def probe() -> List[str]:
        async with create_connected_server_and_client_session(server.protocol_server) as client:
            await client.initialize()
            response = await client.list_tools()
            return sorted(tool.name for tool in response.tools)

    tool_names = asyncio.run(probe())
    return {
        "checks": [
            _check(
                tool_names == ["save_memory", "search_knowledge", "search_memory"],
                "mcp_allowlist",
                ",".join(tool_names),
            ),
            _check(len(tool_names) == 3, "mcp_tool_count", str(len(tool_names))),
        ]
    }


def _run_mcp_unauthorized_save() -> Dict[str, Any]:
    server, _, memory_service = _build_mcp_server()

    async def probe() -> tuple[bool, str]:
        async with create_connected_server_and_client_session(server.protocol_server) as client:
            await client.initialize()
            result = await client.call_tool(
                "save_memory",
                {"memory_type": "decision", "content": "unauthorized"},
            )
            return bool(result.isError), str((result.structuredContent or {}).get("error_code"))

    is_error, error_code = asyncio.run(probe())
    return {
        "checks": [
            _check(is_error and error_code == "permission_denied", "mcp_permission", error_code),
            _check(not memory_service.save_calls, "mcp_no_write", str(len(memory_service.save_calls))),
        ]
    }


def _run_sse_scenario(scenario: GoldenScenario) -> Dict[str, Any]:
    retriever = FixtureRetriever(_fixture_chunks(scenario.setup.get("evidence", "none")))
    memory_service = FixtureMemoryService()
    provider = ScriptedProvider(_provider_script(scenario))
    collector = EventCollector()
    result = asyncio.run(
        _runtime(provider, retriever, memory_service).run(
            query=scenario.user_input,
            session_id=7,
            owner_id="demo-owner",
            provider_name=provider.name,
            model="eval-scripted-1",
            request_workflow_id=f"eval-{scenario.id}",
            event_sink=collector,
        )
    )
    events: List[Dict[str, Any]] = list(collector.events)
    answer = ""
    for delta in iter_answer_deltas(result.answer):
        events.append({"event_type": "answer_delta", "text": delta})
        answer += delta
    if result.citations and not result.insufficient_info:
        events.append({"event_type": "citations", "count": len(result.citations)})
    events.append({"event_type": "done", "termination_reason": result.termination_reason.value})
    event_types = [event["event_type"] for event in events]
    expected_types = scenario.expected.get("sse_event_types")
    checks = [
        _check(answer == result.answer, "delta_reconstruction", "exact"),
        _check(event_types[-1] == "done", "terminal_done", event_types[-1]),
        _check(
            expected_types is None
            or all(expected in event_types for expected in expected_types),
            "event_types",
            ",".join(event_types),
        ),
        _check(
            ("citations" in event_types) is (not result.insufficient_info and bool(result.citations)),
            "citation_event_authority",
            "present" if "citations" in event_types else "absent",
        ),
    ]
    if scenario.id == "sse-insufficient-lifecycle":
        checks.append(_check("citations" not in event_types, "no_citations_event", "absent"))
    return {"checks": checks}


def _runtime(
    provider: ScriptedProvider,
    retriever: FixtureRetriever,
    memory_service: FixtureMemoryService,
) -> BoundedAgentRuntime:
    router = ProviderRouter()
    router.register_provider(provider)
    return BoundedAgentRuntime(
        provider_router=router,
        tool_registry=build_agent_tool_registry(
            retriever=retriever, embedding_client=None, memory_service=memory_service  # type: ignore[arg-type]
        ),
        max_tool_calls=3,
        max_iterations=6,
        tool_timeout_seconds=1.0,
    )


def _provider_script(scenario: GoldenScenario) -> List[LLMResponse]:
    setup = scenario.setup
    if scenario.id in {
        "standalone-conversation-dependency-none",
        "repeated-standalone-dependency-none",
    }:
        decision = {
            "needs_knowledge": True,
            "needs_memory": True,
            "conversation_dependency": "none",
            "contextual_facets": [
                {"id": "c1", "text": "company size"},
                {"id": "c2", "text": "development preferences"},
            ],
            "memory_query": None,
        }
        count = 4 if scenario.id.startswith("repeated-") else 1
        return [
            response
            for _ in range(count)
            for response in [
                LLMResponse(
                    provider="eval-scripted",
                    model="eval-scripted-1",
                    output_text="",
                    structured_output=decision,
                ),
                _final("The bounded standalone answer is grounded."),
            ]
        ]
    if scenario.id == "referential-substantive-dependency-required":
        return [
            LLMResponse(
                provider="eval-scripted",
                model="eval-scripted-1",
                output_text="",
                structured_output={
                    "needs_knowledge": True,
                    "needs_memory": False,
                    "contextual_facets": [],
                    "memory_query": None,
                    "conversation_dependency": "required",
                },
            ),
            _final("The referential answer is grounded."),
        ]
    if scenario.id.startswith("contextual-facet-"):
        decision = {
            "needs_knowledge": True,
            "needs_memory": True,
            "conversation_dependency": "none",
            "contextual_facets": [
                {"id": "c1", "text": "company size"},
                {"id": "c2", "text": "development preferences"},
            ],
        }
        return [
            LLMResponse(
                provider="eval-scripted",
                model="eval-scripted-1",
                output_text="",
                structured_output=decision,
            ),
            _final("The bounded contextual answer is grounded in Knowledge."),
        ]
    if scenario.id.startswith("context-requirement-"):
        decision = {
            "needs_knowledge": scenario.id != "context-requirement-memory-only",
            "needs_memory": scenario.id != "context-requirement-knowledge-only",
            "conversation_dependency": "none",
            "memory_query": (
                "company size and development preferences relevant to prioritization"
                if scenario.id != "context-requirement-knowledge-only"
                else None
            ),
        }
        return [
            LLMResponse(
                provider="eval-scripted",
                model="eval-scripted-1",
                output_text="",
                structured_output=decision,
            ),
            _final("The bounded context answer is grounded.")
        ]
    if scenario.id == "contextual-memory-application":
        return [
            _tool("search_knowledge", {"query": "production AI agent practices", "top_k": 5}, "knowledge-1"),
            _tool(
                "search_memory",
                {
                    "query": (
                        "organization size and development practices relevant to adopting "
                        "production AI agent practices"
                    ),
                    "top_k": 5,
                    "retrieval_mode": "contextual",
                },
                "memory-1",
            ),
            _final("Prioritize controlled tool invocation and evaluation for this organization."),
        ]
    if scenario.id == "unsupported-enterprise-fact-rejected-by-knowledge-gate":
        return [
            LLMResponse(
                provider="eval-scripted",
                model="eval-scripted-1",
                output_text="",
                structured_output={
                    "needs_knowledge": True,
                    "needs_memory": False,
                    "conversation_dependency": "none",
                    "memory_query": None,
                },
            ),
            _final("The final provider must not be called."),
        ]
    if scenario.id == "authority-knowledge-and-memory" or setup.get("provider_script") == "memory_then_knowledge":
        return [
            _tool("search_memory", {"query": scenario.user_input}, "memory-1"),
            _tool("search_knowledge", {"query": scenario.user_input, "top_k": 5}, "knowledge-1"),
            _final("Atlas uses the saved project name and PostgreSQL deployment evidence."),
        ]
    if scenario.id == "memory-explicit-save":
        explicit = setup["explicit_save"]
        return [
            _tool("save_memory", {"memory_type": explicit["memory_type"], "content": explicit["content"]}, "save-1"),
            _final("Memory saved"),
        ]
    if scenario.id == "memory-non-explicit-protection":
        return [_tool("save_memory", {"memory_type": "preference", "content": "implicit"}, "save-1")]
    if scenario.id == "memory-cross-session-recall":
        return [_tool("search_memory", {"query": scenario.user_input}, "memory-1"), _final("The saved API convention is snake_case.")]
    if scenario.id == "agent-unknown-tool":
        return [_tool("run_sql", {}, "unknown-1")]
    if scenario.id == "tool-invalid-arguments":
        return [_tool("search_knowledge", {"query": scenario.user_input, "top_k": 999}, "invalid-1")]
    if scenario.id == "agent-max-tool-calls":
        return [_tool("search_memory", {"query": f"search-{index}"}, f"loop-{index}") for index in range(4)]
    if scenario.setup.get("evidence") == "none":
        return [
            _tool("search_knowledge", {"query": scenario.user_input}, "knowledge-1"),
            _tool("search_memory", {"query": scenario.user_input}, "memory-fallback-1"),
            _final("INSUFFICIENT_INFO"),
        ]
    return [_tool("search_knowledge", {"query": scenario.user_input, "top_k": 5}, "knowledge-1"), _final("The indexed evidence answers this question.")]


def _tool(name: str, arguments: Dict[str, Any], call_id: str) -> LLMResponse:
    return LLMResponse(
        provider="eval-scripted",
        model="eval-scripted-1",
        output_text="",
        tool_calls=[LLMToolCall(id=call_id, name=name, arguments=arguments)],
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(provider="eval-scripted", model="eval-scripted-1", output_text=text)


def _fixture_chunks(evidence: str) -> List[RetrievedChunk]:
    if evidence == "none":
        return []
    payload = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [
        RetrievedChunk(
            chunk_id=index,
            chunk_index=0,
            chunk_text=item["chunk_text"],
            notion_path=item["notion_path"],
            notion_page_id=item["notion_page_id"],
            source_kind=item["source_kind"],
            score=float(item["score"]),
            source_display_name=item["source_display_name"],
            locator=item["locator"],
            citation_metadata=item.get("citation_metadata"),
            source_url=item.get("source_url"),
        )
        for index, item in enumerate(payload["knowledge"], start=1)
        if item["source_kind"] == evidence
    ]


def _fixture_memories(saved_memory: object) -> List[LongTermMemorySnapshot]:
    if not isinstance(saved_memory, dict):
        return []
    now = datetime.now(timezone.utc)
    return [
        LongTermMemorySnapshot(
            id=1,
            owner_id="demo-owner",
            memory_type=saved_memory["memory_type"],
            content=saved_memory["content"],
            embedding_model="eval-fake",
            embedding_dimensions=1536,
            status="active",
            created_at=now,
            updated_at=now,
            score=0.98,
        )
    ]


def _build_mcp_server() -> tuple[NativeMCPServer, FixtureRetriever, FixtureMemoryService]:
    retriever = FixtureRetriever([])
    memory_service = FixtureMemoryService()
    registry = build_agent_tool_registry(
        retriever=retriever, embedding_client=None, memory_service=memory_service  # type: ignore[arg-type]
    )
    return NativeMCPServer(registry=registry, owner_id="demo-owner"), retriever, memory_service


def _has_locator(result: Any) -> bool:
    return bool(result.citations and result.citations[0].locator)


def _has_image_provenance(result: Any) -> bool:
    if not result.citations:
        return False
    citation = result.citations[0]
    return citation.image_index == 1 and citation.original_filename == "atlas-architecture.png"


def _check(passed: bool, name: str, detail: str) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def write_report(report: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
