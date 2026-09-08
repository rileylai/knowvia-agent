from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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

FAILED_CASE_IDS = (
    "rv-004",
    "rv-005",
    "rv-009",
    "rv-023",
    "rv-026",
    "rv-027",
    "rv-029",
)
PRODUCTION_TOP_K = 5
DIAGNOSTIC_TOP_K = 10
DIAGNOSTIC_CANDIDATE_POOL_MAX = 20


@dataclass(frozen=True)
class DepthCaseEvaluation:
    case_id: str
    category: str
    expected_gold: tuple[dict[str, object], ...]
    observations_top_5: tuple[RetrievalObservation, ...]
    observations_top_10: tuple[RetrievalObservation, ...]
    group_first_rank: Mapping[str, Optional[int]]
    group_hits_at_5: Mapping[str, bool]
    group_hits_at_10: Mapping[str, bool]
    completion_rank: Optional[int]
    depth_status: str
    pattern_classification: str


def select_failed_cases(benchmark: RetrievalBenchmark) -> tuple[BenchmarkCase, ...]:
    cases_by_id = {case.case_id: case for case in benchmark.cases}
    missing = [case_id for case_id in FAILED_CASE_IDS if case_id not in cases_by_id]
    if missing:
        raise RetrievalBenchmarkError(
            f"depth diagnostic cases missing from benchmark: {', '.join(missing)}"
        )
    selected = tuple(cases_by_id[case_id] for case_id in FAILED_CASE_IDS)
    if any(not case.answerable for case in selected):
        raise RetrievalBenchmarkError("depth diagnostic cases must all be answerable")
    return selected


def run_depth_diagnostic_cases(
    benchmark: RetrievalBenchmark,
    *,
    query_embeddings: Sequence[Sequence[float]],
    retriever,
    source_id_by_document_id: Mapping[int, str],
) -> tuple[DepthCaseEvaluation, ...]:
    cases = select_failed_cases(benchmark)
    if len(query_embeddings) != len(cases):
        raise RetrievalBenchmarkError(
            "depth diagnostic query embedding count does not match selected cases"
        )

    evaluations: list[DepthCaseEvaluation] = []
    for case, query_embedding in zip(cases, query_embeddings):
        result_top_5 = retriever.retrieve_with_metadata(
            query_text=case.query,
            top_k=PRODUCTION_TOP_K,
            query_embedding=list(query_embedding),
            allow_legacy_embedding_scoring=False,
            owner_scope="local",
        )
        result_top_10 = retriever.retrieve_with_metadata(
            query_text=case.query,
            top_k=DIAGNOSTIC_TOP_K,
            query_embedding=list(query_embedding),
            allow_legacy_embedding_scoring=False,
            owner_scope="local",
        )
        observations_top_5 = _observations_from_result(
            result_top_5,
            source_id_by_document_id,
        )
        observations_top_10 = _observations_from_result(
            result_top_10,
            source_id_by_document_id,
        )
        evaluations.append(
            evaluate_depth_case(
                case,
                observations_top_5=observations_top_5,
                observations_top_10=observations_top_10,
            )
        )
    return tuple(evaluations)


def evaluate_depth_case(
    case: BenchmarkCase,
    *,
    observations_top_5: Iterable[RetrievalObservation],
    observations_top_10: Iterable[RetrievalObservation],
) -> DepthCaseEvaluation:
    top_5 = tuple(sorted(observations_top_5, key=lambda item: item.rank)[:5])
    top_10 = tuple(sorted(observations_top_10, key=lambda item: item.rank)[:10])
    group_first_rank = {
        group.group_id: _first_group_rank(group, top_10)
        for group in case.evidence_groups
    }
    group_hits_at_5 = {
        group.group_id: _group_hit(group, top_5) for group in case.evidence_groups
    }
    group_hits_at_10 = {
        group.group_id: _group_hit(group, top_10) for group in case.evidence_groups
    }
    completion_rank = (
        max(group_first_rank.values())
        if group_first_rank and all(rank is not None for rank in group_first_rank.values())
        else None
    )
    return DepthCaseEvaluation(
        case_id=case.case_id,
        category=case.category,
        expected_gold=_expected_gold(case),
        observations_top_5=top_5,
        observations_top_10=top_10,
        group_first_rank=group_first_rank,
        group_hits_at_5=group_hits_at_5,
        group_hits_at_10=group_hits_at_10,
        completion_rank=completion_rank,
        depth_status=_depth_status(completion_rank),
        pattern_classification=_classify_pattern(
            case,
            top_5=top_5,
            top_10=top_10,
            completion_rank=completion_rank,
        ),
    )


