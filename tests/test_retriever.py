import json
from typing import List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument
from src.rag import (
    ProductionChunkRetriever,
    RETRIEVAL_MODE_LEXICAL_FALLBACK,
    RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE,
)
from src.repositories import (
    ChunkRepository,
    ChunkVectorQueryError,
    RetrievalChunkCandidate,
    SemanticChunkMatch,
)


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


def _seed_retrieval_data(session: Session) -> None:
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
        content_text="Attention uses query key value vectors",
        block_path="Knowledge/NLP/Week5/Attention",
        block_order=0,
    )
    block_transformer = NotionBlock(
        id=2,
        notion_block_id="blk-transformer",
        notion_page_id=page_nlp.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="Transformer encoder has multi-head attention",
        block_path="Knowledge/NLP/Week5/Transformer",
        block_order=1,
    )
    block_regression = NotionBlock(
        id=3,
        notion_block_id="blk-regression",
        notion_page_id=page_ml.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="Linear regression basics",
        block_path="Knowledge/ML/Week1/Regression",
        block_order=0,
    )
    session.add_all([block_attention, block_transformer, block_regression])
    session.flush()

    source_document = SourceDocument(
        id=11,
        source_type="pdf",
        source_display_name="ml-paper.pdf",
        content_hash="hash-1",
        raw_text="private source text",
    )
    session.add(source_document)
    session.flush()

    chunks = [
        KnowledgeChunk(
            id=1,
            source_document_id=None,
            notion_block_id=block_attention.id,
            chunk_index=0,
            chunk_text="Attention uses query key value vectors",
            notion_path="Knowledge/NLP/Week5/Attention",
            embedding_text=json.dumps([0.9, 0.1]),
            source_kind="notion",
        ),
        KnowledgeChunk(
            id=2,
            source_document_id=None,
            notion_block_id=block_transformer.id,
            chunk_index=1,
            chunk_text="Transformer encoder has multi-head attention",
            notion_path="Knowledge/NLP/Week5/Transformer",
            embedding_text=json.dumps([0.8, 0.2]),
            source_kind="notion",
        ),
        KnowledgeChunk(
            id=3,
            source_document_id=None,
            notion_block_id=block_regression.id,
            chunk_index=0,
            chunk_text="Linear regression basics",
            notion_path="Knowledge/ML/Week1/Regression",
            embedding_text=json.dumps([0.1, 0.9]),
            source_kind="notion",
        ),
        KnowledgeChunk(
            id=4,
            source_document_id=source_document.id,
            notion_block_id=None,
            chunk_index=0,
            chunk_text="External PDF source attention note",
            notion_path=None,
            embedding_text=json.dumps([1.0, 0.0]),
            source_kind="source_document",
        ),
    ]
    session.add_all(chunks)
    session.commit()


def test_retriever_returns_ranked_notion_chunks_only_in_production_scope() -> None:
    session = _build_test_session()
    _seed_retrieval_data(session)
    retriever = ProductionChunkRetriever(chunk_repository=ChunkRepository(session))

    results = retriever.retrieve(query_text="attention transformer", top_k=3)

    assert len(results) == 2
    assert [chunk.chunk_id for chunk in results] == [2, 1]
    assert all(chunk.source_kind == "notion" for chunk in results)


def test_retriever_filters_by_page_scope() -> None:
    session = _build_test_session()
    _seed_retrieval_data(session)
    retriever = ProductionChunkRetriever(chunk_repository=ChunkRepository(session))

    results = retriever.retrieve(
        query_text="regression",
        page_ids=["page-ml-week1"],
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].notion_page_id == "page-ml-week1"
    assert results[0].notion_path == "Knowledge/ML/Week1/Regression"


def test_retriever_filters_by_section_scope() -> None:
    session = _build_test_session()
    _seed_retrieval_data(session)
    retriever = ProductionChunkRetriever(chunk_repository=ChunkRepository(session))

    results = retriever.retrieve(
        query_text="attention",
        section_paths=["Knowledge/NLP/Week5/Attention"],
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].notion_path == "Knowledge/NLP/Week5/Attention"


def test_retriever_applies_source_kind_filter() -> None:
    session = _build_test_session()
    _seed_retrieval_data(session)
    retriever = ProductionChunkRetriever(chunk_repository=ChunkRepository(session))

    results = retriever.retrieve(
        query_text="attention",
        source_kinds=["source_document"],
        top_k=5,
    )

    assert results == []


def test_retriever_supports_embedding_only_query() -> None:
    session = _build_test_session()
    _seed_retrieval_data(session)
    retriever = ProductionChunkRetriever(chunk_repository=ChunkRepository(session))

    results = retriever.retrieve(
        query_text="",
        query_embedding=[1.0, 0.0],
        top_k=1,
    )

    assert len(results) == 1
    assert results[0].chunk_id == 1


def test_retriever_lexical_path_remains_safe_with_mixed_vector_state() -> None:
    session = _build_test_session()
    _seed_retrieval_data(session)

    legacy_chunk = session.get(KnowledgeChunk, 1)
    assert legacy_chunk is not None
    legacy_chunk.embedding_text = None
    session.commit()

    retriever = ProductionChunkRetriever(chunk_repository=ChunkRepository(session))

    results = retriever.retrieve(
        query_text="attention query key value",
        top_k=2,
    )

    assert len(results) == 2
    assert [chunk.chunk_id for chunk in results] == [1, 2]
    assert results[0].notion_path == "Knowledge/NLP/Week5/Attention"


