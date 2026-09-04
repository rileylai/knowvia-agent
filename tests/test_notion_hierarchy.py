from __future__ import annotations

from src.services import (
    InMemoryTelegramSessionStore,
    NotionHierarchyPage,
    NotionHierarchyPicker,
    NotionPageHierarchy,
)


def _page(
    page_id: str,
    title: str,
    path: str,
    parent: str | None = None,
) -> NotionHierarchyPage:
    return NotionHierarchyPage(
        page_id=page_id,
        title=title,
        notion_path=path,
        parent_page_id=parent,
    )


def test_hierarchy_builds_roots_and_nested_depth_three() -> None:
    hierarchy = NotionPageHierarchy.from_pages(
        [
            _page("root", "Root", "Knowledge/Root"),
            _page("child", "Child", "Knowledge/Root/Child", "root"),
            _page("grandchild", "Grandchild", "Knowledge/Root/Child/Grandchild", "child"),
        ]
    )

    assert [node.page.page_id for node in hierarchy.roots] == ["root"]
    assert hierarchy.roots[0].children[0].children[0].page.page_id == "grandchild"
    assert hierarchy.breadcrumb("grandchild") == "Root › Child › Grandchild"
    tree_text = "\n".join(hierarchy.format_tree_messages())
    assert "1. Root (root)" in tree_text
    assert "   └─ 1.1 Child (child)" in tree_text
    assert "      └─ 1.1.1 Grandchild (grandchild)" in tree_text


def test_pages_tree_omits_paths_but_keeps_titles_numbers_and_ids() -> None:
    hierarchy = NotionPageHierarchy.from_pages(
        [
            _page("root", "Root", "Knowledge/Root"),
            _page("child-a", "Shared", "Knowledge/Root/A", "root"),
            _page("child-b", "Shared", "Knowledge/Root/B", "root"),
        ]
    )

    tree_text = "\n".join(hierarchy.format_tree_messages())

    assert "Knowledge/" not in tree_text
    assert "1. Root (root)" in tree_text
    assert "   ├─ 1.1 Shared (child-a)" in tree_text
    assert "   └─ 1.2 Shared (child-b)" in tree_text


def test_parent_is_selectable_and_leaf_is_a_direct_select_button() -> None:
    hierarchy = NotionPageHierarchy.from_pages(
        [
            _page("parent", "Parent", "Knowledge/Parent"),
            _page("leaf", "Leaf", "Knowledge/Parent/Leaf", "parent"),
        ]
    )

    parent_view = NotionHierarchyPicker(hierarchy).render(
        mode="upload", current_page_id="parent"
    )
    assert parent_view.buttons[0].action == "select_target"
    assert parent_view.buttons[0].target_notion_page_id == "parent"
    assert parent_view.buttons[1].action == "select_target"
    assert parent_view.buttons[1].target_notion_page_id == "leaf"


def test_duplicate_titles_remain_distinct_by_canonical_id_and_breadcrumb() -> None:
    hierarchy = NotionPageHierarchy.from_pages(
        [
            _page("a", "Shared", "Knowledge/A"),
            _page("b", "Shared", "Knowledge/B"),
            _page("a-child", "Same", "Knowledge/A/Same", "a"),
            _page("b-child", "Same", "Knowledge/B/Same", "b"),
        ]
    )

    assert [root.page.page_id for root in hierarchy.roots] == ["a", "b"]
    assert hierarchy.breadcrumb("a-child") != hierarchy.breadcrumb("b-child")
    assert {
        button.target_notion_page_id
        for button in NotionHierarchyPicker(hierarchy).render(
            mode="upload", current_page_id="a"
        ).buttons
        if button.target_notion_page_id
    } == {"a", "a-child"}


def test_missing_parent_and_cycle_are_safe_roots_without_recursion_loop() -> None:
    hierarchy = NotionPageHierarchy.from_pages(
        [
            _page("orphan", "Orphan", "Knowledge/Orphan", "missing"),
            _page("cycle-a", "Cycle A", "Knowledge/Cycle A", "cycle-b"),
            _page("cycle-b", "Cycle B", "Knowledge/Cycle B", "cycle-a"),
            _page("cycle-child", "Cycle Child", "Knowledge/Cycle B/Child", "cycle-b"),
        ]
    )

    assert [node.page.page_id for node in hierarchy.roots] == [
        "cycle-a",
        "cycle-b",
        "orphan",
    ]
    assert hierarchy.parent_id("cycle-a") is None
    assert hierarchy.parent_id("cycle-b") is None
    assert hierarchy.parent_id("cycle-child") == "cycle-b"