def build_depth_report(
    *,
    benchmark: RetrievalBenchmark,
    benchmark_path: Path,
    corpus_manifest: list[dict[str, object]],
    evaluations: Sequence[DepthCaseEvaluation],
) -> dict[str, object]:
    if tuple(item.case_id for item in evaluations) != FAILED_CASE_IDS:
        raise RetrievalBenchmarkError(
            "depth diagnostic report must contain the fixed seven failed cases"
        )
    recovered = [item for item in evaluations if item.completion_rank is not None]
    semantic = [item for item in evaluations if item.category == "semantic_paraphrase"]
    multi = [item for item in evaluations if item.category == "multi_evidence"]
    all_groups = sum(len(item.group_hits_at_10) for item in evaluations)
    recovered_groups = sum(
        sum(1 for hit in item.group_hits_at_10.values() if hit)
        for item in evaluations
    )
    return {
        "artifact_type": "positive_retrieval_depth_diagnostic",
        "benchmark_id": benchmark.benchmark_id,
        "benchmark_version": benchmark.version,
        "benchmark_sha256": _sha256_file(benchmark_path),
        "canonical_baseline": {
            "recall_at_1": 0.425,
            "recall_at_3": 0.575,
            "recall_at_5": 0.700,
            "mrr": 0.625,
            "full_case_success_at_5": 0.650,
            "source_recall_at_5": 0.950,
            "page_coverage_at_5": 0.750,
        },
        "controls": {
            "parser": "PyPDFParserClient",
            "page_aware": benchmark.contract.page_aware,
            "chunk_max_chars": benchmark.contract.chunk_max_chars,
            "chunk_overlap_chars": benchmark.contract.chunk_overlap_chars,
            "embedding_model": VECTOR_MODEL,
            "embedding_dimensions": VECTOR_DIMENSIONS,
            "similarity": benchmark.contract.similarity,
            "candidate_pool": "min(top_k * 2, 20)",
            "candidate_pool_max": DIAGNOSTIC_CANDIDATE_POOL_MAX,
            "relevance_floor": benchmark.contract.relevance_floor,
            "owner_scope": "local",
            "retrieval_mode": "pgvector_exact_cosine",
            "final_llm_called": False,
        },
        "top_k_diagnostic": {
            "production_equivalent": PRODUCTION_TOP_K,
            "diagnostic": DIAGNOSTIC_TOP_K,
            "canonical_contract_unchanged": list(benchmark.contract.top_k),
        },
        "corpus": corpus_manifest,
        "failed_case_count": len(evaluations),
        "recovered_by_top10_count": len(recovered),
        "still_failed_at_top10_count": len(evaluations) - len(recovered),
        "semantic_recovered": {
            "recovered": sum(item.completion_rank is not None for item in semantic),
            "total": len(semantic),
        },
        "multi_evidence_recovered": {
            "recovered": sum(item.completion_rank is not None for item in multi),
            "total": len(multi),
        },
        "diagnostic_metrics": {
            "semantics": "targeted_failed_positive_cases_only",
            "recall_at_5": _ratio(
                sum(sum(item.group_hits_at_5.values()) for item in evaluations),
                all_groups,
            ),
            "recall_at_10": _ratio(recovered_groups, all_groups),
            "full_case_success_at_5": _ratio(
                sum(all(item.group_hits_at_5.values()) for item in evaluations),
                len(evaluations),
            ),
            "full_case_success_at_10": _ratio(
                sum(all(item.group_hits_at_10.values()) for item in evaluations),
                len(evaluations),
            ),
        },
        "cases": [_case_to_bounded_dict(item) for item in evaluations],
        "pattern_summary": _pattern_summary(evaluations),
    }


