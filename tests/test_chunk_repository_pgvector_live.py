from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument
from src.repositories import ChunkRepository

VECTOR_DIMENSIONS = 1536
ADMIN_DATABASE_URL = "postgresql+psycopg://learnloop:learnloop@localhost:5432/postgres"
DATABASE_PREFIX_ENV = "LEARNLOOP_PGVECTOR_TEST_DATABASE_PREFIX"
DEFAULT_DATABASE_PREFIX = "learnloop_step52_"
ALLOWED_DATABASE_PREFIXES = {DEFAULT_DATABASE_PREFIX, "learnloop_step98_"}


def _embedding(*leading_values: float) -> list[float]:
    values = [0.0] * VECTOR_DIMENSIONS
    for index, value in enumerate(leading_values):
        values[index] = float(value)
    return values


@pytest.fixture
def pgvector_session() -> Iterator[Session]:
    if os.getenv("LEARNLOOP_RUN_PGVECTOR_TESTS") != "1":
        pytest.skip("set LEARNLOOP_RUN_PGVECTOR_TESTS=1 to run live pgvector tests")

    admin_url = make_url(
        os.getenv("LEARNLOOP_PGVECTOR_ADMIN_DATABASE_URL", ADMIN_DATABASE_URL)
    )
    database_prefix = os.getenv(DATABASE_PREFIX_ENV, DEFAULT_DATABASE_PREFIX)
    if database_prefix not in ALLOWED_DATABASE_PREFIXES:
        raise RuntimeError("unapproved disposable database prefix")
    database_name = f"{database_prefix}{uuid.uuid4().hex[:8]}"
    test_database_url = admin_url.set(database=database_name)

    admin_engine = create_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    admin_engine.dispose()

    engine = create_engine(test_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

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
    session = local_session()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()

        cleanup_engine = create_engine(
            admin_url.render_as_string(hide_password=False),
            isolation_level="AUTOCOMMIT",
        )
        with cleanup_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        cleanup_engine.dispose()


def _seed_vector_query_fixture(session: Session) -> None:
    page_a = NotionPage(
        id=1,
        notion_page_id="page-a",
        title="Page A",
        notion_path="Knowledge/PageA",
    )
    page_b = NotionPage(
        id=2,
        notion_page_id="page-b",
        title="Page B",
        notion_path="Knowledge/PageB",
    )
    session.add_all([page_a, page_b])
    session.flush()

    blocks = [
        NotionBlock(
            id=1,
            notion_block_id="blk-a-target-live",
            notion_page_id=page_a.id,
            parent_block_id=None,
            block_type="paragraph",
            content_text="Page A target semantic note",
            block_path="Knowledge/PageA/Target/Live",
            block_order=0,
        ),
        NotionBlock(
            id=2,
            notion_block_id="blk-a-target-null",
            notion_page_id=page_a.id,
            parent_block_id=None,
            block_type="paragraph",
            content_text="Page A target legacy null vector",
            block_path="Knowledge/PageA/Target/Legacy",
            block_order=1,
        ),
        NotionBlock(
            id=3,
            notion_block_id="blk-a-other-perfect",
            notion_page_id=page_a.id,
            parent_block_id=None,
            block_type="paragraph",
            content_text="Page A other perfect semantic note",
            block_path="Knowledge/PageA/Other/Perfect",
            block_order=2,
        ),
        NotionBlock(
            id=4,
            notion_block_id="blk-b-global-best",
            notion_page_id=page_b.id,
            parent_block_id=None,
            block_type="paragraph",
            content_text="Page B global best semantic note",
            block_path="Knowledge/PageB/Best",
            block_order=0,
        ),
    ]
    session.add_all(blocks)
    session.flush()

    source_document = SourceDocument(
        id=11,
        source_type="pdf",
        source_display_name="source.pdf",
        content_hash="hash-source",
        raw_text="source raw text",
    )
    session.add(source_document)
    session.flush()

    chunks = [
        KnowledgeChunk(
            id=1,
            source_document_id=None,
            notion_block_id=1,
            chunk_index=0,
            chunk_text="Page A target semantic note",
            notion_path="Knowledge/PageA/Target/Live",
            embedding=_embedding(0.8, 0.2),
            embedding_text=None,
            source_kind="notion",
        ),
        KnowledgeChunk(
            id=2,
            source_document_id=None,
            notion_block_id=2,
            chunk_index=1,
            chunk_text="Page A target legacy null vector",
            notion_path="Knowledge/PageA/Target/Legacy",
            embedding=None,
            embedding_text=None,
            source_kind="notion",
        ),
        KnowledgeChunk(
            id=3,
            source_document_id=None,
            notion_block_id=3,
            chunk_index=2,
            chunk_text="Page A other perfect semantic note",
            notion_path="Knowledge/PageA/Other/Perfect",
            embedding=_embedding(1.0, 0.0),
            embedding_text=None,
            source_kind="notion",
        ),
        KnowledgeChunk(
            id=4,
            source_document_id=None,
            notion_block_id=4,
            chunk_index=0,
            chunk_text="Page B global best semantic note",
            notion_path="Knowledge/PageB/Best",
            embedding=_embedding(1.0, 0.0),
            embedding_text=None,
            source_kind="notion",
        ),
        KnowledgeChunk(
            id=5,
            source_document_id=source_document.id,
            notion_block_id=None,
            chunk_index=0,
            chunk_text="Source document perfect semantic note",
            notion_path=None,
            embedding=_embedding(1.0, 0.0),
            embedding_text=None,
            source_kind="source_document",
        ),
    ]
    session.add_all(chunks)
    session.commit()


def test_vector_query_applies_page_filter_before_top_k(
    pgvector_session: Session,
) -> None:
    _seed_vector_query_fixture(pgvector_session)
    repository = ChunkRepository(pgvector_session)

    results = repository.list_production_chunks_by_vector(
        query_embedding=_embedding(1.0, 0.0),
        top_k=1,
        page_ids=["page-a"],
    )

    assert len(results) == 1
    assert results[0].chunk_id == 3
    assert results[0].notion_page_id == "page-a"
    assert results[0].notion_path == "Knowledge/PageA/Other/Perfect"


def test_vector_query_applies_section_filter_and_skips_null_vectors(
    pgvector_session: Session,
) -> None:
    _seed_vector_query_fixture(pgvector_session)
    repository = ChunkRepository(pgvector_session)

    results = repository.list_production_chunks_by_vector(
        query_embedding=_embedding(1.0, 0.0),
        top_k=5,
        section_paths=["Knowledge/PageA/Target"],
    )

    assert len(results) == 1
    assert results[0].chunk_id == 1
    assert results[0].notion_path == "Knowledge/PageA/Target/Live"
    assert results[0].score > 0.0


def test_vector_query_keeps_production_safe_source_kind_scope(
    pgvector_session: Session,
) -> None:
    _seed_vector_query_fixture(pgvector_session)
    repository = ChunkRepository(pgvector_session)

    results = repository.list_production_chunks_by_vector(
        query_embedding=_embedding(1.0, 0.0),
        top_k=5,
        source_kinds=["source_document"],
    )

    assert results == []
