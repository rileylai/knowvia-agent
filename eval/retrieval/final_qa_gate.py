from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.orchestrators.qa_orchestrator import (
    DEFAULT_QA_MODEL,
    DEFAULT_QA_PROVIDER_NAME,
    QAOrchestrator,
)
from src.providers import (
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    OpenAIClient,
    OpenAIEmbeddingClient,
    ProviderRouter,
)
from src.rag import ProductionChunkRetriever, RetrievalResult
from src.repositories.chunk_repository import ChunkRepository
from src.services import (
    CostTracker,
    EmbeddingBatchService,
    KnowledgeIndexingService,
    PromptTemplateLoader,
    WorkflowRunService,
)

from .metrics import RetrievalObservation, page_from_locator
from .runner import (
    DEFAULT_ADMIN_DATABASE_URL,
    VECTOR_DIMENSIONS,
    VECTOR_MODEL,
    RetrievalBenchmarkError,
    _TemporaryEvaluationDatabase,
    _verify_production_contract,
    verify_corpus,
)
from .schema import BenchmarkCase, RetrievalBenchmark
from .topk_tradeoff import _first_group_rank, _group_hit


FINAL_QA_TOP_KS = (5, 8)
FINAL_QA_ARTIFACT_TYPE = "retrieval_top_k_final_qa_variant"
FINAL_QA_COMPARISON_ARTIFACT_TYPE = "retrieval_top_k_final_qa_comparison"
FINAL_QA_LABELS = (
    "CORRECT_GROUNDED",
    "INSUFFICIENT_DESPITE_AVAILABLE_GOLD",
    "UNSUPPORTED_OR_INCORRECT",
    "RETRIEVAL_INCOMPLETE",
)
HARD_NEGATIVE_LABELS = ("SAFE_REJECTION", "UNSAFE_UNSUPPORTED_ANSWER")
CITATION_LABELS = (
    "SUPPORTED_CITATIONS",
    "CITATIONS_DO_NOT_SUPPORT_CLAIM",
)

class FinalQAGateError(RuntimeError):
    pass


class _QueryEmbeddingReplayClient(EmbeddingClient):
    """Evaluation-only replay of one already-computed embedding per query."""

    def __init__(self, embeddings: Mapping[str, Sequence[float]]) -> None:
        self._embeddings = {key.strip(): list(value) for key, value in embeddings.items()}

    @property
    def name(self) -> str:
        return "openai"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if len(request.inputs) != 1:
            raise FinalQAGateError("QA query embedding replay expects one input")
        query = request.inputs[0].strip()
        embedding = self._embeddings.get(query)
        if embedding is None:
            raise FinalQAGateError("QA query embedding replay missed a benchmark query")
        return EmbeddingResponse(
            provider="openai",
            model=VECTOR_MODEL,
            embeddings=[list(embedding)],
            indices=[0],
        )


class _RecordingRetriever:
    """Evaluation-only seam that records the exact QA retrieval result."""

    def __init__(self, delegate: ProductionChunkRetriever) -> None:
        self._delegate = delegate
        self.last_result: Optional[RetrievalResult] = None

    def retrieve_with_metadata(self, **kwargs: Any) -> RetrievalResult:
        result = self._delegate.retrieve_with_metadata(**kwargs)
        self.last_result = result
        return result


class _TimingProvider(LLMProvider):
    """Record only bounded latency around the existing provider call."""

    def __init__(self, delegate: OpenAIClient) -> None:
        self._delegate = delegate
        self.last_latency_ms: Optional[float] = None

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def supports_tool_calling(self) -> bool:
        return self._delegate.supports_tool_calling

    @property
    def supports_structured_output(self) -> bool:
        return self._delegate.supports_structured_output

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        response = await self._delegate.generate(request)
        self.last_latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return response


@dataclass(frozen=True)
class _VariantRun:
    report: dict[str, object]
    cases: tuple[dict[str, object], ...]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _observations_from_result(
    result: RetrievalResult,
    source_id_by_document_id: Mapping[int, str],
) -> tuple[RetrievalObservation, ...]:
    observations: list[RetrievalObservation] = []
    for rank, chunk in enumerate(result.chunks, start=1):
        observations.append(
            RetrievalObservation(
                rank=rank,
                source_id=(
                    source_id_by_document_id.get(chunk.source_document_id)
                    if chunk.source_document_id is not None
                    else None
                ),
                locator=chunk.locator or "",
                chunk_text=chunk.chunk_text,
                score=chunk.score,
                accepted=True,
                retrieval_mode=result.retrieval_mode,
                source_document_id=chunk.source_document_id,
                page=page_from_locator(chunk.locator),
                candidate_count=result.candidate_count,
            )
        )
    return tuple(observations)


