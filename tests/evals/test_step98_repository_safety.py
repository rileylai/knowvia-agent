from __future__ import annotations

import itertools

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument
from src.rag import ProductionChunkRetriever
from src.repositories import ChunkRepository


@pytest.mark.parametrize(
    "decoy_kind",
    list(
        itertools.chain.from_iterable(
            ([kind, kind] for kind in ("pending", "rejected", "non_notion", "wrong_page", "wrong_section"))
        )
    ),
)
def test_step98_safety_decoy_is_filtered_before_top_k(decoy_kind: str) -> None:
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
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        session.add_all(
            [
                NotionPage(id=1, notion_page_id="allowed-page", title="Allowed", notion_path="Allowed"),
                NotionPage(id=2, notion_page_id="wrong-page", title="Wrong", notion_path="Wrong"),
                NotionBlock(id=1, notion_block_id="allowed-block", notion_page_id=1, parent_block_id=None, block_type="paragraph", content_text="target phrase", block_path="Allowed/Section", block_order=0),
                NotionBlock(id=2, notion_block_id="wrong-page-block", notion_page_id=2, parent_block_id=None, block_type="paragraph", content_text="target phrase target phrase", block_path="Wrong/Section", block_order=0),
                NotionBlock(id=3, notion_block_id="wrong-section-block", notion_page_id=1, parent_block_id=None, block_type="paragraph", content_text="target phrase target phrase", block_path="Allowed/Other", block_order=1),
                KnowledgeChunk(id=1, source_document_id=None, notion_block_id=1, chunk_index=0, chunk_text="target phrase supporting evidence", notion_path="Allowed/Section", embedding_text=None, source_kind="notion"),
            ]
        )
        if decoy_kind == "wrong_page":
            decoy = KnowledgeChunk(id=2, source_document_id=None, notion_block_id=2, chunk_index=0, chunk_text="target phrase target phrase", notion_path="Wrong/Section", embedding_text=None, source_kind="notion")
        elif decoy_kind == "wrong_section":
            decoy = KnowledgeChunk(id=2, source_document_id=None, notion_block_id=3, chunk_index=0, chunk_text="target phrase target phrase", notion_path="Allowed/Other", embedding_text=None, source_kind="notion")
        else:
            decoy = KnowledgeChunk(id=2, source_document_id=None, notion_block_id=None, chunk_index=0, chunk_text=f"target phrase target phrase {decoy_kind}", notion_path=f"Excluded/{decoy_kind}", embedding_text=None, source_kind="source_document")
        session.add(decoy)
        session.commit()

        repository = ChunkRepository(session)
        eligible = repository.list_production_chunks(
            page_ids=["allowed-page"],
            section_paths=["Allowed/Section"],
            source_kinds=["notion"],
        )
        assert [candidate.chunk_id for candidate in eligible] == [1]

        retrieved = ProductionChunkRetriever(chunk_repository=repository).retrieve(
            query_text="target phrase",
            top_k=1,
            page_ids=["allowed-page"],
            section_paths=["Allowed/Section"],
            source_kinds=["notion"],
        )
        assert [chunk.chunk_id for chunk in retrieved] == [1]
    finally:
        session.close()
