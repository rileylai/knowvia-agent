from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.tools.base import Tool
from src.tools.models import ToolContext, ToolResult, ToolSpec
from src.services.infrastructure_errors import InfrastructureExecutionTimeout


@dataclass
class NotionBlockNode:
    block_id: str
    block_type: str
    content_text: str
    block_path: str
    children: List["NotionBlockNode"] = field(default_factory=list)


@dataclass
class NotionPageTree:
    page_id: str
    title: str
    notion_path: str
    blocks: List[NotionBlockNode] = field(default_factory=list)
    last_edited_time: Optional[datetime] = None
    parent_notion_page_id: Optional[str] = None


@dataclass(frozen=True)
class NotionPageSummary:
    page_id: str
    title: str
    last_edited_time: Optional[datetime] = None
    parent_notion_page_id: Optional[str] = None


class NotionReaderClient:
    def fetch_page_tree(self, page_id: str) -> Optional[NotionPageTree]:
        raise NotImplementedError

    def list_pages(self) -> List[NotionPageSummary]:
        raise NotImplementedError


class NotionReaderClientError(Exception):
    """Safe, deterministic error raised by a Notion reader client."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InMemoryNotionReaderClient(NotionReaderClient):
    def __init__(self, pages: Dict[str, NotionPageTree]) -> None:
        self._pages = pages

    def fetch_page_tree(self, page_id: str) -> Optional[NotionPageTree]:
        return self._pages.get(page_id)

    def list_pages(self) -> List[NotionPageSummary]:
        return [
            NotionPageSummary(
                page_id=page.page_id,
                title=page.title,
                parent_notion_page_id=page.parent_notion_page_id,
                last_edited_time=page.last_edited_time,
            )
            for page in sorted(self._pages.values(), key=lambda item: item.page_id)
        ]


class NotionReaderTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="notion_reader",
            description="Read one Notion page as a read-only block tree with paths.",
            input_schema={
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["list_pages"]},
                },
            },
            output_schema={
                "type": "object",
                "required": ["page", "blocks", "printable_tree"],
                "properties": {
                    "page": {
                        "type": "object",
                        "properties": {
                            "page_id": {"type": "string"},
                            "title": {"type": "string"},
                            "notion_path": {"type": "string"},
                            "parent_notion_page_id": {
                                "type": ["string", "null"],
                            },
                            "last_edited_time": {
                                "type": ["string", "null"],
                            },
                        },
                    },
                    "blocks": {"type": "array"},
                    "printable_tree": {"type": "string"},
                    "pages": {"type": "array"},
                },
            },
        )

    def __init__(self, notion_reader_client: NotionReaderClient) -> None:
        self._notion_reader_client = notion_reader_client

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        _ = context
        action = str(arguments.get("action", "")).strip().lower()
        if action == "list_pages":
            return self._run_list_pages()

        page_id = str(arguments.get("page_id", "")).strip()
        if not page_id:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="page_id is required",
            )

        try:
            page_tree = self._notion_reader_client.fetch_page_tree(page_id)
        except NotionReaderClientError as exc:
            return ToolResult.failure(code=exc.code, message=exc.message)
        except InfrastructureExecutionTimeout:
            raise
        except Exception as exc:
            _ = exc
            return ToolResult.failure(
                code="NOTION_BLOCK_FETCH_FAILED",
                message="Failed to fetch Notion block tree",
            )

        if page_tree is None:
            return ToolResult.failure(
                code="NOTION_PAGE_NOT_FOUND",
                message=f"Notion page is not found: page_id={page_id}",
            )

        printable_tree = self._render_printable_tree(page_tree)
        return ToolResult.success(
            content=printable_tree,
            structured_content={
                "page": {
                    "page_id": page_tree.page_id,
                    "title": page_tree.title,
                    "notion_path": page_tree.notion_path,
                    "parent_notion_page_id": page_tree.parent_notion_page_id,
                    "last_edited_time": (
                        page_tree.last_edited_time.isoformat()
                        if page_tree.last_edited_time is not None
                        else None
                    ),
                },
                "blocks": [self._block_to_dict(block) for block in page_tree.blocks],
                "printable_tree": printable_tree,
            },
        )

    def _run_list_pages(self) -> ToolResult:
        try:
            pages = self._notion_reader_client.list_pages()
        except NotionReaderClientError as exc:
            return ToolResult.failure(code=exc.code, message=exc.message)
        except InfrastructureExecutionTimeout:
            raise
        except Exception as exc:
            _ = exc
            return ToolResult.failure(
                code="NOTION_BLOCK_FETCH_FAILED",
                message="Failed to discover Notion pages",
            )

        page_payloads = [
            {
                "page_id": page.page_id,
                "title": page.title,
                "parent_notion_page_id": page.parent_notion_page_id,
                "last_edited_time": (
                    page.last_edited_time.isoformat()
                    if page.last_edited_time is not None
                    else None
                ),
            }
            for page in pages
        ]
        return ToolResult.success(
            content=f"Discovered {len(page_payloads)} Notion pages",
            structured_content={"pages": page_payloads},
        )

    def _render_printable_tree(self, page_tree: NotionPageTree) -> str:
        lines = [
            f"Page: {page_tree.title} ({page_tree.page_id})",
            f"Path: {page_tree.notion_path}",
        ]
        for block in page_tree.blocks:
            self._append_block_lines(lines=lines, block=block, level=0)
        return "\n".join(lines)

    def _append_block_lines(
        self, *, lines: List[str], block: NotionBlockNode, level: int
    ) -> None:
        indent = "  " * level
        lines.append(
            f"{indent}- {block.block_type} [{block.block_id}] {block.content_text} | path={block.block_path}"
        )
        for child in block.children:
            self._append_block_lines(lines=lines, block=child, level=level + 1)

    def _block_to_dict(self, block: NotionBlockNode) -> Dict[str, Any]:
        return {
            "block_id": block.block_id,
            "block_type": block.block_type,
            "content_text": block.content_text,
            "block_path": block.block_path,
            "children": [self._block_to_dict(child) for child in block.children],
        }
