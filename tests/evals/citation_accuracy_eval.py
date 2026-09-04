from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set

from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.rag import ProductionChunkRetriever
from src.repositories import ChunkRepository

try:
    from .golden_questions import (
        DEFAULT_GOLDEN_QUESTIONS_PATH,
        GoldenQuestionSet,
        load_golden_questions,
    )
    from .retrieval_eval import build_synthetic_retrieval_session
except ImportError:
    from golden_questions import (  # type: ignore[no-redef]
        DEFAULT_GOLDEN_QUESTIONS_PATH,
        GoldenQuestionSet,
        load_golden_questions,
    )
    from retrieval_eval import build_synthetic_retrieval_session  # type: ignore[no-redef]


DEFAULT_CITATION_ACCURACY_THRESHOLD = 1.0


@dataclass(frozen=True)
class CitationQuestionResult:
    question_id: str
    expected_paths: List[str]
    citation_paths: List[str]
    accurate: bool


@dataclass(frozen=True)
class CitationAccuracyEvalResult:
    total_questions: int
    accurate_count: int
    accuracy: float
    threshold: float
    passed: bool
    question_results: List[CitationQuestionResult]


def evaluate_citation_accuracy(
    *,
    session: Session,
    question_set: GoldenQuestionSet,
    threshold: float = DEFAULT_CITATION_ACCURACY_THRESHOLD,
) -> CitationAccuracyEvalResult:
    normalized_threshold = _normalize_threshold(threshold)
    retriever = ProductionChunkRetriever(
        chunk_repository=ChunkRepository(session)
    )
    question_results: List[CitationQuestionResult] = []

    for question in question_set.questions:
        if not question.checks.citation_accuracy:
            continue

        retrieved_chunks = retriever.retrieve(
            query_text=question.query,
            top_k=question.scope.top_k,
            page_ids=question.scope.page_ids,
            section_paths=question.scope.section_paths,
            source_kinds=question.scope.source_kinds,
        )
        citation_paths = _build_citation_paths(
            chunk.notion_path for chunk in retrieved_chunks
        )
        accurate = any(
            expected_path in citation_paths
            for expected_path in question.expected.paths
        )
        question_results.append(
            CitationQuestionResult(
                question_id=question.id,
                expected_paths=question.expected.paths,
                citation_paths=citation_paths,
                accurate=accurate,
            )
        )

    total_questions = len(question_results)
    accurate_count = sum(1 for result in question_results if result.accurate)
    accuracy = accurate_count / total_questions if total_questions else 0.0
    return CitationAccuracyEvalResult(
        total_questions=total_questions,
        accurate_count=accurate_count,
        accuracy=accuracy,
        threshold=normalized_threshold,
        passed=accuracy >= normalized_threshold,
        question_results=question_results,
    )


def format_citation_accuracy_eval_result(
    result: CitationAccuracyEvalResult,
) -> str:
    status = "pass" if result.passed else "fail"
    lines = [
        (
            "citation_accuracy: "
            f"{result.accuracy:.3f} "
            f"({result.accurate_count}/{result.total_questions})"
        ),
        f"threshold: {result.threshold:.3f}",
        f"status: {status}",
        "question_results:",
    ]
    for question_result in result.question_results:
        question_status = "accurate" if question_result.accurate else "inaccurate"
        lines.append(
            "- "
            f"{question_result.question_id}: {question_status}; "
            f"expected={question_result.expected_paths}; "
            f"citations={question_result.citation_paths}"
        )
    return "\n".join(lines)


def _build_citation_paths(paths: Iterable[str]) -> List[str]:
    citation_paths: List[str] = []
    seen_paths = set()
    for value in paths:
        path = str(value).strip()
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        citation_paths.append(path)
    return citation_paths


def _normalize_threshold(threshold: float) -> float:
    normalized = float(threshold)
    if normalized < 0.0 or normalized > 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure deterministic citation path accuracy for golden questions."
    )
    parser.add_argument(
        "--golden-questions",
        type=Path,
        default=DEFAULT_GOLDEN_QUESTIONS_PATH,
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_CITATION_ACCURACY_THRESHOLD,
    )
    parser.add_argument(
        "--missing-question-id",
        action="append",
        default=[],
        help="Synthetic fixture helper for testing misses.",
    )
    args = parser.parse_args()

    question_set = load_golden_questions(args.golden_questions)
    session = build_synthetic_retrieval_session(
        question_set,
        missing_question_ids=set(args.missing_question_id),
    )
    try:
        result = evaluate_citation_accuracy(
            session=session,
            question_set=question_set,
            threshold=args.threshold,
        )
    finally:
        session.close()

    print(format_citation_accuracy_eval_result(result))
    if not result.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
