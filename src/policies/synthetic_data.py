from __future__ import annotations

from typing import FrozenSet


# This is intentionally fixed in code. Cleanup must never accept arbitrary
# caller-supplied page ids that could match a real Notion page.
SYNTHETIC_NOTION_PAGE_IDS: FrozenSet[str] = frozenset(
    {
        "page-1",
        "page-2",
        "page-a",
        "page-accept-1",
        "page-accept-retry",
        "page-b",
        "page-block",
        "page-blocks",
        "page-external-201",
        "page-external-203",
        "page-external-6",
        "page-flush-only",
        "page-invalid-target",
        "page-iso-9001",
        "page-legacy-a",
        "page-legacy-b",
        "page-legacy-null",
        "page-live-vector-smoke-main",
        "page-live-vector-smoke-secondary",
        "page-lock",
        "page-manual-sync-eval",
        "page-mixed",
        "page-ml-week1",
        "page-ml-week2",
        "page-nlp-week5",
        "page-rag-basics",
        "page-rollback",
        "page-stale",
        "page-sync",
        "page-telegram-accept",
        "page-telegram-flow",
        "page-write-safety",
        "synthetic-page",
    }
)


def is_known_synthetic_notion_page_id(page_id: str) -> bool:
    return page_id.strip() in SYNTHETIC_NOTION_PAGE_IDS