async def run_live_depth_diagnostic(
    benchmark: RetrievalBenchmark,
    *,
    benchmark_path: Path,
    corpus_root: Path,
    openai_api_key: str,
    admin_database_url: str = DEFAULT_ADMIN_DATABASE_URL,
    keep_database: bool = False,
) -> dict[str, object]:
    """Run the seven-case depth diagnostic in an isolated migrated database."""
    verified = verify_corpus(benchmark, corpus_root=corpus_root)
    _verify_production_contract(benchmark)
    cases = select_failed_cases(benchmark)
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
                request_workflow_id=f"retrieval-depth-diagnostic-{source.source_id}",
                pages=parsed.pages,
                owner_scope="local",
            )
            source_id_by_document_id[source_document_id] = source.source_id

        query_embedding_result = await embedding_service.embed(
            [case.query for case in cases],
            metadata={
                "operation": "retrieval_depth_diagnostic",
                "benchmark_id": benchmark.benchmark_id,
            },
        )
        with session_factory() as session:
            retriever = ProductionChunkRetriever(
                chunk_repository=ChunkRepository(session),
            )
            evaluations = run_depth_diagnostic_cases(
                benchmark,
                query_embeddings=query_embedding_result.embeddings,
                retriever=retriever,
                source_id_by_document_id=source_id_by_document_id,
            )
        return build_depth_report(
            benchmark=benchmark,
            benchmark_path=benchmark_path,
            corpus_manifest=verified.manifest,
            evaluations=evaluations,
        )
    finally:
        if engine is not None:
            engine.dispose()
        temporary_database.close(keep_database=keep_database)


def write_depth_report(report: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _expected_gold(case: BenchmarkCase) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "group_id": group.group_id,
            "match": group.match,
            "locations": [
                {"source_id": location.source_id, "pages": list(location.pages)}
                for location in group.locations
            ],
        }
        for group in case.evidence_groups
    )


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


def _depth_status(completion_rank: Optional[int]) -> str:
    if completion_rank is None:
        return "STILL_ABSENT_AT_10"
    if completion_rank <= 1:
        return "ALREADY_AT_1"
    if completion_rank <= 3:
        return "WITHIN_3"
    if completion_rank <= 5:
        return "WITHIN_5"
    return "ONLY_RANK_6_10"


def _classify_pattern(
    case: BenchmarkCase,
    *,
    top_5: Sequence[RetrievalObservation],
    top_10: Sequence[RetrievalObservation],
    completion_rank: Optional[int],
) -> str:
    if completion_rank is None:
        return "STILL_ABSENT_AT_10"
    if case.case_id == "rv-005":
        expected_page_in_top_5 = any(
            observation.source_id == "google_agent_patterns"
            and observation.page == 4
            for observation in top_5
        )
        gold_in_top_5 = _group_hit(case.evidence_groups[0], top_5)
        gold_in_top_10 = _group_hit(case.evidence_groups[0], top_10)
        if expected_page_in_top_5 and not gold_in_top_5 and gold_in_top_10:
            return "SAME_PAGE_WRONG_CHUNK_DEPTH"
    if len(case.evidence_groups) > 1 and completion_rank > PRODUCTION_TOP_K:
        return "PARTIAL_MULTI_EVIDENCE_DEPTH"
    if completion_rank > PRODUCTION_TOP_K:
        return "DEPTH_LIMITED"
    return "UNRESOLVED"


def _case_to_bounded_dict(item: DepthCaseEvaluation) -> dict[str, object]:
    return {
        "case_id": item.case_id,
        "category": item.category,
        "expected_gold": item.expected_gold,
        "gold_first_rank_by_group": dict(item.group_first_rank),
        "completion_rank": item.completion_rank,
        "depth_status": item.depth_status,
        "pattern_classification": item.pattern_classification,
        "group_hits_at_5": dict(item.group_hits_at_5),
        "group_hits_at_10": dict(item.group_hits_at_10),
        "observations_top_5": [_observation_to_dict(item) for item in item.observations_top_5],
        "observations_top_10": [_observation_to_dict(item) for item in item.observations_top_10],
    }


def _observation_to_dict(observation: RetrievalObservation) -> dict[str, object]:
    return {
        "rank": observation.rank,
        "source_id": observation.source_id,
        "page": observation.page,
        "locator": observation.locator,
        "score": round(observation.score, 6),
        "accepted": observation.accepted,
        "retrieval_mode": observation.retrieval_mode,
        "candidate_count": observation.candidate_count,
    }


def _pattern_summary(
    evaluations: Sequence[DepthCaseEvaluation],
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in evaluations:
        grouped.setdefault(item.pattern_classification, []).append(item.case_id)
    return {key: grouped[key] for key in sorted(grouped)}


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
