from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from eval.retrieval.schema import load_benchmark
from eval.retrieval.topk_tradeoff import (
    _aggregate_variant_metrics,
    build_variant_report,
    run_variant_cases,
)
from src.rag.retriever import (
    RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
    RetrievedChunk,
    RetrievalResult,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = REPO_ROOT / "eval/retrieval/benchmark.yaml"


def _benchmark():
    benchmark = load_benchmark(BENCHMARK_PATH)
    return replace(benchmark, cases=benchmark.cases[:2])


def _chunk(*, chunk_id: int, page: int, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        chunk_index=chunk_id,
        chunk_text=text,
        notion_path="",
        notion_page_id=None,
        source_kind="pdf",
        score=0.8 - chunk_id / 100,
        source_document_id=101,
        source_display_name="fixture.pdf",
        locator=f"page {page}",
    )


def test_variant_runner_requests_exact_tradeoff_top_ks() -> None:
    benchmark = _benchmark()
    calls: list[int] = []

    class FakeRetriever:
        def retrieve_with_metadata(self, **kwargs):
            top_k = kwargs["top_k"]
            calls.append(top_k)
            return RetrievalResult(
                chunks=[
                    _chunk(chunk_id=index, page=2, text="conventional, deterministic code")
                    for index in range(min(top_k, 2))
                ],
                retrieval_mode=RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
                retrieval_fallback_reason=None,
                candidate_count=min(top_k * 2, 20),
                accepted_evidence_count=min(top_k, 2),
            )

    for top_k in (5, 8, 10):
        run_variant_cases(
            benchmark,
            query_embeddings=[[0.1], [0.2]],
            retriever=FakeRetriever(),
            source_id_by_document_id={101: "production_agents"},
            top_k=top_k,
        )

    assert calls == [5, 5, 8, 8, 10, 10]


def test_variant_report_is_bounded_and_records_candidate_pool() -> None:
    benchmark = _benchmark()

    class FakeRetriever:
        def retrieve_with_metadata(self, **kwargs):
            return RetrievalResult(
                chunks=[
                    _chunk(
                        chunk_id=1,
                        page=2,
                        text="conventional, deterministic code with private fixture text",
                    )
                ],
                retrieval_mode=RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
                retrieval_fallback_reason=None,
                candidate_count=16,
                accepted_evidence_count=1,
            )

    cases = run_variant_cases(
        benchmark,
        query_embeddings=[[0.1], [0.2]],
        retriever=FakeRetriever(),
        source_id_by_document_id={101: "production_agents"},
        top_k=8,
    )
    report = build_variant_report(
        benchmark=benchmark,
        benchmark_path=BENCHMARK_PATH,
        corpus_manifest=[{"source_id": "redacted", "sha256": "redacted"}],
        top_k=8,
        cases=cases,
    )
    report_text = str(report)

    assert report["controls"]["candidate_pool_behavior"]["requested_candidate_pool"] == 16
    assert report["metrics"]["candidate_pool"]["observed_candidate_count_maximum"] == 16
    assert report["cases"][0]["retrieved_character_count"] > 0
    assert "chunk_text" not in report_text
    assert "private fixture text" not in report_text


def test_variant_group_recall_uses_case_macro_average() -> None:
    empty_case = {
        "case_id": "empty",
        "category": "multi_evidence",
        "answerable": True,
        "passed": False,
        "first_required_rank": 1,
        "completion_rank": None,
        "group_hits": {"a": True, "b": False},
        "required_sources": ["source"],
        "required_pages": ["source:p1"],
        "observations": [],
        "accepted_chunk_count": 0,
        "retrieved_character_count": 0,
        "retrieval_latency_ms": 1.0,
        "candidate_count": 2,
    }
    full_case = {
        **empty_case,
        "case_id": "full",
        "passed": True,
        "group_hits": {"a": True},
    }

    metrics = _aggregate_variant_metrics((empty_case, full_case))

    assert metrics["positive"]["recall_at_requested_top_k"] == 0.75
    assert metrics["by_category"]["multi_evidence"]["group_recall"] == 0.75
