from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.agent import BoundedAgentRuntime, build_agent_tool_registry
from src.agent.models import (
    ContextRequirementDecision,
    ContextRequirementWireDecision,
    EvidenceReadinessDecision,
    ReferenceBindingDecision,
    map_context_requirement_wire_decision,
)
from src.db.models import KnowledgeChunk
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.orchestrators.qa_orchestrator import DEFAULT_QA_MODEL, DEFAULT_QA_PROVIDER_NAME
from src.providers import (
    LLMProvider,
    LLMClientError,
    LLMRequest,
    LLMResponse,
    OpenAIClient,
    OpenAIEmbeddingClient,
    ProviderRouter,
)
from src.rag import (
    KNOWLEDGE_RELEVANCE_FLOOR,
    KNOWLEDGE_VECTOR_CANDIDATE_POOL_MAX,
    ProductionChunkRetriever,
    RetrievalResult,
)
from src.repositories.chunk_repository import ChunkRepository
from src.repositories.source_document_repository import SourceDocumentRepository
from src.services.memory import MemoryService


EXPECTED_OUTCOMES = frozenset(
    {"SHOULD_ANSWER", "SHOULD_FAIL_CLOSED", "REVIEW_REQUIRED"}
)
CLASSIFICATIONS = (
    "completed",
    "selector_failure",
    "retrieval_failure",
    "acceptance_failure",
    "evidence_coverage_failure",
    "readiness_false_negative",
    "final_synthesis_failure",
    "expected_insufficient",
    "invalid_test_setup",
    "provider_failure",
)
FORBIDDEN_REPORT_FIELDS = frozenset(
    {
        "api_key",
        "chain_of_thought",
        "chunk_text",
        "database_url",
        "embedding_input",
        "full_source_text",
        "hidden_prompt",
        "prompt",
        "raw_provider_response",
        "raw_response",
        "reasoning_text",
        "vector",
    }
)


class DiagnosticReportError(ValueError):
    pass


class DiagnosticSetError(ValueError):
    pass


def classify_safe_provider_error(exc: BaseException) -> str:
    """Map provider exceptions to bounded categories without retaining messages."""
    text_value = str(exc).casefold()
    if isinstance(exc, LLMClientError):
        if "429" in text_value or "rate limit" in text_value:
            return "rate_limit_failure"
        if "timeout" in text_value or "timed out" in text_value:
            return "provider_timeout"
        if "response schema is invalid" in text_value or "structured output" in text_value:
            return "structured_output_parse_failure"
        if "request failed" in text_value:
            return "transient_transport_failure"
        return "provider_contract_failure"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "provider_timeout"
    return "unresolved_provider_failure"


def _safe_value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return "unknown"


def _safe_shape_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {
            "returned_top_level_fields": [],
            "field_presence": {},
            "returned_top_level_types": {},
        }
    fields = sorted(str(key) for key in value.keys() if isinstance(key, str))
    metadata: dict[str, object] = {
        "returned_top_level_fields": fields,
        "field_presence": {field: field in value for field in (
            "needs_knowledge",
            "needs_memory",
            "contextual_facets",
            "memory_query",
        )},
        "returned_top_level_types": {
            field: _safe_value_type(value.get(field)) for field in fields
        },
    }
    facets = value.get("contextual_facets")
    if isinstance(facets, list):
        metadata["contextual_facets_count"] = len(facets)
    return metadata


def build_safe_contract_fingerprint(
    exc: ValidationError,
    returned: object,
) -> dict[str, object]:
    errors = exc.errors()
    first = errors[0] if errors else {}
    raw_loc = first.get("loc", ()) if isinstance(first, Mapping) else ()
    loc = raw_loc if isinstance(raw_loc, tuple) else tuple(raw_loc or ())
    field_path = ".".join(str(part) for part in loc) or None
    validation_error_type = str(first.get("type") or "unknown")
    if validation_error_type == "missing":
        validation_rule = "missing"
    elif validation_error_type == "extra_forbidden":
        validation_rule = "extra_forbidden"
    elif validation_error_type in {"bool_type", "string_type", "list_type"}:
        validation_rule = validation_error_type
    elif validation_error_type in {"string_too_long", "too_long"}:
        validation_rule = (
            "too_many_items" if field_path == "contextual_facets" else "too_long"
        )
    elif validation_error_type in {"value_error", "assertion_error"} and not field_path:
        validation_rule = "invalid_cross_field_combination"
    else:
        validation_rule = "unknown"
    fingerprint: dict[str, object] = {
        "error_stage": (
            "cross_field_validation"
            if validation_rule == "invalid_cross_field_combination"
            else "pydantic_validation"
        ),
        "validation_error_type": validation_error_type,
        "field_path": field_path,
        "validation_rule": validation_rule,
    }
    fingerprint.update(_safe_shape_metadata(returned))
    return fingerprint


