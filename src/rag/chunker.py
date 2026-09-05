from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


SECTION_BOUNDARY_BLOCK_TYPES: Set[str] = {
    "heading_1",
    "heading_2",
    "heading_3",
    "toggle",
    "child_page",
}


@dataclass
class ChunkerBlock:
    notion_block_id: str
    block_type: str
    content_text: str
    block_path: str
    children: List["ChunkerBlock"] = field(default_factory=list)


@dataclass
class ChunkerPage:
    notion_page_id: str
    title: str
    notion_path: str
    blocks: List[ChunkerBlock] = field(default_factory=list)


@dataclass
class NotionChunkDraft:
    source_kind: str
    chunk_index: int
    chunk_text: str
    notion_path: str
    citation_meta: Dict[str, Any]


@dataclass
class TextChunkDraft:
    source_kind: str
    chunk_index: int
    chunk_text: str
    locator: str
    citation_meta: Dict[str, Any]
    page_number: Optional[int] = None


@dataclass
class _ChunkAccumulator:
    notion_path: str
    lines: List[str] = field(default_factory=list)
    block_ids: List[str] = field(default_factory=list)


def chunk_notion_page(
    page: ChunkerPage,
    *,
    max_chunk_chars: int = 1200,
) -> List[NotionChunkDraft]:
    if max_chunk_chars <= 0:
        raise ValueError("max_chunk_chars must be positive")

    chunks: List[NotionChunkDraft] = []
    current: Optional[_ChunkAccumulator] = None

    for block in _flatten_blocks(page.blocks):
        block_text = _normalize_text(block.content_text)
        block_path = _normalize_path(block.block_path) or _normalize_path(page.notion_path)
        is_boundary = _is_boundary_block(block.block_type)

        if is_boundary:
            _flush_chunk(chunks=chunks, current=current, page=page)
            current = _ChunkAccumulator(notion_path=block_path)

        if current is None:
            current = _ChunkAccumulator(notion_path=block_path)

        if block_text:
            projected_length = len("\n".join(current.lines + [block_text]))
            if current.lines and projected_length > max_chunk_chars:
                _flush_chunk(chunks=chunks, current=current, page=page)
                current = _ChunkAccumulator(notion_path=current.notion_path)
            current.lines.append(block_text)

        current.block_ids.append(block.notion_block_id)

    _flush_chunk(chunks=chunks, current=current, page=page)
    return chunks


def chunk_text_document(
    text: str,
    *,
    source_kind: str,
    source_display_name: str,
    pages: Optional[List[str]] = None,
    page_labels: Optional[List[str]] = None,
    max_chunk_chars: int = 1200,
) -> List[TextChunkDraft]:
    """Build bounded, deterministic chunks for any normalized text source."""
    if max_chunk_chars <= 0:
        raise ValueError("max_chunk_chars must be positive")
    if not source_kind.strip():
        raise ValueError("source_kind must not be empty")
    if not source_display_name.strip():
        raise ValueError("source_display_name must not be empty")

    page_values = list(pages or [])
    page_label_values = list(page_labels or [])
    if page_label_values and len(page_label_values) != len(page_values):
        raise ValueError("page_labels must match pages length")
    chunks: List[TextChunkDraft] = []
    if page_values:
        for page_number, page_text in enumerate(page_values, start=1):
            normalized_page = _normalize_text(page_text)
            page_label = page_label_values[page_number - 1] if page_label_values else None
            for local_chunk_index, piece in enumerate(
                _split_bounded_text(normalized_page, max_chunk_chars),
                start=1,
            ):
                locator = (
                    f"{page_label} · chunk {local_chunk_index}"
                    if page_label
                    else f"page {page_number}"
                )
                chunks.append(
                    TextChunkDraft(
                        source_kind=source_kind.strip().lower(),
                        chunk_index=len(chunks),
                        chunk_text=piece,
                        locator=locator,
                        citation_meta={
                            "source_kind": source_kind.strip().lower(),
                            "source_display_name": source_display_name.strip(),
                            "locator": locator,
                        },
                        page_number=page_number,
                    )
                )
    else:
        normalized_text = _normalize_text(text)
        for piece in _split_bounded_text(normalized_text, max_chunk_chars):
            locator = f"chunk {len(chunks) + 1}"
            chunks.append(
                TextChunkDraft(
                    source_kind=source_kind.strip().lower(),
                    chunk_index=len(chunks),
                    chunk_text=piece,
                    locator=locator,
                    citation_meta={
                        "source_kind": source_kind.strip().lower(),
                        "source_display_name": source_display_name.strip(),
                        "locator": locator,
                    },
                )
            )
    return chunks


def _flatten_blocks(blocks: List[ChunkerBlock]) -> List[ChunkerBlock]:
    flattened: List[ChunkerBlock] = []
    for block in blocks:
        flattened.append(block)
        flattened.extend(_flatten_blocks(block.children))
    return flattened


def _flush_chunk(
    *,
    chunks: List[NotionChunkDraft],
    current: Optional[_ChunkAccumulator],
    page: ChunkerPage,
) -> None:
    if current is None:
        return

    chunk_text = "\n".join(line for line in current.lines if line).strip()
    if not chunk_text:
        return

    chunk_index = len(chunks)
    chunks.append(
        NotionChunkDraft(
            source_kind="notion",
            chunk_index=chunk_index,
            chunk_text=chunk_text,
            notion_path=current.notion_path,
            citation_meta={
                "notion_page_id": page.notion_page_id,
                "notion_page_title": page.title,
                "notion_page_path": _normalize_path(page.notion_path),
                "notion_block_ids": list(dict.fromkeys(current.block_ids)),
            },
        )
    )


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _split_bounded_text(text: str, max_chunk_chars: int) -> List[str]:
    normalized_text = text.strip()
    if not normalized_text:
        return []
    if len(normalized_text) <= max_chunk_chars:
        return [normalized_text]

    words = normalized_text.split(" ")
    pieces: List[str] = []
    current: List[str] = []
    current_length = 0
    for word in words:
        if current and current_length + 1 + len(word) > max_chunk_chars:
            pieces.append(" ".join(current))
            current = []
            current_length = 0
        if len(word) > max_chunk_chars:
            if current:
                pieces.append(" ".join(current))
                current = []
                current_length = 0
            for start in range(0, len(word), max_chunk_chars):
                pieces.append(word[start : start + max_chunk_chars])
            continue
        current.append(word)
        current_length += len(word) if len(current) == 1 else 1 + len(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _normalize_path(path: str) -> str:
    segments = [segment.strip() for segment in path.strip().split("/") if segment.strip()]
    return "/".join(segments)


def _is_boundary_block(block_type: str) -> bool:
    return block_type.strip().lower() in SECTION_BOUNDARY_BLOCK_TYPES
