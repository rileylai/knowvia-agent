from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from .schema import BenchmarkCase, EvidenceGroup, EvidenceLocation


FAILURE_LABELS = (
    "INGESTION_PARSER_FAILURE",
    "CHUNKING_EVIDENCE_BOUNDARY_FAILURE",
    "EMBEDDING_RANKING_FAILURE",
    "RELEVANCE_GATE_FAILURE",
    "SOURCE_DISCRIMINATION_FAILURE",
    "UNRESOLVED",
)
TOP_KS = (1, 3, 5)
_PAGE_PATTERN = re.compile(r"\bpage\s+(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RetrievalObservation:
    rank: int
    source_id: Optional[str]
    locator: str
    chunk_text: str
    score: float
    accepted: bool
    retrieval_mode: str
    source_document_id: Optional[int] = None
    page: Optional[int] = None
    candidate_count: Optional[int] = None


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    category: str
    answerable: bool
    passed: bool
    group_hits_at: Mapping[int, Mapping[str, bool]]
    first_required_rank: Optional[int]
    completion_rank: Optional[int]
    failure_label: str
    observations: tuple[RetrievalObservation, ...]
    required_sources: tuple[str, ...]
    required_pages: tuple[tuple[str, int], ...]


def evaluate_case(
    case: BenchmarkCase,
    observations: Iterable[RetrievalObservation],
) -> CaseEvaluation:
    bounded_observations = tuple(sorted(observations, key=lambda item: item.rank)[:5])
    if not case.answerable:
        accepted = any(item.accepted for item in bounded_observations)
        return CaseEvaluation(
            case_id=case.case_id,
            category=case.category,
            answerable=False,
            passed=not accepted,
            group_hits_at={},
            first_required_rank=None,
            completion_rank=None,
            failure_label=("NONE" if not accepted else "UNRESOLVED"),
            observations=bounded_observations,
            required_sources=tuple(),
            required_pages=tuple(),
        )

    group_hits_at: dict[int, dict[str, bool]] = {}
    first_ranks: list[int] = []
    for top_k in TOP_KS:
        top_observations = tuple(
            item for item in bounded_observations if item.accepted and item.rank <= top_k
        )
        group_hits: dict[str, bool] = {}
        for group in case.evidence_groups:
            location_hits = [
                _location_hit(location, top_observations)
                for location in group.locations
            ]
            group_hits[group.group_id] = (
                any(location_hits) if group.match == "any" else all(location_hits)
            )
        group_hits_at[top_k] = group_hits

    for group in case.evidence_groups:
        group_rank = _first_group_rank(group, bounded_observations)
        if group_rank is not None:
            first_ranks.append(group_rank)
    passed = all(group_hits_at[5].values())
    failure_label = "NONE" if passed else classify_failure(case, bounded_observations)
    group_ranks = [
        _first_group_rank(group, bounded_observations)
        for group in case.evidence_groups
    ]
    completion_rank = (
        max(rank for rank in group_ranks if rank is not None)
        if group_ranks and all(rank is not None for rank in group_ranks)
        else None
    )
    return CaseEvaluation(
        case_id=case.case_id,
        category=case.category,
        answerable=True,
        passed=passed,
        group_hits_at=group_hits_at,
        first_required_rank=min(first_ranks) if first_ranks else None,
        completion_rank=completion_rank,
        failure_label=failure_label,
        observations=bounded_observations,
        required_sources=tuple(
            sorted(
                {
                    location.source_id
                    for group in case.evidence_groups
                    for location in group.locations
                }
            )
        ),
        required_pages=tuple(
            sorted(
                {
                    (location.source_id, page)
                    for group in case.evidence_groups
                    for location in group.locations
                    for page in location.pages
                }
            )
        ),
    )


def classify_failure(
    case: BenchmarkCase,
    observations: tuple[RetrievalObservation, ...],
) -> str:
    accepted = tuple(item for item in observations if item.accepted)
    # Accepted-only observations do not expose rejected candidates or gate
    # decisions. Keep the taxonomy for future richer observations, but do not
    # infer a root cause from this pilot surface.
    _ = case, accepted
    return "UNRESOLVED"


def aggregate_metrics(
    cases: Iterable[CaseEvaluation],
) -> dict[str, object]:
    evaluations = tuple(cases)
    positives = tuple(item for item in evaluations if item.answerable)
    negatives = tuple(item for item in evaluations if not item.answerable)

    case_group_recall_at = {
        top_k: [
            _ratio(
                sum(1 for hit in item.group_hits_at[top_k].values() if hit),
                len(item.group_hits_at[top_k]),
            )
            for item in positives
        ]
        for top_k in TOP_KS
    }
    recall_at = {
        f"recall_at_{top_k}": _mean(case_group_recall_at[top_k])
        for top_k in TOP_KS
    }
    full_case_success_at = {
        f"full_case_success_at_{top_k}": _ratio(
            sum(
                1
                for item in positives
                if item.group_hits_at[top_k]
                and all(item.group_hits_at[top_k].values())
            ),
            len(positives),
        )
        for top_k in TOP_KS
    }
    reciprocal_ranks = [
        1 / item.first_required_rank if item.first_required_rank is not None else 0.0
        for item in positives
    ]
    source_recall_at_5 = _source_recall_at_5(evaluations)
    page_coverage_at_5 = _page_coverage_at_5(evaluations)
    negative_rejection_rate = _ratio(
        sum(1 for item in negatives if item.passed),
        len(negatives),
    )
    failure_counts: dict[str, int] = defaultdict(int)
    for item in evaluations:
        if item.failure_label != "NONE":
            failure_counts[item.failure_label] += 1

    category_metrics: dict[str, dict[str, object]] = {}
    categories = sorted({item.category for item in evaluations})
    for category in categories:
        category_cases = tuple(item for item in evaluations if item.category == category)
        category_metrics[category] = {
            "case_count": len(category_cases),
            "passed_count": sum(1 for item in category_cases if item.passed),
            "pass_rate": _ratio(
                sum(1 for item in category_cases if item.passed),
                len(category_cases),
            ),
        }

    return {
        "case_count": len(evaluations),
        "positive_case_count": len(positives),
        "negative_case_count": len(negatives),
        "passed_count": sum(1 for item in evaluations if item.passed),
        "recall_at_1": recall_at["recall_at_1"],
        "recall_at_3": recall_at["recall_at_3"],
        "recall_at_5": recall_at["recall_at_5"],
        "mrr": _mean(reciprocal_ranks),
        "full_case_success_at_1": full_case_success_at["full_case_success_at_1"],
        "full_case_success_at_3": full_case_success_at["full_case_success_at_3"],
        "full_case_success_at_5": full_case_success_at["full_case_success_at_5"],
        "source_recall_at_5": source_recall_at_5,
        "page_coverage_at_5": page_coverage_at_5,
        "negative_rejection_rate": negative_rejection_rate,
        "false_positive_retrieval_rate": (
            1 - negative_rejection_rate if negatives else None
        ),
        "failure_counts": dict(sorted(failure_counts.items())),
        "by_category": category_metrics,
    }


def build_bounded_report(
    benchmark_id: str,
    benchmark_version: str,
    contract: Optional[Mapping[str, object]],
    corpus_manifest: list[dict[str, object]],
    evaluations: Iterable[CaseEvaluation],
) -> dict[str, object]:
    case_results: list[dict[str, object]] = []
    evaluation_list = tuple(evaluations)
    for item in evaluation_list:
        case_results.append(
            {
                "case_id": item.case_id,
                "category": item.category,
                "answerable": item.answerable,
                "passed": item.passed,
                "first_required_rank": item.first_required_rank,
                "completion_rank": item.completion_rank,
                "failure_label": item.failure_label,
                "group_hits_at": {
                    str(top_k): dict(item.group_hits_at[top_k])
                    for top_k in TOP_KS
                    if top_k in item.group_hits_at
                },
                "observations": [
                    {
                        "rank": observation.rank,
                        "source_id": observation.source_id,
                        "locator": observation.locator,
                        "page": observation.page,
                        "score": round(observation.score, 6),
                        "accepted": observation.accepted,
                        "retrieval_mode": observation.retrieval_mode,
                        "candidate_count": observation.candidate_count,
                    }
                    for observation in item.observations
                ],
            }
        )
    return {
        "benchmark_id": benchmark_id,
        "benchmark_version": benchmark_version,
        "contract": dict(contract or {}),
        "top_k": list(TOP_KS),
        "corpus": corpus_manifest,
        "metrics": aggregate_metrics(evaluation_list),
        "cases": case_results,
    }


def _location_hit(
    location: EvidenceLocation,
    observations: tuple[RetrievalObservation, ...],
) -> bool:
    for observation in observations:
        if observation.source_id != location.source_id:
            continue
        if observation.page not in location.pages:
            continue
        normalized_chunk = normalize_for_match(observation.chunk_text)
        if any(
            normalize_for_match(anchor) in normalized_chunk
            for anchor in location.anchors
        ):
            return True
    return False


def _first_group_rank(
    group: EvidenceGroup,
    observations: tuple[RetrievalObservation, ...],
) -> Optional[int]:
    if group.match == "any":
        ranks = [
            observation.rank
            for location in group.locations
            for observation in observations
            if observation.accepted and _location_hit(location, (observation,))
        ]
        return min(ranks) if ranks else None
    ranks: list[int] = []
    for location in group.locations:
        matching = [
            observation.rank
            for observation in observations
            if observation.accepted and _location_hit(location, (observation,))
        ]
        if not matching:
            return None
        ranks.append(min(matching))
    return max(ranks) if ranks else None


def _source_recall_at_5(evaluations: tuple[CaseEvaluation, ...]) -> Optional[float]:
    source_ratios: list[float] = []
    for evaluation in evaluations:
        if not evaluation.answerable:
            continue
        expected_sources = set(evaluation.required_sources)
        observed_sources = {
            observation.source_id
            for observation in evaluation.observations
            if observation.accepted and observation.rank <= 5
        }
        if not expected_sources:
            continue
        source_ratios.append(len(expected_sources.intersection(observed_sources)) / len(expected_sources))
    return _mean(source_ratios)


def _page_coverage_at_5(evaluations: tuple[CaseEvaluation, ...]) -> Optional[float]:
    page_ratios: list[float] = []
    for evaluation in evaluations:
        if not evaluation.answerable or not evaluation.required_pages:
            continue
        observed_pages = {
            (observation.source_id, observation.page)
            for observation in evaluation.observations
            if observation.accepted and observation.rank <= 5
        }
        expected_pages = set(evaluation.required_pages)
        page_ratios.append(len(expected_pages.intersection(observed_pages)) / len(expected_pages))
    return _mean(page_ratios)


def normalize_for_match(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def page_from_locator(locator: Optional[str]) -> Optional[int]:
    if not locator:
        return None
    match = _PAGE_PATTERN.search(locator)
    return int(match.group(1)) if match else None


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: Iterable[float]) -> Optional[float]:
    values_list = list(values)
    return sum(values_list) / len(values_list) if values_list else None
