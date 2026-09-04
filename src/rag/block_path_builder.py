from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class BlockPathNode:
    block_id: str
    block_type: str
    content_text: str
    children: List["BlockPathNode"] = field(default_factory=list)


@dataclass
class BlockPathSnapshot:
    block_id: str
    block_type: str
    content_text: str
    block_path: str
    children: List["BlockPathSnapshot"] = field(default_factory=list)


def build_block_paths(
    *,
    page_path: str,
    blocks: List[BlockPathNode],
) -> List[BlockPathSnapshot]:
    normalized_page_path = _normalize_page_path(page_path)
    return [
        _build_snapshot(block=block, parent_path=normalized_page_path)
        for block in blocks
    ]


def _build_snapshot(
    *,
    block: BlockPathNode,
    parent_path: str,
) -> BlockPathSnapshot:
    segment = _normalize_segment(block.content_text)
    current_path = parent_path
    if segment:
        current_path = _join_path(parent_path, segment)

    child_snapshots = [
        _build_snapshot(block=child, parent_path=current_path)
        for child in block.children
    ]
    return BlockPathSnapshot(
        block_id=block.block_id,
        block_type=block.block_type,
        content_text=block.content_text,
        block_path=current_path,
        children=child_snapshots,
    )


def _normalize_page_path(page_path: str) -> str:
    parts = [part.strip() for part in page_path.strip().split("/") if part.strip()]
    return "/".join(parts)


def _normalize_segment(content_text: str) -> str:
    return " ".join(content_text.split())


def _join_path(base_path: str, segment: str) -> str:
    if not base_path:
        return segment
    return f"{base_path}/{segment}"
