from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


TELEGRAM_MESSAGE_MAX_LENGTH = 4096
# Deprecated compatibility constant. The picker no longer uses a page size or
# emits pagination controls; all direct children render in one view.
TELEGRAM_PICKER_PAGE_SIZE = 6
TELEGRAM_PICKER_TITLE_MAX_LENGTH = 56


@dataclass(frozen=True)
class NotionHierarchyPage:
    page_id: str
    title: str
    notion_path: str
    parent_page_id: Optional[str] = None


@dataclass(frozen=True)
class NotionHierarchyNode:
    page: NotionHierarchyPage
    children: tuple["NotionHierarchyNode", ...] = ()

    @property
    def has_children(self) -> bool:
        return bool(self.children)


class NotionPageHierarchy:
    """Build a safe, deterministic page tree from canonical page identities."""

    def __init__(
        self,
        *,
        roots: tuple[NotionHierarchyNode, ...],
        nodes_by_id: dict[str, NotionHierarchyNode],
        effective_parent_by_id: dict[str, Optional[str]],
    ) -> None:
        self.roots = roots
        self._nodes_by_id = nodes_by_id
        self._effective_parent_by_id = effective_parent_by_id

    @classmethod
    def from_pages(cls, pages: Iterable[NotionHierarchyPage]) -> "NotionPageHierarchy":
        unique_pages: dict[str, NotionHierarchyPage] = {}
        for page in pages:
            page_id = str(page.page_id).strip()
            if not page_id:
                continue
            candidate = NotionHierarchyPage(
                page_id=page_id,
                title=str(page.title).strip() or "Untitled Notion Page",
                notion_path=str(page.notion_path).strip(),
                parent_page_id=(
                    str(page.parent_page_id).strip()
                    if page.parent_page_id is not None
                    and str(page.parent_page_id).strip()
                    else None
                ),
            )
            previous = unique_pages.get(page_id)
            if previous is None or cls._page_sort_key(candidate) < cls._page_sort_key(previous):
                unique_pages[page_id] = candidate

        parent_by_id: dict[str, Optional[str]] = {}
        for page_id, page in unique_pages.items():
            parent_id = page.parent_page_id
            if parent_id not in unique_pages or parent_id == page_id:
                parent_id = None
            parent_by_id[page_id] = parent_id

        cycle_members = cls._find_cycle_members(parent_by_id)
        effective_parent_by_id = {
            page_id: None if page_id in cycle_members else parent_id
            for page_id, parent_id in parent_by_id.items()
        }

        children_by_id: dict[str, list[NotionHierarchyPage]] = {
            page_id: [] for page_id in unique_pages
        }
        root_pages: list[NotionHierarchyPage] = []
        for page_id, page in unique_pages.items():
            parent_id = effective_parent_by_id[page_id]
            if parent_id is None:
                root_pages.append(page)
            else:
                children_by_id[parent_id].append(page)

        def build_node(page: NotionHierarchyPage) -> NotionHierarchyNode:
            children = tuple(
                build_node(child)
                for child in sorted(
                    children_by_id[page.page_id], key=cls._page_sort_key
                )
            )
            return NotionHierarchyNode(page=page, children=children)

        roots = tuple(build_node(page) for page in sorted(root_pages, key=cls._page_sort_key))
        nodes_by_id: dict[str, NotionHierarchyNode] = {}

        def index_nodes(node: NotionHierarchyNode) -> None:
            nodes_by_id[node.page.page_id] = node
            for child in node.children:
                index_nodes(child)

        for root in roots:
            index_nodes(root)
        return cls(
            roots=roots,
            nodes_by_id=nodes_by_id,
            effective_parent_by_id=effective_parent_by_id,
        )

    @staticmethod
    def _page_sort_key(page: NotionHierarchyPage) -> tuple[str, str, str, str]:
        return (
            page.notion_path.casefold(),
            page.title.casefold(),
            page.title,
            page.page_id,
        )

    @staticmethod
    def _find_cycle_members(
        parent_by_id: dict[str, Optional[str]],
    ) -> set[str]:
        cycle_members: set[str] = set()
        visited: set[str] = set()
        for start in sorted(parent_by_id):
            path: list[str] = []
            position: dict[str, int] = {}
            current: Optional[str] = start
            while current is not None and current not in visited:
                if current in position:
                    cycle_members.update(path[position[current] :])
                    break
                position[current] = len(path)
                path.append(current)
                current = parent_by_id.get(current)
            visited.update(path)
        return cycle_members

    def get_node(self, page_id: str) -> Optional[NotionHierarchyNode]:
        return self._nodes_by_id.get(str(page_id).strip())

    def parent_id(self, page_id: str) -> Optional[str]:
        return self._effective_parent_by_id.get(str(page_id).strip())

    def breadcrumb(self, page_id: str) -> str:
        node = self.get_node(page_id)
        if node is None:
            return ""
        parts: list[str] = []
        title_counts: dict[str, int] = {}
        for candidate in self._nodes_by_id.values():
            title_counts[candidate.page.title] = title_counts.get(candidate.page.title, 0) + 1
        current_id: Optional[str] = node.page.page_id
        seen: set[str] = set()
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            current = self.get_node(current_id)
            if current is None:
                break
            title = current.page.title
            if title_counts.get(title, 0) > 1:
                title = f"{title} ({current.page.page_id})"
            parts.append(title)
            current_id = self.parent_id(current_id)
        return " › ".join(reversed(parts))

    def format_tree_messages(
        self,
        *,
        max_message_length: int = TELEGRAM_MESSAGE_MAX_LENGTH,
    ) -> list[str]:
        if max_message_length < 256:
            raise ValueError("max_message_length must be at least 256")
        if not self.roots:
            return ["📚 Available Notion pages\n\nNo indexed Notion pages are available."]

        lines = ["📚 Available Notion pages", ""]

        def append_lines(
            node: NotionHierarchyNode,
            number: str,
            indent: str,
            connector: str,
        ) -> None:
            lines.append(
                self._format_page_line(
                    number=number,
                    prefix=f"{indent}{connector}",
                    page=node.page,
                    max_message_length=max_message_length,
                )
            )
            for child_index, child in enumerate(node.children, start=1):
                branch = "└─" if child_index == len(node.children) else "├─"
                append_lines(
                    child,
                    f"{number}.{child_index}",
                    f"{indent}   ",
                    f"{branch} ",
                )

        for root_index, root in enumerate(self.roots, start=1):
            append_lines(root, str(root_index), "", "")

        lines.append("")
        lines.append(
            "Upload a PDF or image to choose a target page; parent and child pages are independent targets."
        )
        return self._split_messages(lines, max_message_length=max_message_length)

    @staticmethod
    def _format_page_line(
        *,
        number: str,
        prefix: str,
        page: NotionHierarchyPage,
        max_message_length: int,
    ) -> str:
        identity = f"({page.page_id})"
        display_number = f"{number}." if "." not in number else number
        fixed = f"{prefix}{display_number} "
        available = max(8, max_message_length - len(fixed) - len(identity) - 1)
        title = _truncate(page.title, min(512, available))
        return f"{fixed}{title} {identity}"

    @staticmethod
    def _split_messages(lines: list[str], *, max_message_length: int) -> list[str]:
        body_limit = max(128, max_message_length - 80)
        chunks: list[list[str]] = [[]]
        for line in lines:
            if len(line) > body_limit:
                line = _truncate(line, body_limit)
            current = chunks[-1]
            proposed = "\n".join(current + [line])
            if current and len(proposed) > body_limit:
                chunks.append([line])
            else:
                current.append(line)
        total = len(chunks)
        rendered: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            if total == 1:
                rendered.append("\n".join(chunk))
            else:
                heading = (
                    "📚 Available Notion pages"
                    if index == 1
                    else f"📚 Available Notion pages (continued {index}/{total})"
                )
                body = "\n".join(chunk)
                rendered.append(f"{heading}\n\n{body}")
        return rendered


