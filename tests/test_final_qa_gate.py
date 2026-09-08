from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from eval.retrieval.final_qa_gate import (
    _aggregate_variant,
    _build_report,
    apply_bounded_human_review,
    run_live_final_qa_gate,
)


def _positive_case(
    *,
    case_id: str,
    top_k: int,
    insufficient_info: bool,
    classification: str = "RETRIEVAL_INCOMPLETE",
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "answerable": True,
        "category": "cross_source_discrimination",
        "requested_top_k": top_k,
        "insufficient_info": insufficient_info,
        "automated_classification": classification,
        "reviewed_classification": None,
        "bounded_classification": classification,
        "classification_authority": "automated",
        "citation_support": None,
        "human_review": {"status": "PENDING"},
        "citations": {"count": 0},
        "operational": {
            "llm_provider": "openai",
            "llm_model": "gpt-test",
            "llm_token_input": None,
            "llm_token_output": None,
            "llm_latency_ms": None,
        },
    }


@pytest.mark.parametrize("case_id", ["rv-009", "rv-027", "rv-029"])
def test_live_case_id_does_not_trigger_human_review(case_id: str) -> None:
    raw = _positive_case(case_id=case_id, top_k=8, insufficient_info=False)

    assert raw["automated_classification"] == "RETRIEVAL_INCOMPLETE"
    assert raw["reviewed_classification"] is None
    assert raw["bounded_classification"] == "RETRIEVAL_INCOMPLETE"
    assert raw["classification_authority"] == "automated"
    assert raw["human_review"]["status"] == "PENDING"
    with pytest.raises(TypeError):
        apply_bounded_human_review(raw)  # type: ignore[call-arg]


def test_explicit_offline_review_uses_supplied_decision_not_case_id() -> None:
    raw = _positive_case(case_id="rv-009", top_k=8, insufficient_info=False)

    reviewed = apply_bounded_human_review(
        raw,
        review_decision={
            "reviewed_classification": "CORRECT_GROUNDED",
            "citation_support": "SUPPORTED_CITATIONS",
            "basis": "Explicit saved-artifact adjudication for this case.",
        },
    )

    assert reviewed["automated_classification"] == "RETRIEVAL_INCOMPLETE"
    assert reviewed["reviewed_classification"] == "CORRECT_GROUNDED"
    assert reviewed["bounded_classification"] == "CORRECT_GROUNDED"
    assert reviewed["classification_authority"] == "human_reviewed"
    assert reviewed["citation_support"] == "SUPPORTED_CITATIONS"
    assert reviewed["human_review"]["status"] == "REVIEWED"


def test_explicit_offline_review_can_mark_case_unsupported() -> None:
    reviewed = apply_bounded_human_review(
        _positive_case(case_id="rv-027", top_k=8, insufficient_info=False),
        review_decision={
            "reviewed_classification": "UNSUPPORTED_OR_INCORRECT",
            "citation_support": "CITATIONS_DO_NOT_SUPPORT_CLAIM",
            "basis": "Explicit saved-artifact adjudication found unsupported citations.",
        },
    )

    assert reviewed["reviewed_classification"] == "UNSUPPORTED_OR_INCORRECT"
    assert reviewed["automated_classification"] == "RETRIEVAL_INCOMPLETE"


def test_aggregate_uses_reviewed_classification_when_present() -> None:
    reviewed = apply_bounded_human_review(
        _positive_case(case_id="rv-029", top_k=5, insufficient_info=False),
        review_decision={
            "reviewed_classification": "UNSUPPORTED_OR_INCORRECT",
            "citation_support": "CITATIONS_DO_NOT_SUPPORT_CLAIM",
            "basis": "Explicit saved-artifact adjudication found an unsupported claim.",
        },
    )

    metrics = _aggregate_variant([reviewed])

    assert metrics["answerable"]["unsupported_or_incorrect_count"] == 1
    assert metrics["answerable"]["retrieval_incomplete_count"] == 0


def test_unreviewed_aggregate_uses_automated_classification_and_stays_pending() -> None:
    raw = _positive_case(case_id="rv-029", top_k=5, insufficient_info=False)

    metrics = _aggregate_variant([raw])

    assert raw["reviewed_classification"] is None
    assert raw["human_review"]["status"] == "PENDING"
    assert metrics["answerable"]["retrieval_incomplete_count"] == 1
    assert metrics["answerable"]["unsupported_or_incorrect_count"] == 0


def test_live_runner_has_no_automatic_human_review_call() -> None:
    assert "apply_bounded_human_review(" not in inspect.getsource(
        run_live_final_qa_gate
    )


def test_unreviewed_report_provenance_is_automated_and_pending(tmp_path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text("{}")
    benchmark = SimpleNamespace(
        benchmark_id="test-benchmark",
        version="test-version",
        contract=SimpleNamespace(
            chunk_max_chars=1200,
            chunk_overlap_chars=0,
            page_aware=True,
            similarity="cosine",
            relevance_floor=0.3,
        ),
    )

    report = _build_report(
        benchmark=benchmark,
        benchmark_path=benchmark_path,
        corpus_manifest=[],
        top_k=5,
        cases=[_positive_case(case_id="rv-009", top_k=5, insufficient_info=False)],
    )

    assert report["review_provenance"] == {
        "application_mode": "live_automated_only",
        "review_status": "PENDING",
        "classification_authority": "automated",
        "automated_classification_field": "automated_classification",
        "reviewed_classification_field": "reviewed_classification",
        "aggregate_metrics_use": "automated_classification",
    }
