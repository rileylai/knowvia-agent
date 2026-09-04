import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage
from src.repositories import (
    ChunkBlockMappingError,
    ChunkRepository,
    NotionChunkUpsert,
)


def _embedding(fill_value: float) -> list[float]:
    return [fill_value] * 1536


def _build_test_session() -> Session:
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
    return local_session()


def _seed_page_and_blocks(session: Session) -> tuple[int, int]:
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

    block_a_1 = NotionBlock(
        id=1,
        notion_block_id="blk-a-1",
        notion_page_id=page_a.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="A1",
        block_path="Knowledge/PageA/A1",
        block_order=0,
    )
    block_a_2 = NotionBlock(
        id=2,
        notion_block_id="blk-a-2",
        notion_page_id=page_a.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="A2",
        block_path="Knowledge/PageA/A2",
        block_order=1,
    )
    block_b_1 = NotionBlock(
        id=3,
        notion_block_id="blk-b-1",
        notion_page_id=page_b.id,
        parent_block_id=None,
        block_type="paragraph",
        content_text="B1",
        block_path="Knowledge/PageB/B1",
        block_order=0,
    )
    session.add_all([block_a_1, block_a_2, block_b_1])
    session.commit()
    return page_a.id, page_b.id


def test_chunk_repository_upsert_replaces_same_page_chunks_without_duplicates() -> None:
    session = _build_test_session()
    page_a_id, _ = _seed_page_and_blocks(session)
    repository = ChunkRepository(session)

    first_inserted = repository.upsert_chunks(
        notion_page_db_id=page_a_id,
        chunks=[
            NotionChunkUpsert(
                chunk_index=0,
                chunk_text="Chunk A1",
                notion_path="Knowledge/PageA/A1",
                notion_block_ids=["blk-a-1"],
                embedding=_embedding(0.1),
            ),
            NotionChunkUpsert(
                chunk_index=1,
                chunk_text="Chunk A2",
                notion_path="Knowledge/PageA/A2",
                notion_block_ids=["blk-a-2"],
            ),
        ],
    )
    assert len(first_inserted) == 2

    second_inserted = repository.upsert_chunks(
        notion_page_db_id=page_a_id,
        chunks=[
            NotionChunkUpsert(
                chunk_index=0,
                chunk_text="Chunk A updated",
                notion_path="Knowledge/PageA/Updated",
                notion_block_ids=["blk-a-2", "blk-a-1"],
                embedding=_embedding(0.3),
            )
        ],
    )

    assert len(second_inserted) == 1
    all_chunks = session.query(KnowledgeChunk).order_by(KnowledgeChunk.id.asc()).all()
    assert len(all_chunks) == 1
    assert all_chunks[0].chunk_text == "Chunk A updated"
    assert all_chunks[0].source_kind == "notion"
    assert all_chunks[0].notion_block_id == 2
    assert all_chunks[0].embedding == _embedding(0.3)
    assert json.loads(all_chunks[0].embedding_text or "[]") == _embedding(0.3)


def test_chunk_repository_upsert_keeps_other_page_chunks() -> None:
    session = _build_test_session()
    page_a_id, page_b_id = _seed_page_and_blocks(session)
    repository = ChunkRepository(session)

    repository.upsert_chunks(
        notion_page_db_id=page_b_id,
        chunks=[
            NotionChunkUpsert(
                chunk_index=0,
                chunk_text="Chunk B1",
                notion_path="Knowledge/PageB/B1",
                notion_block_ids=["blk-b-1"],
            )
        ],
    )

    repository.upsert_chunks(
        notion_page_db_id=page_a_id,
        chunks=[
            NotionChunkUpsert(
                chunk_index=0,
                chunk_text="Chunk A1",
                notion_path="Knowledge/PageA/A1",
                notion_block_ids=["blk-a-1"],
            )
        ],
    )

    all_chunks = session.query(KnowledgeChunk).order_by(KnowledgeChunk.id.asc()).all()
    assert len(all_chunks) == 2
    assert all_chunks[0].chunk_text == "Chunk B1"
    assert all_chunks[1].chunk_text == "Chunk A1"


def test_chunk_repository_delete_page_chunks_keeps_other_pages() -> None:
    session = _build_test_session()
    page_a_id, page_b_id = _seed_page_and_blocks(session)
    repository = ChunkRepository(session)

    repository.upsert_chunks(
        notion_page_db_id=page_a_id,
        chunks=[
            NotionChunkUpsert(
                chunk_index=0,
                chunk_text="Chunk A1",
                notion_path="Knowledge/PageA/A1",
                notion_block_ids=["blk-a-1"],
            )
        ],
    )
    repository.upsert_chunks(
        notion_page_db_id=page_b_id,
        chunks=[
            NotionChunkUpsert(
                chunk_index=0,
                chunk_text="Chunk B1",
                notion_path="Knowledge/PageB/B1",
                notion_block_ids=["blk-b-1"],
            )
        ],
    )

    deleted_count = repository.delete_page_chunks(notion_page_db_id=page_a_id)

    assert deleted_count == 1
    all_chunks = session.query(KnowledgeChunk).order_by(KnowledgeChunk.id.asc()).all()
    assert len(all_chunks) == 1
    assert all_chunks[0].chunk_text == "Chunk B1"


def test_chunk_repository_upsert_flushes_without_commit() -> None:
    session = _build_test_session()
    page_a_id, _ = _seed_page_and_blocks(session)
    repository = ChunkRepository(session)

    inserted = repository.upsert_chunks(
        notion_page_db_id=page_a_id,
        chunks=[
            NotionChunkUpsert(
                chunk_index=0,
                chunk_text="Chunk A1",
                notion_path="Knowledge/PageA/A1",
                notion_block_ids=["blk-a-1"],
            )
        ],
    )

    assert len(inserted) == 1
    assert session.query(KnowledgeChunk).count() == 1

    session.rollback()

    assert session.query(KnowledgeChunk).count() == 0


def test_chunk_repository_delete_page_chunks_flushes_without_commit() -> None:
    session = _build_test_session()
    page_a_id, _ = _seed_page_and_blocks(session)
    repository = ChunkRepository(session)
    repository.upsert_chunks(
        notion_page_db_id=page_a_id,
        chunks=[
            NotionChunkUpsert(
                chunk_index=0,
                chunk_text="Chunk A1",
                notion_path="Knowledge/PageA/A1",
                notion_block_ids=["blk-a-1"],
            )
        ],
    )
    session.commit()

    deleted_count = repository.delete_page_chunks(notion_page_db_id=page_a_id)

    assert deleted_count == 1
    assert session.query(KnowledgeChunk).count() == 0

    session.rollback()

    assert session.query(KnowledgeChunk).count() == 1


def test_chunk_repository_upsert_raises_when_block_mapping_missing() -> None:
    session = _build_test_session()
    page_a_id, _ = _seed_page_and_blocks(session)
    repository = ChunkRepository(session)

    with pytest.raises(ChunkBlockMappingError):
        repository.upsert_chunks(
            notion_page_db_id=page_a_id,
            chunks=[
                NotionChunkUpsert(
                    chunk_index=0,
                    chunk_text="Chunk A",
                    notion_path="Knowledge/PageA/A1",
                    notion_block_ids=["missing-block"],
                )
            ],
        )
