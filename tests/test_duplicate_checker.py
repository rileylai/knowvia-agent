from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument
from src.repositories import ChunkRepository
from src.services import DuplicateKnowledgeChecker


def _build_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            NotionPage.__table__,
            NotionBlock.__table__,
            SourceDocument.__table__,
            KnowledgeChunk.__table__,
        ],
    )
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return local_session()


def _seed_chunk_data(session: Session) -> None:
    page_nlp = NotionPage(
        id=1,
        notion_page_id="page-nlp-week5",
        title="NLP Week 5",
        notion_path="Knowledge/NLP/Week5",
    )
    page_ml = NotionPage(
        id=2,
        notion_page_id="page-ml-week1",
        title="ML Week 1",
        notion_path="Knowledge/ML/Week1",
    )
    session.add_all([page_nlp, page_ml])
    session.flush()

    block_attention = NotionBlock(
        id=1,
        notion_block_id="blk-attn",
        notion_page_id=page_nlp.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="Transformer attention uses query key value vectors for context weighting",
        block_path="Knowledge/NLP/Week5/Attention",
        block_order=0,
    )
    block_regression = NotionBlock(
        id=2,
        notion_block_id="blk-regression",
        notion_page_id=page_ml.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="Linear regression estimates coefficients with least squares",
        block_path="Knowledge/ML/Week1/Regression",
        block_order=0,
    )
    session.add_all([block_attention, block_regression])
    session.flush()

    source_document = SourceDocument(
        id=11,
        source_type="pdf",
        source_display_name="week5.pdf",
        content_hash="hash-source",
        raw_text="private source text",
    )
    session.add(source_document)
    session.flush()

    session.add_all(
        [
            KnowledgeChunk(
                id=1,
                source_document_id=None,
                notion_block_id=block_attention.id,
                chunk_index=0,
                chunk_text=(
                    "Transformer attention uses query key value vectors for context weighting"
                ),
                notion_path="Knowledge/NLP/Week5/Attention",
                embedding_text=None,
                source_kind="notion",
            ),
            KnowledgeChunk(
                id=2,
                source_document_id=None,
                notion_block_id=block_regression.id,
                chunk_index=0,
                chunk_text="Linear regression estimates coefficients with least squares",
                notion_path="Knowledge/ML/Week1/Regression",
                embedding_text=None,
                source_kind="notion",
            ),
            KnowledgeChunk(
                id=3,
                source_document_id=source_document.id,
                notion_block_id=None,
                chunk_index=0,
                chunk_text="source document chunk that should stay non-production",
                notion_path=None,
                embedding_text=None,
                source_kind="source_document",
            ),
        ]
    )
    session.commit()


def test_duplicate_checker_detects_hash_match_and_returns_citation_path() -> None:
    session = _build_test_session()
    _seed_chunk_data(session)

    checker = DuplicateKnowledgeChecker(chunk_repository=ChunkRepository(session))
    result = checker.check_duplicate(
        candidate_text=(
            "  transformer attention uses query key value vectors for context weighting  "
        ),
    )

    assert result.is_duplicate is True
    assert result.matched is not None
    assert result.matched.match_type == "hash_match"
    assert result.matched.notion_path == "Knowledge/NLP/Week5/Attention"
    assert result.matched.similarity_score == 1.0


def test_duplicate_checker_detects_similarity_match() -> None:
    session = _build_test_session()
    _seed_chunk_data(session)

    checker = DuplicateKnowledgeChecker(
        chunk_repository=ChunkRepository(session),
        similarity_threshold=0.72,
    )
    result = checker.check_duplicate(
        candidate_text="Transformer attention uses query key value vectors to weight context",
    )

    assert result.is_duplicate is True
    assert result.matched is not None
    assert result.matched.match_type == "similarity_match"
    assert result.matched.notion_path == "Knowledge/NLP/Week5/Attention"
    assert result.matched.similarity_score >= 0.72


def test_duplicate_checker_returns_not_duplicate_for_unrelated_text() -> None:
    session = _build_test_session()
    _seed_chunk_data(session)

    checker = DuplicateKnowledgeChecker(chunk_repository=ChunkRepository(session))
    result = checker.check_duplicate(
        candidate_text="K-means clustering groups points into k clusters by distance",
    )

    assert result.is_duplicate is False
    assert result.matched is None


def test_duplicate_checker_honors_page_scope_filter() -> None:
    session = _build_test_session()
    _seed_chunk_data(session)

    checker = DuplicateKnowledgeChecker(chunk_repository=ChunkRepository(session))

    filtered_out = checker.check_duplicate(
        candidate_text="Linear regression estimates coefficients with least squares",
        page_ids=["page-nlp-week5"],
    )
    assert filtered_out.is_duplicate is False
    assert filtered_out.matched is None

    filtered_in = checker.check_duplicate(
        candidate_text="Linear regression estimates coefficients with least squares",
        page_ids=["page-ml-week1"],
    )
    assert filtered_in.is_duplicate is True
    assert filtered_in.matched is not None
    assert filtered_in.matched.notion_path == "Knowledge/ML/Week1/Regression"