def load_diagnostic_set(path: Path) -> dict[str, object]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DiagnosticSetError(f"diagnostic set could not be loaded: {path}") from exc
    if not isinstance(payload, Mapping):
        raise DiagnosticSetError("diagnostic root must be a mapping")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not 10 <= len(raw_cases) <= 15:
        raise DiagnosticSetError("diagnostic set must contain 10 to 15 cases")

    cases: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise DiagnosticSetError(f"cases[{index}] must be a mapping")
        query_id = _required_string(raw_case, "id", f"cases[{index}]")
        if query_id in seen_ids:
            raise DiagnosticSetError(f"duplicate query id: {query_id}")
        seen_ids.add(query_id)
        user_query = _required_string(raw_case, "query", f"cases[{index}]")
        expected_outcome = _required_string(
            raw_case, "expected_outcome", f"cases[{index}]"
        )
        if expected_outcome not in EXPECTED_OUTCOMES:
            raise DiagnosticSetError(f"invalid expected outcome: {expected_outcome}")
        manual_review = raw_case.get("manual_review")
        if not isinstance(manual_review, Mapping):
            raise DiagnosticSetError(f"cases[{index}].manual_review must be a mapping")
        if manual_review.get("status") != "REVIEWED":
            raise DiagnosticSetError(f"{query_id} must be manually reviewed before execution")
        categories = raw_case.get("categories")
        if not isinstance(categories, list) or not categories or not all(
            isinstance(item, str) and item.strip() for item in categories
        ):
            raise DiagnosticSetError(f"{query_id} categories must be non-empty strings")
        evidence_groups = _parse_evidence_groups(
            raw_case.get("evidence_groups"), query_id=query_id
        )
        if expected_outcome == "SHOULD_ANSWER" and not evidence_groups:
            raise DiagnosticSetError(f"{query_id} requires reviewed Gold evidence")
        if expected_outcome == "SHOULD_FAIL_CLOSED" and evidence_groups:
            raise DiagnosticSetError(f"{query_id} negative control cannot contain Gold evidence")
        cases.append(
            {
                "query_id": query_id,
                "user_query": user_query,
                "categories": list(categories),
                "expected_outcome": expected_outcome,
                "manual_review": dict(manual_review),
                "gold_evidence_groups": evidence_groups,
            }
        )

    required_repros = {
        "UQ-001": "agentic system 有哪些權限管理要做",
        "UQ-002": "agentic system 要注意什麼地方",
    }
    by_id = {str(case["query_id"]): case for case in cases}
    for query_id, exact_query in required_repros.items():
        if by_id.get(query_id, {}).get("user_query") != exact_query:
            raise DiagnosticSetError(f"{query_id} mandatory reproduction is missing")

    return {
        "diagnostic_id": _required_string(payload, "diagnostic_id", "root"),
        "version": _required_string(payload, "version", "root"),
        "owner_scope": _required_string(payload, "owner_scope", "root"),
        "production_contract": dict(payload.get("production_contract") or {}),
        "cases": cases,
    }