@dataclass(frozen=True)
class HierarchyPickerButton:
    action: str
    label: str
    page_id: Optional[str] = None
    target_notion_page_id: Optional[str] = None
    target_notion_path: Optional[str] = None
    navigation_page_id: Optional[str] = None
    navigation_page_number: int = 1


@dataclass(frozen=True)
class HierarchyPickerView:
    text: str
    current_page_id: Optional[str]
    page_number: int
    total_pages: int
    buttons: tuple[HierarchyPickerButton, ...]


class NotionHierarchyPicker:
    """Render the same progressive picker for upload and Change Target flows."""

    def __init__(
        self,
        hierarchy: NotionPageHierarchy,
        *,
        page_size: Optional[int] = None,
        max_message_length: int = TELEGRAM_MESSAGE_MAX_LENGTH,
    ) -> None:
        if page_size is not None and page_size <= 0:
            raise ValueError("page_size must be positive")
        self._hierarchy = hierarchy
        self._max_message_length = max_message_length

    def render(
        self,
        *,
        mode: str,
        current_page_id: Optional[str] = None,
        page_number: int = 1,
    ) -> HierarchyPickerView:
        # ``page_number`` remains accepted so callbacks issued by the previous
        # paginated UI can be normalized safely until their TTL expires. The
        # current picker always renders the complete direct-child list.
        _ = page_number
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"upload", "change_target"}:
            raise ValueError("mode must be upload or change_target")
        if current_page_id is not None and self._hierarchy.get_node(current_page_id) is None:
            raise KeyError("current picker page does not exist")

        current_node = (
            self._hierarchy.get_node(current_page_id)
            if current_page_id is not None
            else None
        )
        candidates = list(
            current_node.children if current_node is not None else self._hierarchy.roots
        )
        buttons: list[HierarchyPickerButton] = []

        if current_node is not None:
            buttons.append(
                HierarchyPickerButton(
                    action="select_target",
                    label=f"✅ Select {_truncate(current_node.page.title, TELEGRAM_PICKER_TITLE_MAX_LENGTH)}",
                    target_notion_page_id=current_node.page.page_id,
                    target_notion_path=current_node.page.notion_path,
                )
            )

        for node in candidates:
            if node.has_children:
                buttons.append(
                    HierarchyPickerButton(
                        action="open_page",
                        label=f"📁 {_truncate(node.page.title, TELEGRAM_PICKER_TITLE_MAX_LENGTH)}",
                        page_id=node.page.page_id,
                    )
                )
            else:
                buttons.append(
                    HierarchyPickerButton(
                        action="select_target",
                        label=f"📄 {_truncate(node.page.title, TELEGRAM_PICKER_TITLE_MAX_LENGTH)}",
                        target_notion_page_id=node.page.page_id,
                        target_notion_path=node.page.notion_path,
                    )
                )

        if current_node is not None:
            buttons.append(
                HierarchyPickerButton(
                    action="back",
                    label="⬅️ Back",
                    navigation_page_id=current_node.page.page_id,
                )
            )
            buttons.append(HierarchyPickerButton(action="root", label="🏠 Root pages"))

        if current_node is None:
            text = (
                "File received. Choose a Notion target page."
                if normalized_mode == "upload"
                else "Choose a new target page for this pending proposal."
            )
        else:
            text = (
                f"📁 {self._hierarchy.breadcrumb(current_node.page.page_id)}\n"
                "Choose this page or one of its child pages."
            )
        return HierarchyPickerView(
            text=_truncate(text, self._max_message_length),
            current_page_id=current_page_id,
            page_number=1,
            total_pages=1,
            buttons=tuple(buttons),
        )


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    if max_length <= 1:
        return value[:max_length]
    return value[: max_length - 1].rstrip() + "…"
