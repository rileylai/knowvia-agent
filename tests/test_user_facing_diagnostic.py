from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eval.user_facing_diagnostic import (
    DiagnosticReportError,
    RecordingProvider,
    build_report,
    build_safe_contract_fingerprint,
    classify_trace,
    load_diagnostic_set,
    write_json_report,
)
from src.agent.models import ContextRequirementDecision, ContextRequirementWireDecision
from eval.run_provider_failure_triage import classify_repeats
from src.providers import LLMMessage, LLMProvider, LLMRequest, LLMResponse


FIXTURE_PATH = Path("eval/user_facing_diagnostic.yaml")


class WireDiagnosticProvider(LLMProvider):
    supports_structured_output = True

    def __init__(self, output: dict[str, object]) -> None:
        self.output = output

    @property
    def name(self) -> str:
        return "wire-diagnostic"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            provider=self.name,
            model=request.model,
            output_text="",
            structured_output=self.output,
        )


def _case(*, expected_outcome: str = "SHOULD_ANSWER") -> dict[str, object]:
    return {
        "query_id": "UQ-TEST",
        "user_query": "What does the indexed source say?",
        "expected_outcome": expected_outcome,
        "gold_evidence_groups": [
            {
                "group_id": "support",
                "match": "any",
                "locations": [{"source_id": 1, "locators": ["page 2"]}],
            }
        ],
    }


def _trace() -> dict[str, object]:
    candidate = {
        "rank": 1,
        "source_id": 1,
        "source_display_name": "source.pdf",
        "locator": "page 2",
        "score": 0.8,
    }
    return {
        "reference_binding": {"binding_count": 0, "valid": True},
        "context_requirement": {
            "needs_knowledge": True,
            "needs_memory": False,
            "contextual_facet_count": 0,
            "selector_status": "completed",
        },
        "knowledge_retrieval": {
            "actual_query_used": "What does the indexed source say?",
            "retrieval_mode": "pgvector_exact_cosine",
            "raw_candidate_count": 1,
            "candidates": [candidate],
            "accepted_evidence_count": 1,
            "accepted": [candidate],
        },
        "memory": {
            "searched": False,
            "query_count": 0,
            "resolved_count": 0,
            "used_saved_memory": False,
        },
        "readiness": {
            "called": True,
            "ready": True,
            "provider_error": False,
            "contract_error": False,
        },
        "final": {
            "synthesis_called": True,
            "termination_reason": "completed",
            "insufficient_info": False,
            "citation_count": 1,
            "completed": True,
        },
    }


def test_diagnostic_set_has_reviewed_bounded_user_questions() -> None:
    diagnostic_set = load_diagnostic_set(FIXTURE_PATH)

    assert len(diagnostic_set["cases"]) == 12
    assert [case["query_id"] for case in diagnostic_set["cases"][:2]] == [
        "UQ-001",
        "UQ-002",
    ]
    assert all(case["manual_review"]["status"] == "REVIEWED" for case in diagnostic_set["cases"])
    assert {case["expected_outcome"] for case in diagnostic_set["cases"]} == {
        "SHOULD_ANSWER",
        "SHOULD_FAIL_CLOSED",
    }


def test_classification_maps_readiness_false_negative() -> None:
    trace = _trace()
    trace["readiness"]["ready"] = False
    trace["final"].update(
        synthesis_called=False,
        termination_reason="insufficient_info",
        insufficient_info=True,
        citation_count=0,
        completed=False,
    )

    assert classify_trace(_case(), trace) == "readiness_false_negative"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("selector", "selector_failure"),
        ("retrieval", "retrieval_failure"),
        ("acceptance", "acceptance_failure"),
        ("final", "final_synthesis_failure"),
    ],
)
def test_classification_maps_failure_boundaries(mutation: str, expected: str) -> None:
    trace = _trace()
    if mutation == "selector":
        trace["context_requirement"]["needs_knowledge"] = False
    elif mutation == "retrieval":
        trace["knowledge_retrieval"].update(
            raw_candidate_count=1,
            candidates=[
                {
                    "rank": 1,
                    "source_id": 2,
                    "source_display_name": "unrelated.pdf",
                    "locator": "page 9",
                    "score": 0.7,
                }
            ],
            accepted_evidence_count=0,
            accepted=[],
        )
    elif mutation == "acceptance":
        trace["knowledge_retrieval"].update(
            accepted_evidence_count=0,
            accepted=[],
        )
    else:
        trace["final"].update(
            termination_reason="provider_contract_error",
            insufficient_info=False,
            citation_count=0,
            completed=False,
        )

    assert classify_trace(_case(), trace) == expected


def test_provider_failure_is_not_semantic_insufficient() -> None:
    trace = _trace()
    trace["readiness"].update(ready=None, provider_error=True)
    trace["final"].update(
        synthesis_called=False,
        termination_reason="provider_error",
        insufficient_info=False,
        citation_count=0,
        completed=False,
    )

    assert classify_trace(_case(), trace) == "provider_failure"


