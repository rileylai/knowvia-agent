from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.providers.embedding import OpenAIEmbeddingClient
from src.rag.retriever import ProductionChunkRetriever
from src.repositories.chunk_repository import ChunkRepository
from src.services.embedding_batch_service import EmbeddingBatchService
from src.services.knowledge_indexing_service import KnowledgeIndexingService

from .metrics import RetrievalObservation, normalize_for_match
from .runner import (
    DEFAULT_ADMIN_DATABASE_URL,
    VECTOR_DIMENSIONS,
    VECTOR_MODEL,
    RetrievalBenchmarkError,
    _TemporaryEvaluationDatabase,
    _observations_from_result,
    _verify_production_contract,
    verify_corpus,
)
from .schema import BenchmarkCase, EvidenceGroup, RetrievalBenchmark

TRADEOFF_TOP_KS = (5, 8, 10)
CANONICAL_FAILED_POSITIVE_CASE_IDS = (
    "rv-004",
    "rv-005",
    "rv-009",
    "rv-023",
    "rv-026",
    "rv-027",
    "rv-029",
)
CANONICAL_METRICS = {
    "recall_at_5": 0.700,
    "full_case_success_at_5": 0.650,
    "mrr": 0.625,
    "source_recall_at_5": 0.950,
    "page_coverage_at_5": 0.750,
}
VECTOR_CANDIDATE_POOL_MAX = 20


def run_variant_cases(
    benchmark: RetrievalBenchmark,
    *,
    query_embeddings: Sequence[Sequence[float]],
    retriever,
    source_id_by_document_id: Mapping[int, str],
    top_k: int,
) -> tuple[dict[str, object], ...]:
    if top_k not in TRADEOFF_TOP_KS:
        raise RetrievalBenchmarkError(f"unsupported tradeoff top_k: {top_k}")
    if len(query_embeddings) != len(benchmark.cases):
        raise RetrievalBenchmarkError("query embedding count does not match benchmark cases")

    results: list[dict[str, object]] = []
    for case, query_embedding in zip(benchmark.cases, query_embeddings):
        started = time.perf_counter()
        retrieval_result = retriever.retrieve_with_metadata(
            query_text=case.query,
            top_k=top_k,
            query_embedding=list(query_embedding),
            allow_legacy_embedding_scoring=False,
            owner_scope="local",
        )
        latency_ms = (time.perf_counter() - started) * 1000
        observations = _observations_from_result(
            retrieval_result,
            source_id_by_document_id,
        )
        results.append(
            _evaluate_variant_case(
                case,
                observations=observations,
                top_k=top_k,
                candidate_count=retrieval_result.candidate_count,
                retrieval_mode=retrieval_result.retrieval_mode,
                latency_ms=latency_ms,
            )
        )
    return tuple(results)


def build_variant_report(
    *,
    benchmark: RetrievalBenchmark,
    benchmark_path: Path,
    corpus_manifest: list[dict[str, object]],
    top_k: int,
    cases: Sequence[dict[str, object]],
) -> dict[str, object]:
    if top_k not in TRADEOFF_TOP_KS:
        raise RetrievalBenchmarkError(f"unsupported tradeoff top_k: {top_k}")
    case_results = tuple(cases)
    return {
        "artifact_type": "retrieval_top_k_tradeoff_variant",
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_version": benchmark.version,
        "benchmark_sha256": _sha256_file(benchmark_path),
        "variant": {"requested_top_k": top_k},
        "controls": {
            "parser": "PyPDFParserClient",
            "page_aware": benchmark.contract.page_aware,
            "chunk_max_chars": benchmark.contract.chunk_max_chars,
            "chunk_overlap_chars": benchmark.contract.chunk_overlap_chars,
            "embedding_model": VECTOR_MODEL,
            "embedding_dimensions": VECTOR_DIMENSIONS,
            "similarity": benchmark.contract.similarity,
            "relevance_floor": benchmark.contract.relevance_floor,
            "owner_scope": "local",
            "retrieval_mode": "pgvector_exact_cosine",
            "candidate_pool_behavior": {
                "formula": "min(top_k * 2, 20)",
                "requested_candidate_pool": min(top_k * 2, VECTOR_CANDIDATE_POOL_MAX),
                "max": VECTOR_CANDIDATE_POOL_MAX,
            },
            "final_llm_called": False,
        },
        "corpus": corpus_manifest,
        "metrics": _aggregate_variant_metrics(case_results),
        "cases": [_case_to_bounded_dict(case) for case in case_results],
    }


