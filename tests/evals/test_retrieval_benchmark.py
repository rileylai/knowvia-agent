from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from eval.retrieval.metrics import (
    RetrievalObservation,
    aggregate_metrics,
    build_bounded_report,
    evaluate_case,
)
from eval.retrieval.runner import (
    RetrievalAnnotationError,
    run_retrieval_cases,
    run_live_benchmark,
    validate_gold_annotations,
    verify_corpus,
)
import eval.retrieval.runner as retrieval_runner
from eval.retrieval.schema import BenchmarkSchemaError, load_benchmark
from src.rag.retriever import (
    RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
    RetrievedChunk,
    RetrievalResult,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_PATH = REPO_ROOT / "eval/retrieval/benchmark.yaml"


def _load_benchmark():
    return load_benchmark(BENCHMARK_PATH)


def _observation(
    *,
    rank: int,
    source_id: str,
    page: int,
    text: str,
    accepted: bool = True,
) -> RetrievalObservation:
    return RetrievalObservation(
        rank=rank,
        source_id=source_id,
        locator=f"page {page}",
        chunk_text=text,
        score=0.8,
        accepted=accepted,
        retrieval_mode=RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
        source_document_id=rank,
        page=page,
        candidate_count=5,
    )


def test_loader_reads_frozen_three_pdf_thirty_case_8_2_pilot() -> None:
    benchmark = _load_benchmark()

    assert benchmark.benchmark_id == "knowvia-retrieval-pilot"
    assert benchmark.version == "8.2"
    assert len(benchmark.corpus) == 3
    assert len(benchmark.cases) == 30
    assert sum(case.category == "hard_negative" for case in benchmark.cases) == 10
    assert sum(case.answerable for case in benchmark.cases) == 20


def test_8_2_adds_only_four_semantic_and_four_multi_positive_cases() -> None:
    benchmark = _load_benchmark()
    new_cases = {
        case.case_id: case
        for case in benchmark.cases
        if case.case_id >= "rv-023"
    }

    assert set(new_cases) == {
        "rv-023",
        "rv-024",
        "rv-025",
        "rv-026",
        "rv-027",
        "rv-028",
        "rv-029",
        "rv-030",
    }
    assert all(case.answerable for case in new_cases.values())
    assert {
        case.category for case in new_cases.values()
    } == {"semantic_paraphrase", "multi_evidence"}
    assert sum(
        case.category == "semantic_paraphrase" for case in new_cases.values()
    ) == 4
    assert sum(case.category == "multi_evidence" for case in new_cases.values()) == 4


def test_8_2_semantic_cases_are_bounded_and_cover_multiple_sources() -> None:
    benchmark = _load_benchmark()
    semantic_cases = tuple(
        case
        for case in benchmark.cases
        if case.case_id in {"rv-023", "rv-024", "rv-025", "rv-026"}
    )

    assert len(semantic_cases) == 4
    assert all(len(case.evidence_groups) == 1 for case in semantic_cases)
    assert len(
        {
            location.source_id
            for case in semantic_cases
            for group in case.evidence_groups
            for location in group.locations
        }
    ) >= 2
    for case in semantic_cases:
        query = case.query.casefold()
        for group in case.evidence_groups:
            for location in group.locations:
                for anchor in location.anchors:
                    assert anchor.casefold() not in query


def test_8_2_multi_cases_require_independent_groups_and_include_distant_pages() -> None:
    benchmark = _load_benchmark()
    multi_cases = tuple(
        case
        for case in benchmark.cases
        if case.case_id in {"rv-027", "rv-028", "rv-029", "rv-030"}
    )

    assert len(multi_cases) == 4
    assert all(len(case.evidence_groups) >= 2 for case in multi_cases)
    assert all(
        len({group.group_id for group in case.evidence_groups})
        == len(case.evidence_groups)
        for case in multi_cases
    )
    distant_page_cases = 0
    for case in multi_cases:
        pages = [
            page
            for group in case.evidence_groups
            for location in group.locations
            for page in location.pages
        ]
        if max(pages) - min(pages) >= 4:
            distant_page_cases += 1
    assert distant_page_cases >= 1


def test_loader_covers_ten_hard_negatives_without_gold_evidence() -> None:
    benchmark = _load_benchmark()
    negatives = tuple(case for case in benchmark.cases if not case.answerable)

    assert {case.case_id for case in negatives} == {
        "rv-013",
        "rv-014",
        "rv-015",
        "rv-016",
        "rv-017",
        "rv-018",
        "rv-019",
        "rv-020",
        "rv-021",
        "rv-022",
    }
    assert all(case.category == "hard_negative" for case in negatives)
    assert all(case.query.strip() and not case.evidence_groups for case in negatives)


def test_rv020_targets_unnamed_python_mcp_implementation() -> None:
    benchmark = _load_benchmark()
    case = next(case for case in benchmark.cases if case.case_id == "rv-020")

    assert case.query == (
        "Which Python MCP framework or SDK does the Week 3 prototype use "
        "to implement its MCP server?"
    )
    assert case.answerable is False
    assert case.notes == (
        "Unsupported implementation detail. Week 3 identifies a Python MCP "
        "server but does not name the Python MCP framework or SDK used to "
        "implement it."
    )


def test_loader_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    payload = yaml.safe_load(BENCHMARK_PATH.read_text(encoding="utf-8"))
    payload["cases"][1]["id"] = payload["cases"][0]["id"]
    path = tmp_path / "duplicate.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(BenchmarkSchemaError, match="duplicate case id"):
        load_benchmark(path)


def test_loader_rejects_invalid_source_page_anchor(tmp_path: Path) -> None:
    payload = yaml.safe_load(BENCHMARK_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["evidence_groups"][0]["locations"][0]["pages"] = [99]
    payload["cases"][0]["evidence_groups"][0]["locations"][0]["anchors"] = [""]
    path = tmp_path / "invalid-page-anchor.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(BenchmarkSchemaError, match="out of range"):
        load_benchmark(path)


def test_verify_corpus_is_deterministic_and_matches_manifest() -> None:
    benchmark = _load_benchmark()

    verified = verify_corpus(benchmark, corpus_root=REPO_ROOT / "mock_data")

    assert verified.manifest == [
        {
            "source_id": "production_agents",
            "filename": "Best Practices for Building AI Agents That Work in Production.pdf",
            "sha256": "c88c995d6d6deb5d350f12621cabc9dbfa2323047491ad66771989f58100f074",
            "page_count": 14,
        },
        {
            "source_id": "chatgpt_tasks_week3",
            "filename": "ChatGPT Tasks (MCP) — Week 3 Prototype - Slidev.pdf",
            "sha256": "eec40e93d8adb8755f89378dc9778a9219786b4f4b466e696a6aa8e1f54b2591",
            "page_count": 17,
        },
        {
            "source_id": "google_agent_patterns",
            "filename": "Choose a design pattern for your agentic AI system  _  Cloud Architecture Center  _  Google Cloud Documentation.pdf",
            "sha256": "c22fe67748782b2dfe8bff364a9ea3fa852a0eba9da22f100cbe43cd73cbba36",
            "page_count": 21,
        },
    ]


def test_gold_anchor_must_exist_in_actual_parsed_annotated_page() -> None:
    benchmark = _load_benchmark()
    verified = verify_corpus(benchmark, corpus_root=REPO_ROOT / "mock_data")

    validate_gold_annotations(benchmark, verified.parsed_documents)

    invalid_case = replace(
        benchmark.cases[0],
        evidence_groups=(
            replace(
                benchmark.cases[0].evidence_groups[0],
                locations=(
                    replace(
                        benchmark.cases[0].evidence_groups[0].locations[0],
                        anchors=("anchor absent from parsed page",),
                    ),
                ),
            ),
        ),
    )
    invalid_benchmark = replace(benchmark, cases=(invalid_case,))

    with pytest.raises(RetrievalAnnotationError, match="rv-001"):
        validate_gold_annotations(invalid_benchmark, verified.parsed_documents)


def test_invalid_annotation_fails_before_embedding_provider(monkeypatch) -> None:
    benchmark = _load_benchmark()
    invalid_case = replace(
        benchmark.cases[0],
        evidence_groups=(
            replace(
                benchmark.cases[0].evidence_groups[0],
                locations=(
                    replace(
                        benchmark.cases[0].evidence_groups[0].locations[0],
                        anchors=("anchor absent from parsed page",),
                    ),
                ),
            ),
        ),
    )
    invalid_benchmark = replace(benchmark, cases=(invalid_case,))

    def fail_if_provider_is_constructed(**kwargs):
        raise AssertionError("embedding provider must not be constructed")

    monkeypatch.setattr(
        retrieval_runner,
        "OpenAIEmbeddingClient",
        fail_if_provider_is_constructed,
    )

    with pytest.raises(RetrievalAnnotationError, match="rv-001"):
        asyncio.run(
            run_live_benchmark(
                invalid_benchmark,
                corpus_root=REPO_ROOT / "mock_data",
                openai_api_key="test-key",
            )
        )


def test_metrics_cover_single_multi_and_negative_cases() -> None:
    benchmark = _load_benchmark()
    single_case = benchmark.cases[0]
    multi_case = benchmark.cases[8]
    negative_case = benchmark.cases[12]

    single = evaluate_case(
        single_case,
        [_observation(rank=2, source_id="production_agents", page=2, text="conventional, deterministic code")],
    )
    multi = evaluate_case(
        multi_case,
        [
            _observation(rank=1, source_id="chatgpt_tasks_week3", page=6, text="Queue(SQS)"),
            _observation(
                rank=2,
                source_id="chatgpt_tasks_week3",
                page=6,
                text=(
                    "watcher 只 負 責 掃 db 找 到 期 job 、 worker 只 負 責 執 行"
                    " 解 耦 : watcher 和 worker 互 不 知道對方"
                ),
            ),
            _observation(rank=4, source_id="chatgpt_tasks_week3", page=16, text="idempotent handler"),
        ],
    )
    negative = evaluate_case(negative_case, [])

    metrics = aggregate_metrics([single, multi, negative])
    assert single.passed is True
    assert single.first_required_rank == 2
    assert multi.passed is True
    assert multi.completion_rank == 4
    assert negative.passed is True
    assert metrics["recall_at_5"] == 1.0
    assert metrics["mrr"] == 0.5
    assert metrics["negative_rejection_rate"] == 1.0
    assert metrics["page_coverage_at_5"] == 1.0


def test_mrr_counts_complete_positive_miss_as_zero() -> None:
    benchmark = _load_benchmark()
    hit = evaluate_case(
        benchmark.cases[0],
        [_observation(rank=1, source_id="production_agents", page=2, text="conventional, deterministic code")],
    )
    miss = evaluate_case(benchmark.cases[1], [])

    assert aggregate_metrics([hit, miss])["mrr"] == 0.5


def test_recall_is_macro_average_of_case_group_recall() -> None:
    benchmark = _load_benchmark()
    one_group_case = evaluate_case(
        benchmark.cases[0],
        [_observation(rank=1, source_id="production_agents", page=2, text="conventional, deterministic code")],
    )
    two_group_case = evaluate_case(
        benchmark.cases[8],
        [
            _observation(rank=1, source_id="chatgpt_tasks_week3", page=6, text="watcher 只 負 責 掃 db 找 到 期 job 、 worker 只 負 責 執 行 解 耦 : watcher 和 worker 互 不 知道對方"),
        ],
    )

    metrics = aggregate_metrics([one_group_case, two_group_case])

    assert metrics["recall_at_5"] == 0.75


def test_completion_rank_is_exact_group_completion_rank() -> None:
    benchmark = _load_benchmark()
    evaluation = evaluate_case(
        benchmark.cases[8],
        [
            _observation(rank=1, source_id="chatgpt_tasks_week3", page=6, text="watcher 只 負 責 掃 db 找 到 期 job 、 worker 只 負 責 執 行 解 耦 : watcher 和 worker 互 不 知道對方"),
            _observation(rank=2, source_id="chatgpt_tasks_week3", page=16, text="idempotent handler"),
        ],
    )

    assert evaluation.completion_rank == 2


def test_accepted_only_unknown_root_cause_is_unresolved() -> None:
    benchmark = _load_benchmark()
    no_result = evaluate_case(benchmark.cases[0], [])
    wrong_page = evaluate_case(
        benchmark.cases[0],
        [_observation(rank=1, source_id="production_agents", page=2, text="unrelated accepted chunk")],
    )

    assert no_result.failure_label == "UNRESOLVED"
    assert wrong_page.failure_label == "UNRESOLVED"


def test_bounded_report_excludes_full_chunk_text() -> None:
    benchmark = _load_benchmark()
    evaluation = evaluate_case(
        benchmark.cases[0],
        [_observation(rank=1, source_id="production_agents", page=2, text="conventional, deterministic code")],
    )

    report = build_bounded_report(
        benchmark_id=benchmark.benchmark_id,
        benchmark_version=benchmark.version,
        contract={"top_k": [1, 3, 5]},
        corpus_manifest=[{"source_id": "production_agents", "sha256": "redacted"}],
        evaluations=[evaluation],
    )
    report_text = str(report)
    assert "conventional, deterministic code" not in report_text
    assert "chunk_text" not in report_text
    assert report["cases"][0]["observations"][0]["locator"] == "page 2"


def test_runner_uses_retrieval_only_path_with_injected_query_embeddings() -> None:
    benchmark = _load_benchmark()
    case = benchmark.cases[0]
    benchmark = replace(benchmark, cases=(case,))

    class FakeRetriever:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def retrieve_with_metadata(self, **kwargs):
            self.calls.append(kwargs)
            return RetrievalResult(
                chunks=[
                    RetrievedChunk(
                        chunk_id=1,
                        chunk_index=0,
                        chunk_text="conventional, deterministic code",
                        notion_path="",
                        notion_page_id=None,
                        source_kind="pdf",
                        score=0.91,
                        source_document_id=101,
                        source_display_name=None,
                        locator="page 2",
                    )
                ],
                retrieval_mode=RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
                retrieval_fallback_reason=None,
                candidate_count=1,
                accepted_evidence_count=1,
            )

    retriever = FakeRetriever()
    evaluations = run_retrieval_cases(
        benchmark,
        query_embeddings=[[0.1, 0.2]],
        retriever=retriever,
        source_id_by_document_id={101: "production_agents"},
    )

    assert evaluations[0].passed is True
    assert retriever.calls[0]["top_k"] == 5
    assert retriever.calls[0]["allow_legacy_embedding_scoring"] is False
    assert retriever.calls[0]["query_embedding"] == [0.1, 0.2]
