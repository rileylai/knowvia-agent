from __future__ import annotations

import pytest

from src.db.unit_of_work import (
    SqlAlchemyUnitOfWork,
    UnitOfWorkAlreadyActiveError,
    UnitOfWorkInactiveError,
)
from src.repositories import (
    ChangeRequestRepository,
    ChunkRepository,
    NotionBlockRepository,
    NotionPageRepository,
    SourceDocumentRepository,
)


class _FakeTransaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _FakeSession:
    def __init__(self, *, fail_begin: bool = False) -> None:
        self.closed = False
        self.begin_count = 0
        self.fail_begin = fail_begin
        self.transaction = _FakeTransaction()

    def begin(self) -> _FakeTransaction:
        self.begin_count += 1
        if self.fail_begin:
            raise RuntimeError("begin failed")
        return self.transaction

    def close(self) -> None:
        self.closed = True


def _build_session_factory() -> tuple[list[_FakeSession], object]:
    sessions: list[_FakeSession] = []

    def session_factory() -> _FakeSession:
        session = _FakeSession()
        sessions.append(session)
        return session

    return sessions, session_factory


def test_unit_of_work_opens_fresh_session_and_exposes_business_repositories() -> None:
    sessions, session_factory = _build_session_factory()
    unit_of_work = SqlAlchemyUnitOfWork(session_factory)

    with unit_of_work as active:
        assert active is unit_of_work
        assert sessions[0].begin_count == 1
        assert isinstance(active.notion_pages, NotionPageRepository)
        assert isinstance(active.notion_blocks, NotionBlockRepository)
        assert isinstance(active.chunks, ChunkRepository)
        assert isinstance(active.source_documents, SourceDocumentRepository)
        assert isinstance(active.change_requests, ChangeRequestRepository)

    assert len(sessions) == 1
    assert sessions[0].transaction.committed is True
    assert sessions[0].transaction.rolled_back is False
    assert sessions[0].closed is True


def test_unit_of_work_rolls_back_on_exception_and_closes_session() -> None:
    sessions, session_factory = _build_session_factory()
    unit_of_work = SqlAlchemyUnitOfWork(session_factory)

    with pytest.raises(ValueError):
        with unit_of_work:
            raise ValueError("boom")

    assert sessions[0].transaction.committed is False
    assert sessions[0].transaction.rolled_back is True
    assert sessions[0].closed is True


def test_unit_of_work_uses_fresh_session_per_context_entry() -> None:
    sessions, session_factory = _build_session_factory()
    unit_of_work = SqlAlchemyUnitOfWork(session_factory)

    with unit_of_work:
        pass
    with unit_of_work:
        pass

    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert sessions[0].closed is True
    assert sessions[1].closed is True


def test_unit_of_work_rejects_repository_access_outside_context() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(lambda: _FakeSession())

    with pytest.raises(UnitOfWorkInactiveError):
        _ = unit_of_work.source_documents

    with unit_of_work:
        pass

    with pytest.raises(UnitOfWorkInactiveError):
        _ = unit_of_work.source_documents


def test_unit_of_work_rejects_nested_entry_and_has_no_manual_commit_api() -> None:
    sessions, session_factory = _build_session_factory()
    unit_of_work = SqlAlchemyUnitOfWork(session_factory)

    with unit_of_work:
        with pytest.raises(UnitOfWorkAlreadyActiveError):
            with unit_of_work:
                pass

    assert hasattr(unit_of_work, "commit") is False
    assert sessions[0].transaction.committed is True
    assert sessions[0].closed is True


def test_unit_of_work_closes_session_when_begin_fails() -> None:
    session = _FakeSession(fail_begin=True)
    unit_of_work = SqlAlchemyUnitOfWork(lambda: session)

    with pytest.raises(RuntimeError, match="begin failed"):
        with unit_of_work:
            pass

    assert session.closed is True
    with pytest.raises(UnitOfWorkInactiveError):
        _ = unit_of_work.source_documents