def build_comparison_report(
    *,
    benchmark: RetrievalBenchmark,
    benchmark_path: Path,
    variant_reports: Mapping[int, Mapping[str, object]],
) -> dict[str, object]:
    missing = [top_k for top_k in TRADEOFF_TOP_KS if top_k not in variant_reports]
    if missing:
        raise RetrievalBenchmarkError(
            f"comparison missing top_k variants: {', '.join(map(str, missing))}"
        )
    baseline_reproducibility = _baseline_reproducibility(variant_reports[5])
    case_level_deltas = _build_case_level_deltas(variant_reports)
    return {
        "artifact_type": "retrieval_top_k_tradeoff_comparison",
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_version": benchmark.version,
        "benchmark_sha256": _sha256_file(benchmark_path),
        "controls": {
            "top_k_variants": list(TRADEOFF_TOP_KS),
            "candidate_pool_formula": "min(top_k * 2, 20)",
            "canonical_contract_unchanged": list(benchmark.contract.top_k),
            "final_llm_called": False,
        },
        "baseline_reproducibility": baseline_reproducibility,
        "positive_metrics_by_top_k": {
            str(top_k): variant_reports[top_k]["metrics"]["positive"]
            for top_k in TRADEOFF_TOP_KS
        },
        "category_metrics_by_top_k": {
            str(top_k): variant_reports[top_k]["metrics"]["by_category"]
            for top_k in TRADEOFF_TOP_KS
        },
        "negative_safety_by_top_k": {
            str(top_k): variant_reports[top_k]["metrics"]["negative"]
            for top_k in TRADEOFF_TOP_KS
        },
        "context_volume_by_top_k": {
            str(top_k): variant_reports[top_k]["metrics"]["context_volume"]
            for top_k in TRADEOFF_TOP_KS
        },
        "operational_cost_by_top_k": {
            str(top_k): variant_reports[top_k]["metrics"]["operational_cost"]
            for top_k in TRADEOFF_TOP_KS
        },
        "case_level_deltas": case_level_deltas,
        "candidate_decision": _choose_candidate(
            baseline_reproducibility=baseline_reproducibility,
            variant_reports=variant_reports,
            case_level_deltas=case_level_deltas,
        ),
    }


