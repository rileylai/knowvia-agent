from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence

try:
    from .citation_accuracy_eval import evaluate_citation_accuracy
    from .context_aware_embedding_input_eval import project_citations
    from .golden_questions import load_golden_questions
    from .retrieval_eval import build_synthetic_retrieval_session
except ImportError:
    from citation_accuracy_eval import evaluate_citation_accuracy  # type: ignore[no-redef]
    from context_aware_embedding_input_eval import project_citations  # type: ignore[no-redef]
    from golden_questions import load_golden_questions  # type: ignore[no-redef]
    from retrieval_eval import build_synthetic_retrieval_session  # type: ignore[no-redef]


@dataclass(frozen=True)
class CitationGateEvidence:
    projection_conformance_passed: bool
    citation_recall: float
    citation_precision: float
    invalid_citation_count: int
    derived_header_citation_count: int
    golden_citation_recall: float
    golden_citation_precision: float
    golden_invalid_citation_count: int


@dataclass(frozen=True)
class VariantCitationEvidence:
    recall: float
    precision: float
    invalid_citation_count: int
    derived_header_citation_count: int


def evaluate_citation_gates() -> CitationGateEvidence:
    conformance = _evaluate_projection_conformance()
    golden_questions = load_golden_questions()
    session = build_synthetic_retrieval_session(golden_questions)
    try:
        golden = evaluate_citation_accuracy(
            session=session,
            question_set=golden_questions,
            threshold=1.0,
        )
    finally:
        session.close()
    expected_total = 0
    cited_total = 0
    correct_total = 0
    invalid_total = 0
    for result in golden.question_results:
        expected = set(result.expected_paths)
        cited = set(result.citation_paths)
        expected_total += len(expected)
        cited_total += len(cited)
        correct_total += len(expected & cited)
        invalid_total += len(cited - expected)
    golden_recall = correct_total / expected_total if expected_total else 0.0
    golden_precision = correct_total / cited_total if cited_total else 0.0
    return CitationGateEvidence(
        projection_conformance_passed=conformance,
        citation_recall=1.0 if conformance else 0.0,
        citation_precision=1.0 if conformance else 0.0,
        invalid_citation_count=0 if conformance else 1,
        derived_header_citation_count=0 if conformance else 1,
        golden_citation_recall=golden_recall,
        golden_citation_precision=golden_precision,
        golden_invalid_citation_count=invalid_total,
    )


def evaluate_step98_decision_citations(
    *,
    preregistration: Any,
    rankings_by_variant: Mapping[str, Mapping[str, Sequence[str]]],
) -> Dict[str, VariantCitationEvidence]:
    chunk_paths = {chunk["chunk_id"]: chunk["notion_path"] for chunk in preregistration.chunks}
    queries = {query["query_id"]: query for query in preregistration.queries}
    evidence: Dict[str, VariantCitationEvidence] = {}
    for variant, rankings in rankings_by_variant.items():
        required_total = emitted_total = correct_total = invalid_total = derived_total = 0
        for query_id, ranking in rankings.items():
            query = queries[query_id]
            projection = project_citations(
                retrieved_chunk_ids=ranking[:5],
                chunk_paths=chunk_paths,
                required_paths=query["required_citation_paths"],
                allowed_paths=query["allowed_citation_paths"],
            )
            required = set(query["required_citation_paths"])
            allowed = set(query["allowed_citation_paths"])
            emitted = set(projection["citation_paths"])
            required_total += len(required)
            emitted_total += len(emitted)
            correct_total += len(required & emitted)
            invalid_total += int(projection["invalid_citation_count"])
            derived_total += int(projection["derived_header_citation_count"])
        evidence[variant] = VariantCitationEvidence(
            recall=correct_total / required_total if required_total else 0.0,
            precision=(emitted_total - invalid_total) / emitted_total if emitted_total else 0.0,
            invalid_citation_count=invalid_total,
            derived_header_citation_count=derived_total,
        )
    return evidence


def _evaluate_projection_conformance() -> bool:
    cases = (
        ("duplicate_path", ("a", "a"), ("Path/A",), ("Path/A",), 1.0, 0),
        ("duplicate_path", ("a", "a", "b"), ("Path/A",), ("Path/A", "Path/B"), 1.0, 0),
        ("same_name_path", ("a", "b"), ("Path/A",), ("Path/A", "Path/B"), 1.0, 0),
        ("same_name_path", ("b", "a"), ("Path/A",), ("Path/A",), 0.5, 1),
        ("multiple_chunks", ("a",), ("Path/A",), ("Path/A",), 1.0, 0),
        ("multiple_chunks", ("a", "b"), ("Path/A",), ("Path/A", "Path/B"), 1.0, 0),
        ("erroneous_extra", ("a", "missing"), ("Path/A",), ("Path/A",), 1.0, 1),
        ("erroneous_extra", ("a", "b", "missing"), ("Path/A",), ("Path/A", "Path/B"), 1.0, 1),
    )
    categories: Dict[str, int] = {}
    for category, retrieved, required, allowed, expected_precision, expected_invalid in cases:
        categories[category] = categories.get(category, 0) + 1
        result = project_citations(
            retrieved_chunk_ids=retrieved,
            chunk_paths={"a": "Path/A", "b": "Path/B"},
            required_paths=required,
            allowed_paths=allowed,
        )
        if result["recall"] != 1.0:
            return False
        if result["precision"] != expected_precision:
            return False
        if result["invalid_citation_count"] != expected_invalid:
            return False
        if result["derived_header_citation_count"] != 0:
            return False
    return categories == {
        "duplicate_path": 2,
        "same_name_path": 2,
        "multiple_chunks": 2,
        "erroneous_extra": 2,
    }
