from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import NotionBlock, NotionPage
from src.repositories import (
    NotionBlockRepository,
    NotionBlockSnapshot,
    NotionPageRepository,
    StaleNotionPageSnapshotError,
)


def _build_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            NotionPage.__table__,
            NotionBlock.__table__,
        ],
    )
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return local_session()


def test_notion_page_repository_upsert_flushes_without_commit() -> None:
    session = _build_test_session()
    repository = NotionPageRepository(session)

    page = repository.upsert_page_snapshot(
        notion_page_id="page-flush-only",
        title="Flush Only",
        notion_path="Knowledge/Flush Only",
    )

    assert page.id == 1
    assert session.query(NotionPage).count() == 1

    session.rollback()

    assert session.query(NotionPage).count() == 0


def test_notion_page_repository_rejects_stale_snapshot_before_mutation() -> None:
    session = _build_test_session()
    repository = NotionPageRepository(session)
    current_time = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)

    repository.upsert_page_snapshot(
        notion_page_id="page-stale",
        title="Current",
        notion_path="Knowledge/Current",
        last_edited_time=current_time,
    )

    with pytest.raises(StaleNotionPageSnapshotError):
        repository.upsert_page_snapshot(
            notion_page_id="page-stale",
            title="Older snapshot",
            notion_path="Knowledge/Older",
            last_edited_time=datetime(2026, 7, 27, 11, tzinfo=timezone.utc),
        )

    page = repository.get_by_notion_page_id("page-stale")
    assert page is not None
    assert page.title == "Current"
    assert page.notion_path == "Knowledge/Current"


def test_notion_page_repository_preserves_timestamp_for_legacy_null_snapshot() -> None:
    session = _build_test_session()
    repository = NotionPageRepository(session)
    current_time = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)

    repository.upsert_page_snapshot(
        notion_page_id="page-legacy-null",
        title="Current",
        notion_path="Knowledge/Current",
        last_edited_time=current_time,
    )
    repository.upsert_page_snapshot(
        notion_page_id="page-legacy-null",
        title="Legacy reader update",
        notion_path="Knowledge/Current",
    )

    page = repository.get_by_notion_page_id("page-legacy-null")
    assert page is not None
    assert page.title == "Legacy reader update"
    assert page.last_edited_time == current_time.replace(tzinfo=None)


def test_notion_page_repository_persists_nullable_canonical_parent_identity() -> None:
    session = _build_test_session()
    repository = NotionPageRepository(session)

    repository.upsert_page_snapshot(
        notion_page_id="child",
        title="Child",
        notion_path="Knowledge/Parent/Child",
        parent_notion_page_id="parent",
    )
    repository.upsert_page_snapshot(
        notion_page_id="child",
        title="Child renamed",
        notion_path="Knowledge/Parent/Child renamed",
        parent_notion_page_id=None,
    )

    page = repository.get_by_notion_page_id("child")
    assert page is not None
    assert page.parent_notion_page_id is None


def test_notion_page_repository_uses_postgresql_transaction_advisory_lock() -> None:
    session = Mock()
    session.bind = Mock()
    session.bind.dialect.name = "postgresql"
    repository = NotionPageRepository(session)

    repository.lock_page_for_reindex("page-lock")

    statement, parameters = session.execute.call_args.args
    assert "pg_advisory_xact_lock" in statement.text
    assert "hashtextextended" in statement.text
    assert parameters == {"notion_page_id": "page-lock"}


def test_notion_block_repository_replace_flushes_without_commit() -> None:
    session = _build_test_session()
    page = NotionPage(
        id=1,
        notion_page_id="page-blocks",
        title="Blocks",
        notion_path="Knowledge/Blocks",
    )
    session.add(page)
    session.commit()
    repository = NotionBlockRepository(session)

    inserted_blocks = repository.replace_page_blocks(
        notion_page_db_id=page.id,
        root_blocks=[
            NotionBlockSnapshot(
                notion_block_id="block-1",
                block_type="paragraph",
                content_text="Block 1",
                block_path="Knowledge/Blocks/Block 1",
            )
        ],
    )

    assert len(inserted_blocks) == 1
    assert session.query(NotionBlock).count() == 1

    session.rollback()

    assert session.query(NotionBlock).count() == 0