async def run_live_tradeoff(
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
        session_factory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
        )
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
                    content_hash=hashlib.sha256(
                        parsed.raw_text.encode("utf-8")
                    ).hexdigest(),
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
                request_workflow_id=f"retrieval-top-k-tradeoff-{source.source_id}",
                pages=parsed.pages,
                owner_scope="local",
            )
            source_id_by_document_id[source_document_id] = source.source_id

        query_embedding_result = await embedding_service.embed(
            [case.query for case in benchmark.cases],
            metadata={
                "operation": "retrieval_top_k_tradeoff",
                "benchmark_id": benchmark.benchmark_id,
            },
        )
        variant_reports: dict[int, dict[str, object]] = {}
        with session_factory() as session:
            retriever = ProductionChunkRetriever(
                chunk_repository=ChunkRepository(session),
            )
            for top_k in TRADEOFF_TOP_KS:
                case_results = run_variant_cases(
                    benchmark,
                    query_embeddings=query_embedding_result.embeddings,
                    retriever=retriever,
                    source_id_by_document_id=source_id_by_document_id,
                    top_k=top_k,
                )
                variant_reports[top_k] = build_variant_report(
                    benchmark=benchmark,
                    benchmark_path=benchmark_path,
                    corpus_manifest=verified.manifest,
                    top_k=top_k,
                    cases=case_results,
                )
                if top_k == 5:
                    baseline = _baseline_reproducibility(variant_reports[5])
                    if baseline["status"] != "PASS":
                        raise RetrievalBenchmarkError(
                            "k=5 canonical baseline reproducibility failed: "
                            f"{baseline}"
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


def _evaluate_variant_case(
    case: BenchmarkCase,
    *,
    observations: Iterable[RetrievalObservation],
    top_k: int,
    candidate_count: Optional[int],
    retrieval_mode: str,
    latency_ms: float,
) -> dict[str, object]:
    bounded_observations = tuple(sorted(observations, key=lambda item: item.rank)[:top_k])
    group_hits = {
        group.group_id: _group_hit(group, bounded_observations)
        for group in case.evidence_groups
    }
    group_first_rank = {
        group.group_id: _first_group_rank(group, bounded_observations)
        for group in case.evidence_groups
    }
    completion_rank = (
        max(group_first_rank.values())
        if group_first_rank and all(rank is not None for rank in group_first_rank.values())
        else None
    )
    passed = (
        not any(observation.accepted for observation in bounded_observations)
        if not case.answerable
        else all(group_hits.values())
    )
    return {
        "case_id": case.case_id,
        "category": case.category,
        "answerable": case.answerable,
        "passed": passed,
        "first_required_rank": (
            min(rank for rank in group_first_rank.values() if rank is not None)
            if group_first_rank and any(rank is not None for rank in group_first_rank.values())
            else None
        ),
        "completion_rank": completion_rank,
        "group_hits": group_hits,
        "group_first_rank": group_first_rank,
        "required_sources": sorted(
            {
                location.source_id
                for group in case.evidence_groups
                for location in group.locations
            }
        ),
        "required_pages": sorted(
            {
                f"{location.source_id}:p{page}"
                for group in case.evidence_groups
                for location in group.locations
                for page in location.pages
            }
        ),
        "candidate_count": candidate_count,
        "retrieval_mode": retrieval_mode,
        "retrieval_latency_ms": round(latency_ms, 3),
        "accepted_chunk_count": sum(
            observation.accepted for observation in bounded_observations
        ),
        "retrieved_character_count": sum(
            len(observation.chunk_text) for observation in bounded_observations
            if observation.accepted
        ),
        "observations": bounded_observations,
    }


def _aggregate_variant_metrics(
    cases: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    positives = tuple(case for case in cases if case["answerable"])
    negatives = tuple(case for case in cases if not case["answerable"])
    positive_case_group_recalls = [
        _ratio(sum(case["group_hits"].values()), len(case["group_hits"]))
        for case in positives
    ]
    positive_passes = [case for case in positives if case["passed"]]
    negative_passes = [case for case in negatives if case["passed"]]
    reciprocal_ranks = [
        1 / case["first_required_rank"]
        if case["first_required_rank"] is not None
        else 0.0
        for case in positives
    ]
    categories: dict[str, dict[str, object]] = {}
    for category in sorted({case["category"] for case in cases}):
        category_cases = tuple(case for case in cases if case["category"] == category)
        category_positive = tuple(case for case in category_cases if case["answerable"])
        category_case_group_recalls = [
            _ratio(sum(case["group_hits"].values()), len(case["group_hits"]))
            for case in category_positive
        ]
        categories[category] = {
            "case_count": len(category_cases),
            "positive_case_count": len(category_positive),
            "pass_count": sum(case["passed"] for case in category_positive),
            "group_recall": _mean(category_case_group_recalls),
            "full_case_success": _ratio(
                sum(case["passed"] for case in category_positive),
                len(category_positive),
            ),
            "recovered_case_ids": [
                case["case_id"] for case in category_positive if case["passed"]
            ],
            "completion_ranks": {
                case["case_id"]: case["completion_rank"]
                for case in category_positive
            },
        }
    return {
        "case_count": len(cases),
        "positive_case_count": len(positives),
        "negative_case_count": len(negatives),
        "positive": {
            "case_count": len(positives),
            "pass_count": len(positive_passes),
            "recall_at_requested_top_k": _ratio(
                sum(positive_case_group_recalls), len(positive_case_group_recalls)
            ),
            "full_case_success_at_requested_top_k": _ratio(
                len(positive_passes), len(positives)
            ),
            "mrr": _mean(reciprocal_ranks),
            "source_recall_at_requested_top_k": _source_recall(positives),
            "page_coverage_at_requested_top_k": _page_coverage(positives),
        },
        "negative": {
            "case_count": len(negatives),
            "negative_rejection_rate": _ratio(len(negative_passes), len(negatives)),
            "false_positive_retrieval_rate": (
                1 - _ratio(len(negative_passes), len(negatives))
                if negatives
                else None
            ),
            "average_accepted_evidence_count": _mean(
                [case["accepted_chunk_count"] for case in negatives]
            ),
            "maximum_accepted_evidence_count": max(
                (case["accepted_chunk_count"] for case in negatives),
                default=0,
            ),
        },
        "by_category": categories,
        "context_volume": {
            "positive_average_accepted_chunks": _mean(
                [case["accepted_chunk_count"] for case in positives]
            ),
            "negative_average_accepted_chunks": _mean(
                [case["accepted_chunk_count"] for case in negatives]
            ),
            "positive_average_retrieved_characters": _mean(
                [case["retrieved_character_count"] for case in positives]
            ),
            "negative_average_retrieved_characters": _mean(
                [case["retrieved_character_count"] for case in negatives]
            ),
        },
        "operational_cost": {
            "average_retrieval_latency_ms": _mean(
                [case["retrieval_latency_ms"] for case in cases]
            ),
            "positive_average_retrieval_latency_ms": _mean(
                [case["retrieval_latency_ms"] for case in positives]
            ),
            "negative_average_retrieval_latency_ms": _mean(
                [case["retrieval_latency_ms"] for case in negatives]
            ),
            "median_retrieval_latency_ms": statistics.median(
                [case["retrieval_latency_ms"] for case in cases]
            )
            if cases
            else 0.0,
        },
        "candidate_pool": {
            "observed_candidate_count_by_case": {
                case["case_id"]: case["candidate_count"] for case in cases
            },
            "observed_candidate_count_average": _mean(
                [case["candidate_count"] or 0 for case in cases]
            ),
            "observed_candidate_count_maximum": max(
                (case["candidate_count"] or 0 for case in cases),
                default=0,
            ),
        },
    }


def _case_to_bounded_dict(case: Mapping[str, object]) -> dict[str, object]:
    return {
        key: case[key]
        for key in (
            "case_id",
            "category",
            "answerable",
            "passed",
            "first_required_rank",
            "completion_rank",
            "group_hits",
            "group_first_rank",
            "required_sources",
            "required_pages",
            "candidate_count",
            "retrieval_mode",
            "retrieval_latency_ms",
            "accepted_chunk_count",
            "retrieved_character_count",
        )
    } | {
        "observations": [
            {
                "rank": observation.rank,
                "source_id": observation.source_id,
                "page": observation.page,
                "locator": observation.locator,
                "score": round(observation.score, 6),
                "accepted": observation.accepted,
                "retrieval_mode": observation.retrieval_mode,
                "candidate_count": observation.candidate_count,
            }
            for observation in case["observations"]
        ]
    }


def _build_case_level_deltas(
    variant_reports: Mapping[int, Mapping[str, object]],
) -> dict[str, object]:
    base_cases = {
        case["case_id"]: case for case in variant_reports[5]["cases"]
    }
    deltas: dict[str, object] = {}
    for top_k in (8, 10):
        current_cases = {
            case["case_id"]: case for case in variant_reports[top_k]["cases"]
        }
        positive_deltas = []
        for case_id, base in base_cases.items():
            if not base["answerable"]:
                continue
            current = current_cases[case_id]
            if current["passed"] and not base["passed"]:
                classification = "RECOVERED"
            elif current["passed"] and base["passed"]:
                classification = "UNCHANGED_PASS"
            elif not current["passed"] and not base["passed"]:
                classification = "UNCHANGED_FAIL"
            else:
                classification = "REGRESSED"
            positive_deltas.append(
                {
                    "case_id": case_id,
                    "classification": classification,
                    "passed": current["passed"],
                    "completion_rank": current["completion_rank"],
                    "group_hits": current["group_hits"],
                    "observations": current["observations"],
                }
            )
        deltas[str(top_k)] = positive_deltas
    return deltas


def _baseline_reproducibility(
    baseline: Mapping[str, object],
) -> dict[str, object]:
    baseline_cases = {
        case["case_id"]: case for case in baseline["cases"]
    }
    observed_failed_ids = tuple(
        case["case_id"]
        for case in baseline["cases"]
        if case["answerable"] and not case["passed"]
    )
    baseline_metrics = baseline["metrics"]["positive"]
    observed_metrics = {
        "recall_at_5": baseline_metrics["recall_at_requested_top_k"],
        "full_case_success_at_5": baseline_metrics[
            "full_case_success_at_requested_top_k"
        ],
        "mrr": baseline_metrics["mrr"],
        "source_recall_at_5": baseline_metrics[
            "source_recall_at_requested_top_k"
        ],
        "page_coverage_at_5": baseline_metrics[
            "page_coverage_at_requested_top_k"
        ],
    }
    return {
        "status": (
            "PASS"
            if observed_failed_ids == CANONICAL_FAILED_POSITIVE_CASE_IDS
            and all(
                _close_enough(observed_metrics.get(key), expected)
                for key, expected in CANONICAL_METRICS.items()
            )
            else "FAIL"
        ),
        "expected_failed_positive_case_ids": list(CANONICAL_FAILED_POSITIVE_CASE_IDS),
        "observed_failed_positive_case_ids": list(observed_failed_ids),
        "expected_metrics": dict(CANONICAL_METRICS),
        "observed_metrics": observed_metrics,
        "canonical_case_ids_present": all(
            case_id in baseline_cases for case_id in CANONICAL_FAILED_POSITIVE_CASE_IDS
        ),
    }


def _choose_candidate(
    *,
    baseline_reproducibility: Mapping[str, object],
    variant_reports: Mapping[int, Mapping[str, object]],
    case_level_deltas: Mapping[str, object],
) -> dict[str, object]:
    if baseline_reproducibility["status"] != "PASS":
        return {
            "decision": "INCONCLUSIVE",
            "reason": "k=5 did not reproduce the canonical baseline.",
        }

    def recovered_ids(top_k: int) -> set[str]:
        return {
            item["case_id"]
            for item in case_level_deltas[str(top_k)]
            if item["classification"] == "RECOVERED"
        }

    recovered_at_8 = recovered_ids(8)
    recovered_at_10 = recovered_ids(10)
    positive_8 = variant_reports[8]["metrics"]["positive"]
    positive_10 = variant_reports[10]["metrics"]["positive"]
    no_regressions = all(
        item["classification"] != "REGRESSED"
        for top_k in (8, 10)
        for item in case_level_deltas[str(top_k)]
    )
    same_group_and_full_case = all(
        _close_enough(
            positive_8[key],
            positive_10[key],
        )
        for key in (
            "recall_at_requested_top_k",
            "full_case_success_at_requested_top_k",
            "page_coverage_at_requested_top_k",
        )
    )
    if (
        recovered_at_8
        and recovered_at_10 == recovered_at_8
        and same_group_and_full_case
        and no_regressions
    ):
        return {
            "decision": "CANDIDATE_TOP_K_8",
            "reason": (
                "k=8 captures all observed positive full-case gains; k=10 adds "
                "no recovered positive case or group/full-case recall."
            ),
            "recovered_case_ids_at_8": sorted(recovered_at_8),
            "additional_recovered_case_ids_at_10": sorted(
                recovered_at_10 - recovered_at_8
            ),
            "production_change": False,
        }
    if recovered_at_10 - recovered_at_8:
        return {
            "decision": "CANDIDATE_TOP_K_10",
            "reason": "k=10 adds positive case recovery beyond k=8.",
            "recovered_case_ids_at_8": sorted(recovered_at_8),
            "additional_recovered_case_ids_at_10": sorted(
                recovered_at_10 - recovered_at_8
            ),
            "production_change": False,
        }
    if not recovered_at_8 and not recovered_at_10:
        return {
            "decision": "KEEP_TOP_K_5",
            "reason": "Deeper retrieval produced no recovered positive cases.",
            "production_change": False,
        }
    return {
        "decision": "INCONCLUSIVE",
        "reason": "Depth tradeoff did not satisfy a clear candidate rule.",
        "production_change": False,
    }


def _group_hit(
    group: EvidenceGroup,
    observations: Sequence[RetrievalObservation],
) -> bool:
    location_hits = [_location_hit(location, observations) for location in group.locations]
    return any(location_hits) if group.match == "any" else all(location_hits)


def _first_group_rank(
    group: EvidenceGroup,
    observations: Sequence[RetrievalObservation],
) -> Optional[int]:
    if group.match == "any":
        ranks = [
            observation.rank
            for location in group.locations
            for observation in observations
            if observation.accepted and _location_hit(location, (observation,))
        ]
        return min(ranks) if ranks else None
    location_ranks: list[int] = []
    for location in group.locations:
        ranks = [
            observation.rank
            for observation in observations
            if observation.accepted and _location_hit(location, (observation,))
        ]
        if not ranks:
            return None
        location_ranks.append(min(ranks))
    return max(location_ranks) if location_ranks else None


def _location_hit(location, observations: Sequence[RetrievalObservation]) -> bool:
    return any(
        observation.source_id == location.source_id
        and observation.page in location.pages
        and any(
            normalize_for_match(anchor) in normalize_for_match(observation.chunk_text)
            for anchor in location.anchors
        )
        for observation in observations
    )


def _source_recall(cases: Sequence[Mapping[str, object]]) -> float:
    ratios = []
    for case in cases:
        expected = set(case["required_sources"])
        observed = {
            observation.source_id
            for observation in case["observations"]
            if observation.accepted
        }
        ratios.append(len(expected.intersection(observed)) / len(expected) if expected else 0.0)
    return _mean(ratios)


def _page_coverage(cases: Sequence[Mapping[str, object]]) -> float:
    ratios = []
    for case in cases:
        expected = set(case["required_pages"])
        observed = {
            f"{observation.source_id}:p{observation.page}"
            for observation in case["observations"]
            if observation.accepted
        }
        ratios.append(len(expected.intersection(observed)) / len(expected) if expected else 0.0)
    return _mean(ratios)


def _mean(values: Iterable[float]) -> float:
    values = tuple(values)
    return sum(values) / len(values) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _close_enough(actual: object, expected: float) -> bool:
    return isinstance(actual, (int, float)) and abs(float(actual) - expected) <= 1e-6


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