def test_diagnostic_records_mapped_domain_metadata_without_wire_values() -> None:
    provider = RecordingProvider(
        WireDiagnosticProvider(
            {
                "selection": {
                    "mode": "mixed",
                    "contextual_facets": [
                        {"id": "c1", "text": "private company context"}
                    ],
                }
            }
        )
    )
    request = LLMRequest(
        model="fixture-1",
        messages=[LLMMessage(role="user", content="private query")],
        response_format=ContextRequirementWireDecision.response_format(),
        metadata={"operation": "context_requirement_selection"},
    )

    asyncio.run(provider.generate(request))

    event = provider.events[0]
    assert event["structured_output_contract"] == "valid"
    assert event["wire_mode"] == "mixed"
    assert event["needs_knowledge"] is True
    assert event["needs_memory"] is True
    assert event["contextual_facet_count"] == 1
    assert event["memory_query_present"] is False
    assert "private company context" not in json.dumps(event)


def test_expected_negative_control_maps_to_expected_insufficient() -> None:
    case = _case(expected_outcome="SHOULD_FAIL_CLOSED")
    case["gold_evidence_groups"] = []
    trace = _trace()
    trace["knowledge_retrieval"].update(
        raw_candidate_count=0,
        candidates=[],
        accepted_evidence_count=0,
        accepted=[],
    )
    trace["readiness"].update(called=False, ready=None)
    trace["final"].update(
        synthesis_called=False,
        termination_reason="insufficient_info",
        insufficient_info=True,
        citation_count=0,
        completed=False,
    )

    assert classify_trace(case, trace) == "expected_insufficient"


def test_report_rejects_private_or_raw_content() -> None:
    trace = {**_trace(), "raw_provider_response": {"secret": "value"}}

    with pytest.raises(DiagnosticReportError, match="forbidden diagnostic field"):
        build_report(
            run_metadata={"run_id": "test"},
            corpus_snapshot={"source_count": 1, "sources": []},
            traces=[{**_case(), **trace, "classification": "completed"}],
        )


def test_report_serialization_is_deterministic(tmp_path: Path) -> None:
    report = build_report(
        run_metadata={"run_id": "test", "live_provider": False},
        corpus_snapshot={"source_count": 1, "sources": []},
        traces=[{**_case(), **_trace(), "classification": "completed"}],
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    write_json_report(report, first)
    write_json_report(report, second)

    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["aggregate"]["total"] == 1
    assert payload["aggregate"]["false_insufficient_count"] == 0


def test_provider_triage_classifies_stable_selector_contract_failure() -> None:
    outcomes = [
        {
            "failing_operation": "context_requirement_selection",
            "failure_boundary": "context_requirement_selection",
            "provider_error_kind": "provider_contract_failure",
            "termination_reason": "provider_error",
        },
        {
            "failing_operation": "context_requirement_selection",
            "failure_boundary": "context_requirement_selection",
            "provider_error_kind": "provider_contract_failure",
            "termination_reason": "provider_error",
        },
    ]

    assert classify_repeats(outcomes) == (
        "selector_provider_failure",
        "deterministic",
    )


def test_provider_triage_marks_success_and_failure_as_intermittent() -> None:
    outcomes = [
        {
            "failing_operation": "context_requirement_selection",
            "failure_boundary": "context_requirement_selection",
            "provider_error_kind": "transient_transport_failure",
            "termination_reason": "provider_error",
        },
        {
            "failing_operation": None,
            "failure_boundary": None,
            "provider_error_kind": "unresolved_provider_failure",
            "termination_reason": "completed",
        },
    ]

    assert classify_repeats(outcomes) == (
        "intermittent_provider_failure",
        "intermittent",
    )


def test_selector_fingerprint_is_safe_and_maps_field_rule() -> None:
    returned = {
        "needs_knowledge": True,
        "needs_memory": True,
        "contextual_facets": [
            {"id": "facet-1", "text": "private provider text"},
            {"id": "facet-2", "text": "private provider text"},
            {"id": "facet-3", "text": "private provider text"},
        ],
        "memory_query": None,
    }
    with pytest.raises(ValidationError) as error:
        ContextRequirementDecision.model_validate(returned)

    fingerprint = build_safe_contract_fingerprint(error.value, returned)
    serialized = json.dumps(fingerprint, ensure_ascii=False)
    assert fingerprint["error_stage"] == "pydantic_validation"
    assert fingerprint["field_path"] == "contextual_facets"
    assert fingerprint["validation_rule"] == "too_many_items"
    assert "private provider text" not in serialized


def test_selector_fingerprint_maps_cross_field_rule() -> None:
    returned = {
        "needs_knowledge": True,
        "needs_memory": False,
        "contextual_facets": [{"id": "facet-1", "text": "context"}],
        "memory_query": None,
    }
    with pytest.raises(ValidationError) as error:
        ContextRequirementDecision.model_validate(returned)

    fingerprint = build_safe_contract_fingerprint(error.value, returned)
    assert fingerprint["error_stage"] == "cross_field_validation"
    assert fingerprint["validation_rule"] == "invalid_cross_field_combination"
