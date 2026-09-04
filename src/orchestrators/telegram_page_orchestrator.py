from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from src.repositories import NotionPageRepository
from src.services.notion_hierarchy import (
    HierarchyPickerView,
    NotionHierarchyPage,
    NotionPageHierarchy,
    NotionHierarchyPicker,
)


@dataclass
class TelegramPageItem:
    page_id: str
    title: str
    notion_path: str
    parent_notion_page_id: Optional[str] = None
    breadcrumb: str = ""
    has_children: bool = False


@dataclass
class TelegramPagesResult:
    reply_text: str
    pages: List[TelegramPageItem]
    reply_texts: List[str] = field(default_factory=list)


class TelegramPageOrchestrator:
    def __init__(self, *, notion_page_repository: NotionPageRepository) -> None:
        self._notion_page_repository = notion_page_repository

    def build_hierarchy(self) -> NotionPageHierarchy:
        return NotionPageHierarchy.from_pages(
            NotionHierarchyPage(
                page_id=page.notion_page_id,
                title=page.title,
                notion_path=page.notion_path,
                parent_page_id=page.parent_notion_page_id,
            )
            for page in self._notion_page_repository.list_pages(limit=None)
        )

    def list_pages(self, *, limit: Optional[int] = None) -> TelegramPagesResult:
        hierarchy = self.build_hierarchy()
        flattened: list[TelegramPageItem] = []

        def visit(node) -> None:
            flattened.append(
                TelegramPageItem(
                    page_id=node.page.page_id,
                    title=node.page.title,
                    notion_path=node.page.notion_path,
                    parent_notion_page_id=hierarchy.parent_id(node.page.page_id),
                    breadcrumb=hierarchy.breadcrumb(node.page.page_id),
                    has_children=node.has_children,
                )
            )
            for child in node.children:
                visit(child)

        for root in hierarchy.roots:
            visit(root)
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit must be positive")
            flattened = flattened[:limit]
        messages = hierarchy.format_tree_messages()
        if not flattened:
            return TelegramPagesResult(
                reply_text=messages[0],
                pages=[],
                reply_texts=messages,
            )
        return TelegramPagesResult(
            reply_text=messages[0],
            pages=flattened,
            reply_texts=messages,
        )

    def build_picker_view(
        self,
        *,
        mode: str,
        current_page_id: Optional[str] = None,
        page_number: int = 1,
    ) -> HierarchyPickerView:
        hierarchy = self.build_hierarchy()
        return NotionHierarchyPicker(hierarchy).render(
            mode=mode,
            current_page_id=current_page_id,
            page_number=page_number,
        )

    def get_page(self, page_id: str) -> Optional[TelegramPageItem]:
        for page in self.list_pages().pages:
            if page.page_id == page_id:
                return page
        return None
