from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

from src.rag import BlockPathNode, BlockPathSnapshot, build_block_paths
from src.tools.notion_reader_tool import (
    InMemoryNotionReaderClient,
    NotionBlockNode,
    NotionPageTree,
)

DEFAULT_MOCK_NOTION_DATA_DIR = (
    Path(__file__).resolve().parents[2] / "mock_data" / "notion_pages"
)


class MockNotionDataError(Exception):
    pass


class _MockPageMetadata(BaseModel):
    synthetic_content: bool
    safe_for_public_demo: bool
    contains_private_content: bool = False
    scenario: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_safety_flags(self) -> "_MockPageMetadata":
        if not self.synthetic_content:
            raise ValueError("demo_metadata.synthetic_content must be true")
        if not self.safe_for_public_demo:
            raise ValueError("demo_metadata.safe_for_public_demo must be true")
        if self.contains_private_content:
            raise ValueError("demo_metadata.contains_private_content must be false")
        return self


class _MockBlockPayload(BaseModel):
    block_id: str = Field(min_length=1)
    block_type: str = Field(min_length=1)
    content_text: str = ""
    children: List["_MockBlockPayload"] = Field(default_factory=list)


class _MockPagePayload(BaseModel):
    page_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    notion_path: str = Field(min_length=1)
    parent_notion_page_id: Optional[str] = None
    last_edited_time: Optional[datetime] = None
    demo_metadata: _MockPageMetadata
    blocks: List[_MockBlockPayload] = Field(default_factory=list)


_MockBlockPayload.model_rebuild()


class JSONMockNotionReaderClient(InMemoryNotionReaderClient):
    @classmethod
    def from_directory(cls, directory: Path | str) -> "JSONMockNotionReaderClient":
        return cls(load_mock_notion_pages(directory))


def load_mock_notion_pages(directory: Path | str) -> Dict[str, NotionPageTree]:
    base_path = Path(directory)
    if not base_path.is_dir():
        raise MockNotionDataError(
            f"Mock Notion data directory is not found: {base_path}"
        )

    json_paths = sorted(base_path.glob("*.json"))
    if not json_paths:
        raise MockNotionDataError(
            f"Mock Notion data directory does not contain any JSON pages: {base_path}"
        )

    pages: Dict[str, NotionPageTree] = {}
    for json_path in json_paths:
        page_tree = _load_mock_page_tree(json_path)
        if page_tree.page_id in pages:
            raise MockNotionDataError(
                f"Duplicate mock Notion page_id found: {page_tree.page_id}"
            )
        pages[page_tree.page_id] = page_tree

    return pages


def _load_mock_page_tree(json_path: Path) -> NotionPageTree:
    try:
        raw_payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MockNotionDataError(
            f"Mock Notion JSON is invalid: path={json_path} error={exc}"
        ) from exc

    try:
        payload = _MockPagePayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise MockNotionDataError(
            f"Mock Notion page schema is invalid: path={json_path} error={exc}"
        ) from exc

    _assert_unique_block_ids(payload)
    block_paths = build_block_paths(
        page_path=payload.notion_path,
        blocks=[_to_block_path_node(block) for block in payload.blocks],
    )
    return NotionPageTree(
        page_id=payload.page_id,
        title=payload.title,
        notion_path=payload.notion_path,
        parent_notion_page_id=payload.parent_notion_page_id,
        last_edited_time=payload.last_edited_time,
        blocks=[_to_notion_block_node(block) for block in block_paths],
    )


def _assert_unique_block_ids(payload: _MockPagePayload) -> None:
    seen_block_ids: set[str] = set()
    for block_id in _iter_block_ids(payload.blocks):
        if block_id in seen_block_ids:
            raise MockNotionDataError(
                "Duplicate block_id found in mock Notion page: "
                f"page_id={payload.page_id} block_id={block_id}"
            )
        seen_block_ids.add(block_id)


def _iter_block_ids(blocks: Iterable[_MockBlockPayload]) -> Iterable[str]:
    for block in blocks:
        yield block.block_id
        yield from _iter_block_ids(block.children)


def _to_block_path_node(block: _MockBlockPayload) -> BlockPathNode:
    return BlockPathNode(
        block_id=block.block_id,
        block_type=block.block_type,
        content_text=block.content_text,
        children=[_to_block_path_node(child) for child in block.children],
    )


def _to_notion_block_node(block: BlockPathSnapshot) -> NotionBlockNode:
    return NotionBlockNode(
        block_id=block.block_id,
        block_type=block.block_type,
        content_text=block.content_text,
        block_path=block.block_path,
        children=[_to_notion_block_node(child) for child in block.children],
    )
