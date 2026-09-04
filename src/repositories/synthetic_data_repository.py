from __future__ import annotations

from dataclasses import dataclass
from typing import List

from sqlalchemy.orm import Session

from src.db.models import KnowledgeChunk, NotionBlock, NotionPage
from src.policies.synthetic_data import SYNTHETIC_NOTION_PAGE_IDS


@dataclass(frozen=True)
class SyntheticDataCounts:
    page_count: int
    block_count: int
    chunk_count: int
    production_chunk_count: int
    vector_chunk_count: int

    @property
    def is_clean(self) -> bool:
        return self.page_count == 0 and self.block_count == 0 and self.chunk_count == 0


class SyntheticDataRepository:
    """Inspect and remove only the fixed synthetic page allowlist."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def inspect(self) -> SyntheticDataCounts:
        page_db_ids = self._synthetic_page_db_ids()
        if not page_db_ids:
            return SyntheticDataCounts(0, 0, 0, 0, 0)

        block_db_ids = self._synthetic_block_db_ids(page_db_ids)
        if not block_db_ids:
            return SyntheticDataCounts(
                page_count=len(page_db_ids),
                block_count=0,
                chunk_count=0,
                production_chunk_count=0,
                vector_chunk_count=0,
            )

        chunk_query = self._session.query(KnowledgeChunk).filter(
            KnowledgeChunk.notion_block_id.in_(block_db_ids)
        )
        chunks = chunk_query.all()
        return SyntheticDataCounts(
            page_count=len(page_db_ids),
            block_count=len(block_db_ids),
            chunk_count=len(chunks),
            production_chunk_count=sum(
                1 for chunk in chunks if chunk.source_kind == "notion"
            ),
            vector_chunk_count=sum(
                1
                for chunk in chunks
                if chunk.source_kind == "notion" and chunk.embedding is not None
            ),
        )

    def delete_synthetic_data(self) -> SyntheticDataCounts:
        before = self.inspect()
        page_db_ids = self._synthetic_page_db_ids()
        if not page_db_ids:
            return before

        block_db_ids = self._synthetic_block_db_ids(page_db_ids)
        if block_db_ids:
            self._session.query(KnowledgeChunk).filter(
                KnowledgeChunk.notion_block_id.in_(block_db_ids)
            ).delete(synchronize_session=False)
            self._session.query(NotionBlock).filter(
                NotionBlock.id.in_(block_db_ids)
            ).delete(synchronize_session=False)
        self._session.query(NotionPage).filter(
            NotionPage.id.in_(page_db_ids)
        ).delete(synchronize_session=False)
        self._session.flush()
        return before

    def _synthetic_page_db_ids(self) -> List[int]:
        rows = (
            self._session.query(NotionPage.id)
            .filter(NotionPage.notion_page_id.in_(SYNTHETIC_NOTION_PAGE_IDS))
            .all()
        )
        return [int(row[0]) for row in rows]

    def _synthetic_block_db_ids(self, page_db_ids: List[int]) -> List[int]:
        rows = (
            self._session.query(NotionBlock.id)
            .filter(NotionBlock.notion_page_id.in_(page_db_ids))
            .all()
        )
        return [int(row[0]) for row in rows]