def _retrieved_metadata(
    result: RetrievalResult,
    source_id_by_document_id: Mapping[int, str],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for rank, chunk in enumerate(result.chunks, start=1):
        items.append(
            {
                "rank": rank,
                "source_id": (
                    source_id_by_document_id.get(chunk.source_document_id)
                    if chunk.source_document_id is not None
                    else None
                ),
                "source_kind": chunk.source_kind,
                "source_display_name": chunk.source_display_name,
                "page": page_from_locator(chunk.locator),
                "locator": chunk.locator,
                "score": round(chunk.score, 6),
            }
        )
    return items


def _citation_metadata(
    citations: Sequence[Any],
    source_id_by_display_name: Mapping[str, str],
) -> list[dict[str, object]]:
    return [
        {
            "source_id": source_id_by_display_name.get(citation.source_display_name or ""),
            "source_kind": citation.source_kind,
            "source_display_name": citation.source_display_name,
            "page_id": citation.page_id,
            "page": page_from_locator(citation.locator),
            "locator": citation.locator,
            "score": citation.score,
        }
        for citation in citations
    ]


def _default_classification(
    *,
    case: BenchmarkCase,
    gold_complete: bool,
    insufficient_info: bool,
) -> str:
    if not case.answerable:
        raise FinalQAGateError("positive classification requested for a negative case")
    if not gold_complete:
        return "RETRIEVAL_INCOMPLETE"
    if insufficient_info:
        return "INSUFFICIENT_DESPITE_AVAILABLE_GOLD"
    return "CORRECT_GROUNDED"


def apply_bounded_human_review(
    case: dict[str, object],
    *,
    review_decision: Mapping[str, object],
) -> dict[str, object]:
    """Apply one explicit offline human decision to a saved case."""
    if "automated_classification" not in case:
        raise FinalQAGateError(
            "human review requires preserved automated_classification"
        )
    reviewed_classification = review_decision.get("reviewed_classification")
    if not isinstance(reviewed_classification, str):
        raise FinalQAGateError(
            "review_decision.reviewed_classification must be a string"
        )
    allowed_labels = FINAL_QA_LABELS if case["answerable"] else HARD_NEGATIVE_LABELS
    if reviewed_classification not in allowed_labels:
        raise FinalQAGateError(
            "review_decision.reviewed_classification is invalid for case type"
        )
    basis = review_decision.get("basis")
    if not isinstance(basis, str) or not basis.strip():
        raise FinalQAGateError("review_decision.basis must be non-empty")
    if (
        case["answerable"]
        and not case["insufficient_info"]
        and "citation_support" not in review_decision
    ):
        raise FinalQAGateError(
            "non-insufficient answer review requires citation_support"
        )
    citation_support = review_decision.get("citation_support")
    if citation_support is not None and citation_support not in CITATION_LABELS:
        raise FinalQAGateError("review_decision.citation_support is invalid")

    case["reviewed_classification"] = reviewed_classification
    case["bounded_classification"] = case["reviewed_classification"]
    case["classification_authority"] = "human_reviewed"
    if "citation_support" in review_decision:
        case["citation_support"] = citation_support
    case["human_review"] = {
        "status": "REVIEWED",
        "basis": basis.strip(),
        "application_mode": "offline_post_processing",
        "reviewed_classification_field": "reviewed_classification",
    }
    return case


def _aggregate_classification(case: Mapping[str, object]) -> str:
    reviewed = case.get("reviewed_classification")
    if reviewed is not None:
        return str(reviewed)
    return str(case.get("automated_classification", case["bounded_classification"]))


def _build_case_record(
    *,
    case: BenchmarkCase,
    top_k: int,
    result: Any,
    retrieval_result: RetrievalResult,
    source_id_by_document_id: Mapping[int, str],
    source_id_by_display_name: Mapping[str, str],
    llm_latency_ms: Optional[float],
) -> dict[str, object]:
    observations = _observations_from_result(
        retrieval_result,
        source_id_by_document_id,
    )
    group_hits = {
        group.group_id: _group_hit(group, observations)
        for group in case.evidence_groups
    }
    group_first_rank = {
        group.group_id: _first_group_rank(group, observations)
        for group in case.evidence_groups
    }
    gold_complete = (
        all(group_hits.values()) if case.answerable else False
    )
    record: dict[str, object] = {
        "case_id": case.case_id,
        "answerable": case.answerable,
        "category": case.category,
        "requested_top_k": top_k,
        "retrieval": {
            "accepted_evidence_count": len(retrieval_result.chunks),
            "candidate_count": retrieval_result.candidate_count,
            "retrieval_mode": retrieval_result.retrieval_mode,
            "relevance_floor": retrieval_result.relevance_floor,
            "group_hits": group_hits,
            "group_first_rank": group_first_rank,
            "gold_complete": gold_complete,
            "evidence": _retrieved_metadata(
                retrieval_result,
                source_id_by_document_id,
            ),
        },
        "final_answer": result.answer,
        "insufficient_info": result.insufficient_info,
        "citations": {
            "count": len(result.citations),
            "evidence": _citation_metadata(
                result.citations,
                source_id_by_display_name,
            ),
            "citation_cleared_for_insufficient_info": (
                not result.insufficient_info or not result.citations
            ),
        },
        "operational": {
            "llm_provider": result.provider,
            "llm_model": result.model,
            "llm_token_input": result.token_input,
            "llm_token_output": result.token_output,
            "llm_latency_ms": llm_latency_ms,
        },
    }
    if case.answerable:
        record["automated_classification"] = _default_classification(
            case=case,
            gold_complete=gold_complete,
            insufficient_info=result.insufficient_info,
        )
    else:
        safe_rejection = bool(result.insufficient_info)
        record["automated_classification"] = (
            "SAFE_REJECTION" if safe_rejection else "UNSAFE_UNSUPPORTED_ANSWER"
        )
    record["reviewed_classification"] = None
    record["bounded_classification"] = record["automated_classification"]
    record["classification_authority"] = "automated"
    record["citation_support"] = None
    record["human_review"] = {
        "status": "PENDING",
        "basis": None,
        "application_mode": "live_automated_only",
        "reviewed_classification_field": "reviewed_classification",
    }
    return record


def _aggregate_variant(cases: Sequence[Mapping[str, object]]) -> dict[str, object]:
    positives = [case for case in cases if case["answerable"]]
    negatives = [case for case in cases if not case["answerable"]]
    positive_labels = [_aggregate_classification(case) for case in positives]
    safe_negatives = [
        case for case in negatives if _aggregate_classification(case) == "SAFE_REJECTION"
    ]
    unsupported_with_citations = [
        case
        for case in positives
        if _aggregate_classification(case) == "UNSUPPORTED_OR_INCORRECT"
        and int(case["citations"]["count"]) > 0
    ]
    insufficient_with_citations = [
        case
        for case in cases
        if case["insufficient_info"] and int(case["citations"]["count"]) > 0
    ]
    supported_citation_cases = [
        case
        for case in positives
        if case["citation_support"] == "SUPPORTED_CITATIONS"
    ]
    return {
        "answerable": {
            "case_count": len(positives),
            "correct_grounded_count": positive_labels.count("CORRECT_GROUNDED"),
            "correct_grounded_rate": round(
                positive_labels.count("CORRECT_GROUNDED") / len(positives),
                6,
            )
            if positives
            else 0.0,
            "insufficient_despite_available_gold_count": positive_labels.count(
                "INSUFFICIENT_DESPITE_AVAILABLE_GOLD"
            ),
            "unsupported_or_incorrect_count": positive_labels.count(
                "UNSUPPORTED_OR_INCORRECT"
            ),
            "retrieval_incomplete_count": positive_labels.count(
                "RETRIEVAL_INCOMPLETE"
            ),
            "classification_counts": {
                label: positive_labels.count(label) for label in FINAL_QA_LABELS
            },
        },
        "hard_negatives": {
            "case_count": len(negatives),
            "safe_rejection_count": len(safe_negatives),
            "unsafe_answer_count": len(negatives) - len(safe_negatives),
            "safe_rejection_rate": round(len(safe_negatives) / len(negatives), 6)
            if negatives
            else 0.0,
            "unsafe_case_ids": [
                case["case_id"]
                for case in negatives
                if _aggregate_classification(case) == "UNSAFE_UNSUPPORTED_ANSWER"
            ],
            "citation_counts": {
                case["case_id"]: case["citations"]["count"] for case in negatives
            },
        },
        "citations": {
            "supported_citation_case_count": len(supported_citation_cases),
            "unsupported_answer_with_citations_count": len(unsupported_with_citations),
            "insufficient_info_with_nonzero_citations_count": len(
                insufficient_with_citations
            ),
        },
        "operational": {
            "average_llm_latency_ms": _mean_optional(
                case["operational"]["llm_latency_ms"] for case in cases
            ),
            "total_llm_token_input": _sum_optional(
                case["operational"]["llm_token_input"] for case in cases
            ),
            "total_llm_token_output": _sum_optional(
                case["operational"]["llm_token_output"] for case in cases
            ),
            "estimated_llm_cost_usd": _estimated_cost(cases),
        },
    }


def _mean_optional(values: Sequence[object] | Any) -> Optional[float]:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    return round(sum(numeric) / len(numeric), 3) if numeric else None


def _sum_optional(values: Sequence[object] | Any) -> Optional[int]:
    numeric = [int(value) for value in values if isinstance(value, int)]
    return sum(numeric) if numeric else None


def _estimated_cost(cases: Sequence[Mapping[str, object]]) -> Optional[float]:
    tracker = CostTracker()
    costs = []
    for case in cases:
        operational = case["operational"]
        cost = tracker.estimate_llm_cost(
            provider_name=str(operational["llm_provider"] or "openai"),
            model=str(operational["llm_model"] or DEFAULT_QA_MODEL),
            token_input=operational["llm_token_input"],
            token_output=operational["llm_token_output"],
        )
        if cost is not None:
            costs.append(cost)
    return round(sum(costs), 12) if costs else None


def _build_report(
    *,
    benchmark: RetrievalBenchmark,
    benchmark_path: Path,
    corpus_manifest: list[dict[str, object]],
    top_k: int,
    cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    review_pending = any(
        case.get("reviewed_classification") is None for case in cases
    )
    review_applied = any(
        case.get("reviewed_classification") is not None for case in cases
    )
    if review_pending or not review_applied:
        review_provenance = {
            "application_mode": "live_automated_only",
            "review_status": "PENDING",
            "classification_authority": "automated",
            "automated_classification_field": "automated_classification",
            "reviewed_classification_field": "reviewed_classification",
            "aggregate_metrics_use": "automated_classification",
        }
    else:
        review_provenance = {
            "application_mode": "offline_post_processing",
            "review_status": "REVIEWED",
            "classification_authority": "human_reviewed",
            "automated_classification_field": "automated_classification",
            "reviewed_classification_field": "reviewed_classification",
            "aggregate_metrics_use": "reviewed_classification",
        }
    return {
        "artifact_type": FINAL_QA_ARTIFACT_TYPE,
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_version": benchmark.version,
        "benchmark_sha256": _sha256_file(benchmark_path),
        "variant": {"requested_top_k": top_k},
        "controls": {
            "qa_path": "QAOrchestrator.answer_question",
            "qa_prompt_version": "qa_answer_v3",
            "qa_provider": DEFAULT_QA_PROVIDER_NAME,
            "qa_model": DEFAULT_QA_MODEL,
            "parser": "PyPDFParserClient",
            "chunk_max_chars": benchmark.contract.chunk_max_chars,
            "chunk_overlap_chars": benchmark.contract.chunk_overlap_chars,
            "page_aware": benchmark.contract.page_aware,
            "embedding_model": VECTOR_MODEL,
            "embedding_dimensions": VECTOR_DIMENSIONS,
            "similarity": benchmark.contract.similarity,
            "candidate_pool": "min(top_k * 2, 20)",
            "relevance_floor": benchmark.contract.relevance_floor,
            "owner_scope": "local",
            "final_qa_structured_output": False,
            "production_change": False,
        },
        "review_provenance": review_provenance,
        "corpus": corpus_manifest,
        "metrics": _aggregate_variant(cases),
        "cases": list(cases),
    }


def _case_delta(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> str:
    if baseline["answerable"]:
        if (
            _aggregate_classification(baseline) != "CORRECT_GROUNDED"
            and _aggregate_classification(candidate) == "CORRECT_GROUNDED"
        ):
            return "IMPROVED_FINAL_QA"
        if (
            _aggregate_classification(baseline) == "CORRECT_GROUNDED"
            and _aggregate_classification(candidate) != "CORRECT_GROUNDED"
        ):
            return "REGRESSED_FINAL_QA"
    else:
        if (
            _aggregate_classification(baseline) == "SAFE_REJECTION"
            and _aggregate_classification(candidate) == "UNSAFE_UNSUPPORTED_ANSWER"
        ):
            return "REGRESSED_FINAL_QA"
        if (
            _aggregate_classification(baseline) == "UNSAFE_UNSUPPORTED_ANSWER"
            and _aggregate_classification(candidate) == "SAFE_REJECTION"
        ):
            return "IMPROVED_FINAL_QA"
    if (
        baseline["citation_support"] != candidate["citation_support"]
        and candidate["citation_support"] == "CITATIONS_DO_NOT_SUPPORT_CLAIM"
    ):
        return "REGRESSED_FINAL_QA"
    return "UNCHANGED"


def build_comparison_report(
    *,
    benchmark: RetrievalBenchmark,
    benchmark_path: Path,
    variant_reports: Mapping[int, Mapping[str, object]],
) -> dict[str, object]:
    baseline_cases = {case["case_id"]: case for case in variant_reports[5]["cases"]}
    candidate_cases = {case["case_id"]: case for case in variant_reports[8]["cases"]}
    deltas = []
    for case_id in baseline_cases:
        baseline = baseline_cases[case_id]
        candidate = candidate_cases[case_id]
        deltas.append(
            {
                "case_id": case_id,
                "answerable": baseline["answerable"],
                "k5_classification": _aggregate_classification(baseline),
                "k8_classification": _aggregate_classification(candidate),
                "delta": _case_delta(baseline, candidate),
                "k5_insufficient_info": baseline["insufficient_info"],
                "k8_insufficient_info": candidate["insufficient_info"],
                "k5_citation_count": baseline["citations"]["count"],
                "k8_citation_count": candidate["citations"]["count"],
            }
        )
    k5_unsafe = set(variant_reports[5]["metrics"]["hard_negatives"]["unsafe_case_ids"])
    k8_unsafe = set(variant_reports[8]["metrics"]["hard_negatives"]["unsafe_case_ids"])
    k5_positive = variant_reports[5]["metrics"]["answerable"]
    k8_positive = variant_reports[8]["metrics"]["answerable"]
    k5_citations = variant_reports[5]["metrics"]["citations"]
    k8_citations = variant_reports[8]["metrics"]["citations"]
    unsafe_new = sorted(k8_unsafe - k5_unsafe)
    citation_regression = (
        k8_citations["unsupported_answer_with_citations_count"]
        > k5_citations["unsupported_answer_with_citations_count"]
        or k8_citations["insufficient_info_with_nonzero_citations_count"]
        > k5_citations["insufficient_info_with_nonzero_citations_count"]
    )
    improved = [item for item in deltas if item["delta"] == "IMPROVED_FINAL_QA"]
    regressions = [item for item in deltas if item["delta"] == "REGRESSED_FINAL_QA"]
    semantic_recovered = ["rv-004", "rv-005", "rv-023"]
    semantic_improvements = [
        item for item in improved if item["case_id"] in semantic_recovered
    ]
    pending_review = any(
        case.get("reviewed_classification") is None
        for report in variant_reports.values()
        for case in report["cases"]
    )
    if pending_review:
        adoption = "PENDING_HUMAN_REVIEW"
        adoption_reason = (
            "Live evaluation contains automated classifications only; explicit "
            "offline human adjudication is required."
        )
    elif (
        semantic_improvements
        and not regressions
        and not unsafe_new
        and not citation_regression
    ):
        adoption = "ADOPT_TOP_K_8"
        adoption_reason = (
            "k=8 improves final QA on recovered semantic cases without positive "
            "regression, new hard-negative unsafe answers, or citation regression."
        )
    elif unsafe_new:
        adoption = "KEEP_TOP_K_5"
        adoption_reason = (
            "k=8 introduces new hard-negative unsafe answers; retrieval gain is "
            "not safe for adoption."
        )
    elif regressions or citation_regression:
        adoption = "KEEP_TOP_K_5"
        adoption_reason = (
            "k=8 does not satisfy the final-QA no-regression or citation-support gate."
        )
    elif not semantic_improvements:
        adoption = "KEEP_TOP_K_5"
        adoption_reason = (
            "Retrieval recovery did not translate into meaningful final-QA improvement "
            "on the recovered semantic cases."
        )
    else:
        adoption = "INCONCLUSIVE"
        adoption_reason = "Paired final-QA evidence does not satisfy a clear adoption rule."
    return {
        "artifact_type": FINAL_QA_COMPARISON_ARTIFACT_TYPE,
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_version": benchmark.version,
        "benchmark_sha256": _sha256_file(benchmark_path),
        "controls": {
            "qa_path": "QAOrchestrator.answer_question",
            "qa_prompt_version": "qa_answer_v3",
            "qa_provider": DEFAULT_QA_PROVIDER_NAME,
            "qa_model": DEFAULT_QA_MODEL,
            "variants": [5, 8],
            "candidate_pool": "min(top_k * 2, 20)",
            "production_change": False,
        },
        "review_provenance": {
            "application_mode": variant_reports[5].get(
                "review_provenance", {}
            ).get("application_mode", "unknown"),
            "review_status": "PENDING" if pending_review else "REVIEWED",
            "classification_authority": (
                "automated" if pending_review else "human_reviewed"
            ),
            "automated_classification_field": "automated_classification",
            "reviewed_classification_field": "reviewed_classification",
            "aggregate_metrics_use": (
                "automated_classification"
                if pending_review
                else "reviewed_classification"
            ),
        },
        "corpus": benchmark.corpus and [
            {
                "source_id": source.source_id,
                "filename": source.filename,
                "sha256": source.sha256,
                "page_count": source.page_count,
            }
            for source in benchmark.corpus
        ],
        "positive_metrics_by_top_k": {
            "5": k5_positive,
            "8": k8_positive,
        },
        "hard_negative_safety_by_top_k": {
            "5": variant_reports[5]["metrics"]["hard_negatives"],
            "8": variant_reports[8]["metrics"]["hard_negatives"],
        },
        "citation_support_by_top_k": {"5": k5_citations, "8": k8_citations},
        "case_level_deltas": deltas,
        "recovered_semantic_cases": {
            case_id: {
                "k5": baseline_cases[case_id],
                "k8": candidate_cases[case_id],
            }
            for case_id in semantic_recovered
        },
        "remaining_positive_failures": {
            case_id: {
                "k5": baseline_cases[case_id],
                "k8": candidate_cases[case_id],
            }
            for case_id in ("rv-009", "rv-026", "rv-027", "rv-029")
        },
        "strong_positive_categories": {
            category: {
                "k5_correct_grounded_case_ids": [
                    case["case_id"]
                    for case in variant_reports[5]["cases"]
                    if case["category"] == category
                    and _aggregate_classification(case) == "CORRECT_GROUNDED"
                ],
                "k8_correct_grounded_case_ids": [
                    case["case_id"]
                    for case in variant_reports[8]["cases"]
                    if case["category"] == category
                    and _aggregate_classification(case) == "CORRECT_GROUNDED"
                ],
            }
            for category in (
                "single_document_factual",
                "exact_term_identifier",
                "cross_source_discrimination",
            )
        },
        "new_unsafe_hard_negative_case_ids": unsafe_new,
        "new_citation_regression": citation_regression,
        "adoption_decision": {
            "decision": adoption,
            "reason": adoption_reason,
            "production_change": False,
        },
    }


async def run_live_final_qa_gate(
    benchmark: RetrievalBenchmark,
    *,
    benchmark_path: Path,
    corpus_root: Path,
    openai_api_key: str,
    admin_database_url: str = DEFAULT_ADMIN_DATABASE_URL,
    keep_database: bool = False,
) -> tuple[dict[str, object], dict[int, dict[str, object]]]:
    verified = verify_corpus(benchmark, corpus_root=corpus_root)
    _verify_production_contract(benchmark)
    temporary_database = _TemporaryEvaluationDatabase(
        admin_database_url=admin_database_url,
    )
    engine = None
    try:
        database_url = temporary_database.create_database()
        engine = create_engine(database_url)
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        unit_of_work_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
        embedding_client = OpenAIEmbeddingClient(
            api_key=openai_api_key,
            default_model=VECTOR_MODEL,
        )
        embedding_service = EmbeddingBatchService(
            embedding_client=embedding_client,
            model=VECTOR_MODEL,
            dimensions=VECTOR_DIMENSIONS,
        )
        indexing_service = KnowledgeIndexingService(
            unit_of_work_factory=unit_of_work_factory,
            embedding_batch_service=embedding_service,
        )
        source_id_by_document_id: dict[int, str] = {}
        source_id_by_display_name = {
            source.filename: source.source_id for source in benchmark.corpus
        }
        for source in benchmark.corpus:
            parsed = verified.parsed_documents[source.source_id]
            pdf_path = corpus_root / source.filename
            file_bytes = pdf_path.read_bytes()
            with unit_of_work_factory() as unit_of_work:
                document = unit_of_work.source_documents.create_source_document(
                    source_type="pdf",
                    source_display_name=source.filename,
                    original_filename=source.filename,
                    raw_text=parsed.raw_text,
                    content_hash=hashlib.sha256(parsed.raw_text.encode("utf-8")).hexdigest(),
                    file_hash=source.sha256,
                    owner_scope="local",
                    status="indexing",
                )
                source_document_id = int(document.id)
            await indexing_service.index_source_document(
                source_document_id=source_document_id,
                source_kind="pdf",
                source_display_name=source.filename,
                raw_text=parsed.raw_text,
                request_workflow_id=f"retrieval-final-qa-{source.source_id}",
                pages=parsed.pages,
                owner_scope="local",
            )
            source_id_by_document_id[source_document_id] = source.source_id

        queries = [case.query for case in benchmark.cases]
        query_embedding_result = await embedding_service.embed(
            queries,
            metadata={
                "operation": "retrieval_top_k_final_qa_gate",
                "benchmark_id": benchmark.benchmark_id,
            },
        )
        query_embeddings = dict(zip(queries, query_embedding_result.embeddings))
        replay_embedding_client = _QueryEmbeddingReplayClient(query_embeddings)
        provider_router = ProviderRouter()
        timing_provider = _TimingProvider(OpenAIClient(api_key=openai_api_key))
        provider_router.register_provider(timing_provider)
        variant_reports: dict[int, dict[str, object]] = {}
        with session_factory() as session:
            recording_retriever = _RecordingRetriever(
                ProductionChunkRetriever(chunk_repository=ChunkRepository(session))
            )
            orchestrator = QAOrchestrator(
                retriever=recording_retriever,
                embedding_client=replay_embedding_client,
                provider_router=provider_router,
                cost_tracker=CostTracker(),
                prompt_template_loader=PromptTemplateLoader(),
                workflow_run_service=WorkflowRunService(session_factory),
            )
            for top_k in FINAL_QA_TOP_KS:
                case_records: list[dict[str, object]] = []
                for case in benchmark.cases:
                    timing_provider.last_latency_ms = None
                    result = await orchestrator.answer_question(
                        query=case.query,
                        top_k=top_k,
                        page_ids=None,
                        section_paths=None,
                        source_kinds=["pdf"],
                        provider_name=DEFAULT_QA_PROVIDER_NAME,
                        model=DEFAULT_QA_MODEL,
                        request_workflow_id=f"retrieval-final-qa-{top_k}-{case.case_id}",
                        owner_scope="local",
                    )
                    if recording_retriever.last_result is None:
                        raise FinalQAGateError(
                            f"QA retrieval result missing for {case.case_id}"
                        )
                    case_records.append(
                        _build_case_record(
                            case=case,
                            top_k=top_k,
                            result=result,
                            retrieval_result=recording_retriever.last_result,
                            source_id_by_document_id=source_id_by_document_id,
                            source_id_by_display_name=source_id_by_display_name,
                            llm_latency_ms=timing_provider.last_latency_ms,
                        )
                    )
                variant_reports[top_k] = _build_report(
                    benchmark=benchmark,
                    benchmark_path=benchmark_path,
                    corpus_manifest=verified.manifest,
                    top_k=top_k,
                    cases=case_records,
                )
        comparison = build_comparison_report(
            benchmark=benchmark,
            benchmark_path=benchmark_path,
            variant_reports=variant_reports,
        )
        return comparison, variant_reports
    finally:
        if engine is not None:
            engine.dispose()
        temporary_database.close(keep_database=keep_database)


def write_json_report(report: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
