from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.db.models import NotionPage


class NotionPageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _allocate_page_id_for_sqlite(self) -> int:
        max_id = self._session.query(func.max(NotionPage.id)).scalar()
        return int(max_id or 0) + 1

    def get_by_notion_page_id(self, notion_page_id: str) -> Optional[NotionPage]:
        return (
            self._session.query(NotionPage)
            .filter(NotionPage.notion_page_id == notion_page_id)
            .one_or_none()
        )

    def get_by_id(self, page_db_id: int) -> Optional[NotionPage]:
        return self._session.get(NotionPage, page_db_id)

    def list_pages(self, *, limit: Optional[int] = None) -> List[NotionPage]:
        query = self._session.query(NotionPage).order_by(
            NotionPage.title.asc(), NotionPage.notion_page_id.asc()
        )
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit must be positive")
            query = query.limit(limit)
        return list(query.all())

    def lock_page_for_reindex(self, notion_page_id: str) -> None:
        """Serialize same-page writers for the lifetime of the current transaction."""
        bind = self._session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return

        self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:notion_page_id, 0))"
            ),
            {"notion_page_id": notion_page_id},
        )

    @staticmethod
    def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def upsert_page_snapshot(
        self,
        *,
        notion_page_id: str,
        title: str,
        notion_path: str,
        parent_notion_page_id: Optional[str] = None,
        last_edited_time: Optional[datetime] = None,
    ) -> NotionPage:
        self.lock_page_for_reindex(notion_page_id)
        page = self.get_by_notion_page_id(notion_page_id)
        incoming_edited_time = self._as_utc(last_edited_time)
        if page is None:
            page = NotionPage(
                notion_page_id=notion_page_id,
                title=title,
                notion_path=notion_path,
                parent_notion_page_id=parent_notion_page_id,
                last_edited_time=incoming_edited_time,
            )
            if self._session.bind is not None and self._session.bind.dialect.name == "sqlite":
                page.id = self._allocate_page_id_for_sqlite()
            self._session.add(page)
        else:
            stored_edited_time = self._as_utc(page.last_edited_time)
            if (
                incoming_edited_time is not None
                and stored_edited_time is not None
                and incoming_edited_time < stored_edited_time
            ):
                raise StaleNotionPageSnapshotError(notion_page_id=notion_page_id)
            page.title = title
            page.notion_path = notion_path
            page.parent_notion_page_id = parent_notion_page_id
            if incoming_edited_time is not None:
                page.last_edited_time = incoming_edited_time

        self._session.flush()
        self._session.refresh(page)
        return page


class StaleNotionPageSnapshotError(RuntimeError):
    def __init__(self, *, notion_page_id: str) -> None:
        super().__init__(
            "Prepared Notion page snapshot is stale: "
            f"notion_page_id={notion_page_id}"
        )
        self.notion_page_id = notion_page_id