def classify_trace(case: Mapping[str, object], trace: Mapping[str, object]) -> str:
    expected_outcome = str(case["expected_outcome"])
    final = _mapping(trace.get("final"))
    readiness = _mapping(trace.get("readiness"))
    context = _mapping(trace.get("context_requirement"))
    retrieval = _mapping(trace.get("knowledge_retrieval"))
    termination_reason = str(final.get("termination_reason") or "")

    if trace.get("test_setup_valid") is False or expected_outcome == "REVIEW_REQUIRED":
        return "invalid_test_setup"
    if readiness.get("provider_error") is True or termination_reason == "provider_error":
        return "provider_failure"
    if termination_reason == "provider_contract_error" and not final.get(
        "synthesis_called"
    ):
        return "provider_failure"

    if expected_outcome == "SHOULD_FAIL_CLOSED":
        return (
            "expected_insufficient"
            if final.get("insufficient_info") is True
            else "final_synthesis_failure"
        )

    if context.get("selector_status") != "completed" or context.get(
        "needs_knowledge"
    ) is not True:
        return "selector_failure"

    groups = case.get("gold_evidence_groups")
    if not isinstance(groups, list):
        groups = []
    candidates = retrieval.get("candidates")
    accepted = retrieval.get("accepted")
    candidate_items = candidates if isinstance(candidates, list) else []
    accepted_items = accepted if isinstance(accepted, list) else []
    raw_complete = _all_groups_covered(groups, candidate_items)
    accepted_complete = _all_groups_covered(groups, accepted_items)
    accepted_related = _any_group_covered(groups, accepted_items)

    corpus_support = _mapping(case.get("manual_review")).get("corpus_support")
    if corpus_support == "RELATED_BUT_INCOMPLETE" and accepted_related:
        return "evidence_coverage_failure"
    if not raw_complete:
        return "retrieval_failure"
    if not accepted_complete:
        return "acceptance_failure"
    if readiness.get("called") is not True:
        return "final_synthesis_failure"
    if readiness.get("ready") is False:
        return "readiness_false_negative"
    if readiness.get("ready") is not True:
        return "provider_failure"
    if (
        final.get("synthesis_called") is not True
        or final.get("insufficient_info") is True
        or termination_reason != "completed"
        or final.get("completed") is not True
    ):
        return "final_synthesis_failure"
    return "completed"


