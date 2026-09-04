from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.db.models import NotionBlock


@dataclass
class NotionBlockSnapshot:
    notion_block_id: str
    block_type: str
    content_text: str
    block_path: str
    children: List["NotionBlockSnapshot"] = field(default_factory=list)


class NotionBlockRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _allocate_block_id_for_sqlite(self) -> int:
        max_id = self._session.query(func.max(NotionBlock.id)).scalar()
        return int(max_id or 0) + 1

    def list_blocks_by_page_id(self, notion_page_db_id: int) -> List[NotionBlock]:
        return (
            self._session.query(NotionBlock)
            .filter(NotionBlock.notion_page_id == notion_page_db_id)
            .order_by(NotionBlock.parent_block_id.asc(), NotionBlock.block_order.asc(), NotionBlock.id.asc())
            .all()
        )

    def replace_page_blocks(
        self,
        *,
        notion_page_db_id: int,
        root_blocks: List[NotionBlockSnapshot],
    ) -> List[NotionBlock]:
        self._session.query(NotionBlock).filter(
            NotionBlock.notion_page_id == notion_page_db_id
        ).delete(synchronize_session=False)
        self._session.flush()

        inserted_blocks: List[NotionBlock] = []
        for block_order, block in enumerate(root_blocks):
            self._insert_block_tree(
                notion_page_db_id=notion_page_db_id,
                parent_block_db_id=None,
                block_order=block_order,
                block=block,
                inserted_blocks=inserted_blocks,
            )

        self._session.flush()
        return inserted_blocks

    def _insert_block_tree(
        self,
        *,
        notion_page_db_id: int,
        parent_block_db_id: Optional[int],
        block_order: int,
        block: NotionBlockSnapshot,
        inserted_blocks: List[NotionBlock],
    ) -> None:
        notion_block = NotionBlock(
            notion_block_id=block.notion_block_id,
            notion_page_id=notion_page_db_id,
            parent_block_id=parent_block_db_id,
            block_type=block.block_type,
            content_text=block.content_text,
            block_path=block.block_path,
            block_order=block_order,
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "sqlite":
            notion_block.id = self._allocate_block_id_for_sqlite()

        self._session.add(notion_block)
        self._session.flush()
        inserted_blocks.append(notion_block)

        for child_order, child in enumerate(block.children):
            self._insert_block_tree(
                notion_page_db_id=notion_page_db_id,
                parent_block_db_id=notion_block.id,
                block_order=child_order,
                block=child,
                inserted_blocks=inserted_blocks,
            )