class _FakeVectorRepository:
    def __init__(
        self,
        *,
        semantic_matches: Optional[List[SemanticChunkMatch]] = None,
        lexical_candidates: Optional[List[RetrievalChunkCandidate]] = None,
        raise_vector_error: bool = False,
    ) -> None:
        self._semantic_matches = semantic_matches or []
        self._lexical_candidates = lexical_candidates or []
        self._raise_vector_error = raise_vector_error

    def supports_vector_query(self) -> bool:
        return True

    def list_production_chunks_by_vector(self, **kwargs) -> list[SemanticChunkMatch]:
        _ = kwargs
        if self._raise_vector_error:
            raise ChunkVectorQueryError("pgvector query failed")
        return list(self._semantic_matches)

    def list_production_chunks(self, **kwargs) -> list[RetrievalChunkCandidate]:
        _ = kwargs
        return list(self._lexical_candidates)


def test_retriever_reports_pgvector_mode_when_semantic_query_succeeds() -> None:
    retriever = ProductionChunkRetriever(
        chunk_repository=_FakeVectorRepository(
            semantic_matches=[
                SemanticChunkMatch(
                    chunk_id=7,
                    chunk_index=0,
                    chunk_text="Attention uses query key value vectors",
                    notion_path="Knowledge/NLP/Week5/Attention",
                    source_kind="notion",
                    notion_page_id="page-nlp-week5",
                    score=0.93,
                )
            ]
        )
    )

    result = retriever.retrieve_with_metadata(
        query_text="attention",
        query_embedding=[0.9, 0.1],
        top_k=3,
        allow_legacy_embedding_scoring=False,
    )

    assert result.retrieval_mode == RETRIEVAL_MODE_PGVECTOR_EXACT_COSINE
    assert result.retrieval_fallback_reason is None
    assert [chunk.chunk_id for chunk in result.chunks] == [7]


def test_retriever_falls_back_when_vector_query_fails() -> None:
    retriever = ProductionChunkRetriever(
        chunk_repository=_FakeVectorRepository(
            lexical_candidates=[
                RetrievalChunkCandidate(
                    chunk_id=1,
                    chunk_index=0,
                    chunk_text="Attention uses query key value vectors",
                    notion_path="Knowledge/NLP/Week5/Attention",
                    source_kind="notion",
                    notion_page_id="page-nlp-week5",
                    embedding_text=json.dumps([0.9, 0.1]),
                )
            ],
            raise_vector_error=True,
        )
    )

    result = retriever.retrieve_with_metadata(
        query_text="attention query key",
        query_embedding=[0.9, 0.1],
        top_k=3,
        allow_legacy_embedding_scoring=False,
    )

    assert result.retrieval_mode == RETRIEVAL_MODE_LEXICAL_FALLBACK
    assert result.retrieval_fallback_reason == "VECTOR_QUERY_FAILED"
    assert [chunk.chunk_id for chunk in result.chunks] == [1]


def test_retriever_falls_back_when_scope_has_no_live_vectors() -> None:
    retriever = ProductionChunkRetriever(
        chunk_repository=_FakeVectorRepository(
            semantic_matches=[],
            lexical_candidates=[
                RetrievalChunkCandidate(
                    chunk_id=2,
                    chunk_index=0,
                    chunk_text="Attention uses query key value vectors",
                    notion_path="Knowledge/NLP/Week5/Attention",
                    source_kind="notion",
                    notion_page_id="page-nlp-week5",
                    embedding_text=None,
                )
            ],
        )
    )

    result = retriever.retrieve_with_metadata(
        query_text="attention query key",
        query_embedding=[0.9, 0.1],
        top_k=3,
        allow_legacy_embedding_scoring=False,
    )

    assert result.retrieval_mode == RETRIEVAL_MODE_LEXICAL_FALLBACK
    assert result.retrieval_fallback_reason == "VECTOR_DATA_UNAVAILABLE"
    assert [chunk.chunk_id for chunk in result.chunks] == [2]


def test_retriever_fallback_can_disable_legacy_embedding_scoring() -> None:
    retriever = ProductionChunkRetriever(
        chunk_repository=_FakeVectorRepository(
            lexical_candidates=[
                RetrievalChunkCandidate(
                    chunk_id=4,
                    chunk_index=0,
                    chunk_text="attention query key value vectors",
                    notion_path="Knowledge/NLP/Week5/Attention",
                    source_kind="notion",
                    notion_page_id="page-nlp-week5",
                    embedding_text=json.dumps([0.0, 1.0]),
                ),
                RetrievalChunkCandidate(
                    chunk_id=5,
                    chunk_index=1,
                    chunk_text="completely unrelated words",
                    notion_path="Knowledge/NLP/Week5/Noise",
                    source_kind="notion",
                    notion_page_id="page-nlp-week5",
                    embedding_text=json.dumps([1.0, 0.0]),
                ),
            ],
            raise_vector_error=True,
        )
    )

    result = retriever.retrieve_with_metadata(
        query_text="attention query key",
        query_embedding=[1.0, 0.0],
        top_k=2,
        allow_legacy_embedding_scoring=False,
    )

    assert result.retrieval_mode == RETRIEVAL_MODE_LEXICAL_FALLBACK
    assert result.retrieval_fallback_reason == "VECTOR_QUERY_FAILED"
    assert [chunk.chunk_id for chunk in result.chunks] == [4]