def test_stable_ordering_and_long_title_truncation_are_deterministic() -> None:
    hierarchy = NotionPageHierarchy.from_pages(
        [
            _page("z", "Zeta", "Knowledge/Zeta"),
            _page("a", "Alpha", "Knowledge/Alpha"),
            _page("long", "L" * 200, "Knowledge/Long"),
        ]
    )

    assert [node.page.page_id for node in hierarchy.roots] == ["a", "long", "z"]
    view = NotionHierarchyPicker(hierarchy).render(mode="upload")
    page_labels = [button.label for button in view.buttons]
    assert any(label.endswith("…") for label in page_labels)
    assert view.total_pages == 1
    assert all(
        button.action not in {"next_page", "previous_page"}
        for button in view.buttons
    )


def test_picker_renders_all_direct_children_with_back_and_root() -> None:
    pages = [
        _page(f"page-{index}", f"Page {index}", f"Knowledge/Page {index}")
        for index in range(8)
    ]
    pages.append(_page("nested", "Nested", "Knowledge/Page 0/Nested", "page-0"))
    hierarchy = NotionPageHierarchy.from_pages(pages)
    picker = NotionHierarchyPicker(hierarchy)

    root = picker.render(mode="upload")
    assert root.total_pages == 1
    assert len(root.buttons) == 8
    assert all(
        button.action not in {"next_page", "previous_page"}
        for button in root.buttons
    )
    nested = picker.render(mode="upload", current_page_id="page-0")
    assert nested.buttons[0].action == "select_target"
    assert nested.total_pages == 1
    assert [button.action for button in nested.buttons] == [
        "select_target",
        "select_target",
        "back",
        "root",
    ]
    assert "Page 1/" not in nested.text
    legacy_page_view = picker.render(
        mode="upload",
        current_page_id="page-0",
        page_number=2,
    )
    assert legacy_page_view.total_pages == 1
    assert [button.action for button in legacy_page_view.buttons] == [
        "select_target",
        "select_target",
        "back",
        "root",
    ]


def test_parent_picker_renders_every_direct_child_without_pagination() -> None:
    pages = [_page("parent", "Parent", "Knowledge/Parent")]
    pages.extend(
        _page(
            f"child-{index}",
            f"Child {index}",
            f"Knowledge/Parent/Child {index}",
            "parent",
        )
        for index in range(8)
    )
    picker = NotionHierarchyPicker(NotionPageHierarchy.from_pages(pages))

    view = picker.render(mode="upload", current_page_id="parent")

    assert view.total_pages == 1
    assert [button.action for button in view.buttons] == [
        "select_target",
        *(["select_target"] * 8),
        "back",
        "root",
    ]
    assert {
        button.target_notion_page_id
        for button in view.buttons
        if button.target_notion_page_id is not None
    } == {"parent", *(f"child-{index}" for index in range(8))}
    assert all(
        button.action not in {"next_page", "previous_page"}
        for button in view.buttons
    )


def test_pages_tree_messages_are_bounded_and_preserve_hierarchy_context() -> None:
    hierarchy = NotionPageHierarchy.from_pages(
        [
            _page(f"page-{index}", f"Page {index}", f"Knowledge/Page {index}")
            for index in range(30)
        ]
    )

    messages = hierarchy.format_tree_messages(max_message_length=256)

    assert messages
    assert all(len(message) <= 256 for message in messages)
    assert "continued" in messages[-1]
    assert "(page-0)" in "\n".join(messages)


def test_navigation_callback_is_opaque_and_ownership_scoped() -> None:
    store = InMemoryTelegramSessionStore()
    token = store.create_callback(
        session_id="upload-1",
        chat_id="chat-1",
        user_id="user-1",
        action="open_page",
        callback_kind="picker",
        picker_mode="upload",
        navigation_page_id="page/with/path",
        navigation_page_number=2,
    )

    assert token
    assert "page/with/path" not in f"ll:{token}"
    resolved = store.resolve_callback(
        token=token,
        chat_id="chat-1",
        user_id="user-1",
    )
    assert resolved is not None
    assert resolved.action == "open_page"
    assert resolved.navigation_page_id == "page/with/path"
    assert resolved.navigation_page_number == 2
    assert (
        store.resolve_callback(
            token=token,
            chat_id="chat-1",
            user_id="user-2",
        )
        is None
    )
