from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage
from src.rag import ProductionChunkRetriever
from src.repositories import ChunkRepository

try:
    from .golden_questions import (
        DEFAULT_GOLDEN_QUESTIONS_PATH,
        GoldenQuestion,
        GoldenQuestionSet,
        load_golden_questions,
    )
except ImportError:
    from golden_questions import (  # type: ignore[no-redef]
        DEFAULT_GOLDEN_QUESTIONS_PATH,
        GoldenQuestion,
        GoldenQuestionSet,
        load_golden_questions,
    )


@dataclass(frozen=True)
class RetrievalQuestionResult:
    question_id: str
    expected_paths: List[str]
    retrieved_paths: List[str]
    hit: bool


@dataclass(frozen=True)
class RetrievalEvalResult:
    total_questions: int
    hit_count: int
    hit_rate: float
    question_results: List[RetrievalQuestionResult]


def evaluate_retrieval_hit_rate(
    *,
    session: Session,
    question_set: GoldenQuestionSet,
) -> RetrievalEvalResult:
    retriever = ProductionChunkRetriever(
        chunk_repository=ChunkRepository(session)
    )
    question_results: List[RetrievalQuestionResult] = []

    for question in question_set.questions:
        if not question.checks.retrieval_hit_rate:
            continue

        retrieved_chunks = retriever.retrieve(
            query_text=question.query,
            top_k=question.scope.top_k,
            page_ids=question.scope.page_ids,
            section_paths=question.scope.section_paths,
            source_kinds=question.scope.source_kinds,
        )
        retrieved_paths = [chunk.notion_path for chunk in retrieved_chunks]
        hit = any(
            expected_path in retrieved_paths
            for expected_path in question.expected.paths
        )
        question_results.append(
            RetrievalQuestionResult(
                question_id=question.id,
                expected_paths=question.expected.paths,
                retrieved_paths=retrieved_paths,
                hit=hit,
            )
        )

    total_questions = len(question_results)
    hit_count = sum(1 for result in question_results if result.hit)
    hit_rate = hit_count / total_questions if total_questions else 0.0
    return RetrievalEvalResult(
        total_questions=total_questions,
        hit_count=hit_count,
        hit_rate=hit_rate,
        question_results=question_results,
    )


def build_synthetic_retrieval_session(
    question_set: GoldenQuestionSet,
    *,
    missing_question_ids: Optional[Set[str]] = None,
) -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            NotionPage.__table__,
            NotionBlock.__table__,
            KnowledgeChunk.__table__,
        ],
    )
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = local_session()
    seed_synthetic_retrieval_fixture(
        session=session,
        question_set=question_set,
        missing_question_ids=missing_question_ids or set(),
    )
    return session


def seed_synthetic_retrieval_fixture(
    *,
    session: Session,
    question_set: GoldenQuestionSet,
    missing_question_ids: Set[str],
) -> None:
    page_db_ids_by_notion_id: dict[str, int] = {}
    next_page_db_id = 1
    next_block_db_id = 1
    next_chunk_db_id = 1

    for question in question_set.questions:
        notion_page_id = _select_notion_page_id(question)
        if notion_page_id not in page_db_ids_by_notion_id:
            page_db_id = next_page_db_id
            next_page_db_id += 1
            page_path = _derive_page_path(question)
            page_db_ids_by_notion_id[notion_page_id] = page_db_id
            session.add(
                NotionPage(
                    id=page_db_id,
                    notion_page_id=notion_page_id,
                    title=page_path.split("/")[-1],
                    notion_path=page_path,
                )
            )

        if question.id in missing_question_ids:
            continue

        for expected_path in question.expected.paths:
            block_db_id = next_block_db_id
            next_block_db_id += 1
            chunk_text = _build_synthetic_chunk_text(question)
            session.add(
                NotionBlock(
                    id=block_db_id,
                    notion_block_id=f"block-{question.id}-{block_db_id}",
                    notion_page_id=page_db_ids_by_notion_id[notion_page_id],
                    parent_block_id=None,
                    block_type="paragraph",
                    content_text=chunk_text,
                    block_path=expected_path,
                    block_order=block_db_id,
                )
            )
            session.add(
                KnowledgeChunk(
                    id=next_chunk_db_id,
                    source_document_id=None,
                    notion_block_id=block_db_id,
                    chunk_index=0,
                    chunk_text=chunk_text,
                    notion_path=expected_path,
                    embedding_text=None,
                    source_kind="notion",
                )
            )
            next_chunk_db_id += 1

    session.add(
        KnowledgeChunk(
            id=next_chunk_db_id,
            source_document_id=None,
            notion_block_id=None,
            chunk_index=0,
            chunk_text="pending change request content rejected change request content",
            notion_path="Synthetic/NonProduction/Pending",
            embedding_text=None,
            source_kind="source_document",
        )
    )
    session.commit()


def format_retrieval_eval_result(result: RetrievalEvalResult) -> str:
    lines = [
        (
            "retrieval_hit_rate: "
            f"{result.hit_rate:.3f} ({result.hit_count}/{result.total_questions})"
        ),
        "question_results:",
    ]
    for question_result in result.question_results:
        status = "hit" if question_result.hit else "miss"
        lines.append(
            "- "
            f"{question_result.question_id}: {status}; "
            f"expected={question_result.expected_paths}; "
            f"retrieved_top_k={question_result.retrieved_paths}"
        )
    return "\n".join(lines)


def _select_notion_page_id(question: GoldenQuestion) -> str:
    if question.scope.page_ids:
        return question.scope.page_ids[0]
    return f"page-{question.id}"


def _derive_page_path(question: GoldenQuestion) -> str:
    candidate_path = (
        question.scope.section_paths[0]
        if question.scope.section_paths
        else question.expected.paths[0]
    )
    if "/AI Supplement Zone" in candidate_path:
        return candidate_path.split("/AI Supplement Zone", 1)[0]

    segments = [segment for segment in candidate_path.split("/") if segment]
    if len(segments) >= 3:
        return "/".join(segments[:3])
    return candidate_path


def _build_synthetic_chunk_text(question: GoldenQuestion) -> str:
    expected_terms = " ".join(question.expected.must_include)
    return f"{question.query} {expected_terms} synthetic production note"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure top-k retrieval hit rate for golden questions."
    )
    parser.add_argument(
        "--golden-questions",
        type=Path,
        default=DEFAULT_GOLDEN_QUESTIONS_PATH,
    )
    args = parser.parse_args()

    question_set = load_golden_questions(args.golden_questions)
    session = build_synthetic_retrieval_session(question_set)
    try:
        result = evaluate_retrieval_hit_rate(
            session=session,
            question_set=question_set,
        )
    finally:
        session.close()

    print(format_retrieval_eval_result(result))
    if result.hit_count != result.total_questions:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
