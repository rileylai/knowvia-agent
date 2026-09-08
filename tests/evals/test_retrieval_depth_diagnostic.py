from __future__ import annotations

from pathlib import Path

from eval.retrieval.depth_diagnostic import (
    FAILED_CASE_IDS,
    build_depth_report,
    evaluate_depth_case,
    run_depth_diagnostic_cases,
)
from eval.retrieval.metrics import RetrievalObservation
from eval.retrieval.schema import load_benchmark
from src.rag.retriever import (
    RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
    RetrievedChunk,
    RetrievalResult,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = REPO_ROOT / "eval/retrieval/benchmark.yaml"


def _benchmark():
    return load_benchmark(BENCHMARK_PATH)


def _observation(
    *,
    rank: int,
    source_id: str,
    page: int,
    text: str,
) -> RetrievalObservation:
    return RetrievalObservation(
        rank=rank,
        source_id=source_id,
        locator=f"page {page}",
        chunk_text=text,
        score=0.8 - rank / 100,
        accepted=True,
        retrieval_mode=RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
        source_document_id=rank,
        page=page,
        candidate_count=20,
    )


def test_depth_diagnostic_classifies_depth_and_multi_evidence() -> None:
    benchmark = _benchmark()
    case = next(case for case in benchmark.cases if case.case_id == "rv-009")
    evaluation = evaluate_depth_case(
        case,
        observations_top_5=(
            _observation(
                rank=1,
                source_id="chatgpt_tasks_week3",
                page=6,
                text="watcher 只 負 責 掃 db 找 到 期 job 、 worker 只 負 責 執 行 解 耦 : watcher 和 worker 互 不 知道對方",
            ),
        ),
        observations_top_10=(
            _observation(
                rank=1,
                source_id="chatgpt_tasks_week3",
                page=6,
                text="watcher 只 負 責 掃 db 找 到 期 job 、 worker 只 負 責 執 行 解 耦 : watcher 和 worker 互 不 知道對方",
            ),
            _observation(
                rank=6,
                source_id="chatgpt_tasks_week3",
                page=16,
                text="idempotent handler",
            ),
        ),
    )

    assert evaluation.group_first_rank == {
        "queue-separation": 1,
        "repeat-execution-handling": 6,
    }
    assert evaluation.completion_rank == 6
    assert evaluation.depth_status == "ONLY_RANK_6_10"
    assert evaluation.pattern_classification == "PARTIAL_MULTI_EVIDENCE_DEPTH"


def test_depth_diagnostic_classifies_same_page_wrong_chunk() -> None:
    benchmark = _benchmark()
    case = next(case for case in benchmark.cases if case.case_id == "rv-005")
    evaluation = evaluate_depth_case(
        case,
        observations_top_5=(
            _observation(
                rank=1,
                source_id="google_agent_patterns",
                page=4,
                text="a different p4 chunk",
            ),
        ),
        observations_top_10=(
            _observation(
                rank=1,
                source_id="google_agent_patterns",
                page=4,
                text="a different p4 chunk",
            ),
            _observation(
                rank=6,
                source_id="google_agent_patterns",
                page=4,
                text="output from one agent serves as the direct input for the next agent",
            ),
        ),
    )

    assert evaluation.completion_rank == 6
    assert evaluation.pattern_classification == "SAME_PAGE_WRONG_CHUNK_DEPTH"


def test_depth_diagnostic_absence_stays_unresolved_at_ten() -> None:
    benchmark = _benchmark()
    case = next(case for case in benchmark.cases if case.case_id == "rv-004")
    evaluation = evaluate_depth_case(
        case,
        observations_top_5=(
            _observation(
                rank=1,
                source_id="production_agents",
                page=8,
                text="unrelated accepted chunk",
            ),
        ),
        observations_top_10=(
            _observation(
                rank=1,
                source_id="production_agents",
                page=8,
                text="unrelated accepted chunk",
            ),
        ),
    )

    assert evaluation.completion_rank is None
    assert evaluation.depth_status == "STILL_ABSENT_AT_10"
    assert evaluation.pattern_classification == "STILL_ABSENT_AT_10"


def test_depth_diagnostic_runner_requests_five_then_ten() -> None:
    benchmark = _benchmark()
    calls: list[int] = []

    class FakeRetriever:
        def retrieve_with_metadata(self, **kwargs):
            calls.append(kwargs["top_k"])
            return RetrievalResult(
                chunks=[
                    RetrievedChunk(
                        chunk_id=1,
                        source_document_id=101,
                        chunk_index=0,
                        chunk_text="unrelated",
                        notion_path="",
                        notion_page_id=None,
                        source_kind="pdf",
                        score=0.8,
                        source_display_name="fixture.pdf",
                        locator="page 1",
                    )
                ],
                retrieval_mode=RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
                retrieval_fallback_reason=None,
                candidate_count=min(kwargs["top_k"] * 2, 20),
                accepted_evidence_count=1,
            )

    evaluations = run_depth_diagnostic_cases(
        benchmark,
        query_embeddings=[[0.1]] * len(FAILED_CASE_IDS),
        retriever=FakeRetriever(),
        source_id_by_document_id={101: "production_agents"},
    )

    assert tuple(item.case_id for item in evaluations) == FAILED_CASE_IDS
    assert calls == [value for _ in FAILED_CASE_IDS for value in (5, 10)]


def test_depth_report_is_bounded_and_declares_diagnostic_semantics() -> None:
    benchmark = _benchmark()
    evaluations = tuple(
        evaluate_depth_case(case, observations_top_5=(), observations_top_10=())
        for case in (next(case for case in benchmark.cases if case.case_id == case_id) for case_id in FAILED_CASE_IDS)
    )
    report = build_depth_report(
        benchmark=benchmark,
        benchmark_path=BENCHMARK_PATH,
        corpus_manifest=[{"source_id": "redacted", "sha256": "redacted"}],
        evaluations=evaluations,
    )
    report_text = str(report)

    assert report["failed_case_count"] == 7
    assert report["diagnostic_metrics"]["semantics"] == "targeted_failed_positive_cases_only"
    assert "chunk_text" not in report_text
    assert "unrelated" not in report_text