def build_report(
    *,
    run_metadata: Mapping[str, object],
    corpus_snapshot: Mapping[str, object],
    traces: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    classifications = Counter(str(trace["classification"]) for trace in traces)
    unknown = set(classifications) - set(CLASSIFICATIONS)
    if unknown:
        raise DiagnosticReportError(f"unknown classifications: {sorted(unknown)}")
    false_insufficient = [
        trace
        for trace in traces
        if trace.get("expected_outcome") == "SHOULD_ANSWER"
        and _mapping(trace.get("final")).get("insufficient_info") is True
    ]
    root_causes = Counter(str(trace["classification"]) for trace in false_insufficient)
    aggregate = {name: classifications.get(name, 0) for name in CLASSIFICATIONS}
    aggregate.update(
        {
            "total": len(traces),
            "false_insufficient_count": len(false_insufficient),
            "false_insufficient_root_causes": dict(sorted(root_causes.items())),
        }
    )
    report: dict[str, object] = {
        "artifact_type": "user_facing_answer_quality_diagnostic",
        "run_metadata": dict(run_metadata),
        "corpus_snapshot": dict(corpus_snapshot),
        "per_query_traces": [dict(trace) for trace in traces],
        "aggregate": aggregate,
    }
    _reject_forbidden_fields(report)
    return report


def write_json_report(report: Mapping[str, object], path: Path) -> None:
    _reject_forbidden_fields(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class RecordingProvider(LLMProvider):
    """Evaluation-only provider seam that never records prompts or raw responses."""

    def __init__(self, delegate: LLMProvider) -> None:
        self._delegate = delegate
        self.events: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def supports_tool_calling(self) -> bool:
        return self._delegate.supports_tool_calling

    @property
    def supports_structured_output(self) -> bool:
        return self._delegate.supports_structured_output

    def reset(self) -> None:
        self.events = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        operation = str((request.metadata or {}).get("operation") or "unknown")
        event: dict[str, object] = {"operation": operation, "status": "completed"}
        try:
            response = await self._delegate.generate(request)
        except Exception as exc:
            event.update(
                status="provider_error",
                error_type=type(exc).__name__,
                provider_error_kind=classify_safe_provider_error(exc),
            )
            if event["provider_error_kind"] == "structured_output_parse_failure":
                event["failure_fingerprint"] = {
                    "error_stage": "provider_parse",
                    "validation_error_type": type(exc).__name__,
                    "field_path": None,
                    "validation_rule": "invalid_json",
                }
            self.events.append(event)
            raise
        structured = response.structured_output
        mapped_context_decision: Optional[ContextRequirementDecision] = None
        wire_mode: Optional[str] = None
        contract_model = (
            ReferenceBindingDecision
            if operation == "reference_binding_resolution"
            else EvidenceReadinessDecision
            if operation == "evidence_readiness"
            else None
        )
        if contract_model is not None or operation == "context_requirement_selection":
            try:
                if operation == "context_requirement_selection":
                    wire_decision = ContextRequirementWireDecision.model_validate(
                        structured
                    )
                    wire_mode = wire_decision.selection.mode
                    mapped_context_decision = map_context_requirement_wire_decision(
                        wire_decision
                    )
                else:
                    assert contract_model is not None
                    contract_model.model_validate(structured)
            except ValidationError as validation_error:
                event.update(
                    status="contract_error",
                    contract_error=True,
                    structured_output_contract="invalid",
                    provider_error_kind="provider_contract_failure",
                    failure_fingerprint=build_safe_contract_fingerprint(
                        validation_error, structured
                    ),
                )
            else:
                event["structured_output_contract"] = "valid"
                event["shape_metadata"] = _safe_shape_metadata(structured)
        if operation == "reference_binding_resolution" and isinstance(structured, Mapping):
            bindings = structured.get("reference_bindings")
            event["binding_count"] = len(bindings) if isinstance(bindings, list) else None
        elif operation == "context_requirement_selection" and isinstance(
            structured, Mapping
        ):
            event["wire_mode"] = wire_mode
            if mapped_context_decision is not None:
                event["needs_knowledge"] = mapped_context_decision.needs_knowledge
                event["needs_memory"] = mapped_context_decision.needs_memory
                event["contextual_facet_count"] = len(
                    mapped_context_decision.contextual_facets
                )
                event["memory_query_present"] = (
                    mapped_context_decision.memory_query is not None
                )
        elif operation == "evidence_readiness" and isinstance(structured, Mapping):
            event["ready"] = structured.get("ready")
        elif operation == "bounded_agent_final":
            event["finish_reason"] = response.finish_reason
        self.events.append(event)
        return response


class RecordingRetriever:
    """Evaluation-only retriever seam that observes the frozen candidate pool."""

    def __init__(
        self,
        *,
        delegate: ProductionChunkRetriever,
        chunk_repository: ChunkRepository,
    ) -> None:
        self._delegate = delegate
        self._chunk_repository = chunk_repository
        self.trace: Optional[dict[str, object]] = None

    def reset(self) -> None:
        self.trace = None

    def retrieve_with_metadata(self, **kwargs: Any) -> RetrievalResult:
        raw_candidates: list[Any] = []
        raw_status = "not_available"
        query_embedding = kwargs.get("query_embedding")
        if query_embedding is not None and self._chunk_repository.supports_vector_query():
            raw_candidates = self._chunk_repository.list_production_chunks_by_vector(
                query_embedding=query_embedding,
                top_k=min(
                    int(kwargs["top_k"]) * 2,
                    KNOWLEDGE_VECTOR_CANDIDATE_POOL_MAX,
                ),
                page_ids=kwargs.get("page_ids"),
                section_paths=kwargs.get("section_paths"),
                source_kinds=kwargs.get("source_kinds"),
                owner_scope=kwargs.get("owner_scope", "local"),
            )
            raw_status = "observed"
        result = self._delegate.retrieve_with_metadata(**kwargs)
        self.trace = {
            "actual_query_used": kwargs.get("query_text"),
            "retrieval_mode": result.retrieval_mode,
            "raw_observation_status": raw_status,
            "raw_candidate_count": (
                result.candidate_count
                if result.candidate_count is not None
                else len(raw_candidates)
            ),
            "candidates": [
                _retrieval_item(rank, item)
                for rank, item in enumerate(raw_candidates, start=1)
            ],
            "accepted_evidence_count": (
                result.accepted_evidence_count
                if result.accepted_evidence_count is not None
                else len(result.chunks)
            ),
            "accepted": [
                _retrieval_item(rank, item)
                for rank, item in enumerate(result.chunks, start=1)
            ],
        }
        return result


class RecordingMemoryService:
    def __init__(self, delegate: MemoryService) -> None:
        self._delegate = delegate
        self.searches: list[dict[str, object]] = []

    def reset(self) -> None:
        self.searches = []

    async def search_memories(self, **kwargs: Any):
        result = await self._delegate.search_memories(**kwargs)
        self.searches.append(
            {
                "retrieval_mode": kwargs.get("retrieval_mode"),
                "result_count": len(result),
            }
        )
        return result

    async def save_memory(self, **kwargs: Any):
        return await self._delegate.save_memory(**kwargs)


async def run_live_diagnostic(
    diagnostic_set: Mapping[str, object],
    *,
    database_url: str,
    openai_api_key: str,
    model: str = DEFAULT_QA_MODEL,
    include_provider_operations: bool = False,
) -> dict[str, object]:
    if not database_url.strip():
        raise DiagnosticReportError("DATABASE_URL is required")
    if not openai_api_key.strip():
        raise DiagnosticReportError("OPENAI_API_KEY is required")
    owner_scope = str(diagnostic_set["owner_scope"])
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session: Session = session_factory()
    try:
        identity_row = session.execute(
            text(
                "select current_database(), current_user, inet_server_port()"
            )
        ).one()
        if identity_row[0] != "knowvia" or identity_row[1] != "knowvia":
            raise DiagnosticReportError("local database identity is not Knowvia")
        source_repository = SourceDocumentRepository(session)
        sources = source_repository.list_indexed_sources(owner_scope=owner_scope)
        corpus_snapshot = {
            "database": {
                "name": identity_row[0],
                "user": identity_row[1],
                "port": identity_row[2],
            },
            "owner_scope": owner_scope,
            "source_count": len(sources),
            "total_indexed_chunk_count": sum(source.chunk_count for source in sources),
            "sources": [
                {
                    "source_id": source.id,
                    "source_kind": source.source_kind,
                    "source_display_name": source.display_name,
                    "status": source.status,
                    "indexed_chunk_count": source.chunk_count,
                }
                for source in sources
            ],
            "embedding_model": "text-embedding-3-small",
            "embedding_dimensions": 1536,
        }
        locator_rows = session.query(
            KnowledgeChunk.source_document_id, KnowledgeChunk.locator
        ).filter(
            KnowledgeChunk.owner_scope == owner_scope,
            KnowledgeChunk.eligibility_status == "eligible",
        ).all()
        available_locators = {
            (int(source_id), str(locator))
            for source_id, locator in locator_rows
            if source_id is not None and locator is not None
        }

        provider = RecordingProvider(OpenAIClient(api_key=openai_api_key))
        provider_router = ProviderRouter()
        provider_router.register_provider(provider)
        embedding_client = OpenAIEmbeddingClient(api_key=openai_api_key)
        chunk_repository = ChunkRepository(session)
        retriever = RecordingRetriever(
            delegate=ProductionChunkRetriever(chunk_repository=chunk_repository),
            chunk_repository=chunk_repository,
        )
        unit_of_work_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
        memory = RecordingMemoryService(
            MemoryService(
                unit_of_work_factory=unit_of_work_factory,
                embedding_client=embedding_client,
            )
        )
        runtime = BoundedAgentRuntime(
            provider_router=provider_router,
            tool_registry=build_agent_tool_registry(
                retriever=retriever,
                embedding_client=embedding_client,
                memory_service=memory,
            ),
            max_tool_calls=3,
            max_iterations=6,
            tool_timeout_seconds=8.0,
            context_char_budget=16000,
            workflow_run_service=None,
        )

        traces: list[dict[str, object]] = []
        for raw_case in diagnostic_set["cases"]:  # type: ignore[index]
            case = dict(raw_case)
            provider.reset()
            retriever.reset()
            memory.reset()
            setup_valid = _case_setup_valid(case, available_locators)
            if not setup_valid:
                trace = _invalid_setup_trace(case)
                traces.append(trace)
                continue
            result = await runtime.run(
                query=str(case["user_query"]),
                session_id=0,
                owner_id=owner_scope,
                provider_name=DEFAULT_QA_PROVIDER_NAME,
                model=model,
                request_workflow_id=f"8.5-{case['query_id']}",
                reference_resolver_history=[],
                history_message_count=0,
                history_roles=[],
                top_k=5,
            )
            trace = _trace_from_run(
                case=case,
                result=result,
                provider_events=provider.events,
                retrieval_trace=retriever.trace,
                memory_searches=memory.searches,
                include_provider_operations=include_provider_operations,
            )
            trace["classification"] = classify_trace(case, trace)
            traces.append(trace)

        return build_report(
            run_metadata={
                "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "diagnostic_id": diagnostic_set["diagnostic_id"],
                "diagnostic_version": diagnostic_set["version"],
                "execution_levels": ["deterministic_local_trace", "actual_provider_trace"],
                "provider": DEFAULT_QA_PROVIDER_NAME,
                "model": model,
                "openai_api_key_available": True,
                "primary_runs_per_query": 1,
                "repeated_probe_count": 0,
                "production_behavior_changed": False,
            },
            corpus_snapshot={
                **corpus_snapshot,
                "production_retrieval_settings": dict(
                    diagnostic_set.get("production_contract") or {}
                ),
            },
            traces=traces,
        )
    finally:
        session.close()
        engine.dispose()


def _trace_from_run(
    *,
    case: Mapping[str, object],
    result: Any,
    provider_events: Sequence[Mapping[str, object]],
    retrieval_trace: Optional[Mapping[str, object]],
    memory_searches: Sequence[Mapping[str, object]],
    include_provider_operations: bool = False,
) -> dict[str, object]:
    resolver = _operation_event(provider_events, "reference_binding_resolution")
    selector = _operation_event(provider_events, "context_requirement_selection")
    readiness_event = _operation_event(provider_events, "evidence_readiness")
    final_event = _operation_event(provider_events, "bounded_agent_final")
    termination_reason = result.termination_reason.value
    trace: dict[str, object] = {
        **dict(case),
        "test_setup_valid": True,
        "reference_binding": {
            "binding_count": resolver.get("binding_count", 0) if resolver else 0,
            "valid": bool(resolver and selector),
        },
        "context_requirement": {
            "needs_knowledge": selector.get("needs_knowledge") if selector else None,
            "needs_memory": selector.get("needs_memory") if selector else None,
            "contextual_facet_count": (
                selector.get("contextual_facet_count") if selector else None
            ),
            "selector_status": (
                "completed" if selector and selector.get("status") == "completed" else "failed"
            ),
        },
        "knowledge_retrieval": dict(
            retrieval_trace
            or {
                "actual_query_used": None,
                "retrieval_mode": None,
                "raw_observation_status": "not_called",
                "raw_candidate_count": 0,
                "candidates": [],
                "accepted_evidence_count": 0,
                "accepted": [],
            }
        ),
        "memory": {
            "searched": bool(memory_searches),
            "query_count": len(memory_searches),
            "resolved_count": sum(
                1 for search in memory_searches if int(search.get("result_count") or 0) > 0
            ),
            "used_saved_memory": bool(result.used_saved_memory),
        },
        "readiness": {
            "called": readiness_event is not None,
            "ready": readiness_event.get("ready") if readiness_event else None,
            "provider_error": bool(
                readiness_event and readiness_event.get("status") == "provider_error"
            ),
            "contract_error": bool(
                readiness_event and termination_reason == "provider_contract_error"
            ),
        },
        "final": {
            "synthesis_called": final_event is not None,
            "termination_reason": termination_reason,
            "insufficient_info": bool(result.insufficient_info),
            "citation_count": len(result.citations),
            "completed": termination_reason == "completed",
        },
    }
    if include_provider_operations:
        trace["provider_operations"] = [dict(event) for event in provider_events]
    return trace


def _invalid_setup_trace(case: Mapping[str, object]) -> dict[str, object]:
    trace = {
        **dict(case),
        "test_setup_valid": False,
        "reference_binding": {"binding_count": 0, "valid": False},
        "context_requirement": {
            "needs_knowledge": None,
            "needs_memory": None,
            "contextual_facet_count": None,
            "selector_status": "not_called",
        },
        "knowledge_retrieval": {
            "actual_query_used": None,
            "retrieval_mode": None,
            "raw_observation_status": "not_called",
            "raw_candidate_count": 0,
            "candidates": [],
            "accepted_evidence_count": 0,
            "accepted": [],
        },
        "memory": {
            "searched": False,
            "query_count": 0,
            "resolved_count": 0,
            "used_saved_memory": False,
        },
        "readiness": {
            "called": False,
            "ready": None,
            "provider_error": False,
            "contract_error": False,
        },
        "final": {
            "synthesis_called": False,
            "termination_reason": "invalid_test_setup",
            "insufficient_info": False,
            "citation_count": 0,
            "completed": False,
        },
        "classification": "invalid_test_setup",
    }
    return trace


def _case_setup_valid(
    case: Mapping[str, object], available_locators: set[tuple[int, str]]
) -> bool:
    groups = case.get("gold_evidence_groups")
    if not isinstance(groups, list):
        return False
    for group in groups:
        for location in _mapping(group).get("locations", []):
            location_map = _mapping(location)
            source_id = location_map.get("source_id")
            locators = location_map.get("locators")
            if not isinstance(source_id, int) or not isinstance(locators, list):
                return False
            if not any((source_id, str(locator)) in available_locators for locator in locators):
                return False
    return True


def _parse_evidence_groups(value: object, *, query_id: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise DiagnosticSetError(f"{query_id} evidence_groups must be a list")
    parsed: list[dict[str, object]] = []
    for index, raw_group in enumerate(value):
        group = _mapping(raw_group)
        group_id = _required_string(group, "group_id", f"{query_id}.group[{index}]")
        match = str(group.get("match") or "any")
        if match not in {"any", "all"}:
            raise DiagnosticSetError(f"{query_id} group match must be any or all")
        raw_locations = group.get("locations")
        if not isinstance(raw_locations, list) or not raw_locations:
            raise DiagnosticSetError(f"{query_id} group locations must not be empty")
        locations: list[dict[str, object]] = []
        for raw_location in raw_locations:
            location = _mapping(raw_location)
            source_id = location.get("source_id")
            locators = location.get("locators")
            if isinstance(source_id, bool) or not isinstance(source_id, int):
                raise DiagnosticSetError(f"{query_id} source_id must be an integer")
            if not isinstance(locators, list) or not locators or not all(
                isinstance(locator, str) and locator.strip() for locator in locators
            ):
                raise DiagnosticSetError(f"{query_id} locators must be non-empty strings")
            locations.append({"source_id": source_id, "locators": list(locators)})
        parsed.append(
            {
                "group_id": group_id,
                "match": match,
                "locations": locations,
            }
        )
    return parsed


def _all_groups_covered(
    groups: Sequence[object], items: Sequence[object]
) -> bool:
    return bool(groups) and all(_group_covered(_mapping(group), items) for group in groups)


def _any_group_covered(
    groups: Sequence[object], items: Sequence[object]
) -> bool:
    return any(_group_covered(_mapping(group), items) for group in groups)


def _group_covered(group: Mapping[str, object], items: Sequence[object]) -> bool:
    locations = group.get("locations")
    if not isinstance(locations, list):
        return False
    matches: list[bool] = []
    for raw_location in locations:
        location = _mapping(raw_location)
        source_id = location.get("source_id")
        locators = location.get("locators")
        locator_set = set(locators) if isinstance(locators, list) else set()
        matches.append(
            any(
                _mapping(item).get("source_id") == source_id
                and _mapping(item).get("locator") in locator_set
                for item in items
            )
        )
    return all(matches) if group.get("match") == "all" else any(matches)


def _operation_event(
    events: Sequence[Mapping[str, object]], operation: str
) -> Optional[Mapping[str, object]]:
    return next((event for event in events if event.get("operation") == operation), None)


def _retrieval_item(rank: int, item: Any) -> dict[str, object]:
    return {
        "rank": rank,
        "source_id": item.source_document_id,
        "source_display_name": item.source_display_name or item.notion_path,
        "locator": item.locator or item.notion_path,
        "score": round(float(item.score), 6),
    }


def _reject_forbidden_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key in FORBIDDEN_REPORT_FIELDS:
                raise DiagnosticReportError(
                    f"forbidden diagnostic field: {normalized_key}"
                )
            _reject_forbidden_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_forbidden_fields(nested)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _required_string(value: Mapping[str, object], key: str, context: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected.strip():
        raise DiagnosticSetError(f"{context}.{key} must be a non-empty string")
    return selected.strip()
