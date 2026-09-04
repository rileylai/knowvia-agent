from __future__ import annotations

from types import TracebackType
from typing import Callable, Optional, Type

from sqlalchemy.orm import Session

from src.repositories.change_request_repository import ChangeRequestRepository
from src.repositories.chunk_repository import ChunkRepository
from src.repositories.notion_block_repository import NotionBlockRepository
from src.repositories.notion_page_repository import NotionPageRepository
from src.repositories.source_document_repository import SourceDocumentRepository

SessionFactory = Callable[[], Session]
UnitOfWorkFactory = Callable[[], "SqlAlchemyUnitOfWork"]


class UnitOfWorkError(RuntimeError):
    pass


class UnitOfWorkInactiveError(UnitOfWorkError):
    pass


class UnitOfWorkAlreadyActiveError(UnitOfWorkError):
    pass


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: Optional[Session] = None
        self._transaction = None
        self._notion_pages: Optional[NotionPageRepository] = None
        self._notion_blocks: Optional[NotionBlockRepository] = None
        self._chunks: Optional[ChunkRepository] = None
        self._source_documents: Optional[SourceDocumentRepository] = None
        self._change_requests: Optional[ChangeRequestRepository] = None

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        if self._session is not None:
            raise UnitOfWorkAlreadyActiveError("Unit of Work is already active")

        session = self._session_factory()
        try:
            self._session = session
            self._transaction = session.begin()
            self._notion_pages = NotionPageRepository(session)
            self._notion_blocks = NotionBlockRepository(session)
            self._chunks = ChunkRepository(session)
            self._source_documents = SourceDocumentRepository(session)
            self._change_requests = ChangeRequestRepository(session)
            return self
        except Exception:
            self._clear_repositories()
            self._transaction = None
            self._session = None
            session.close()
            raise

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        session = self._session
        transaction = self._transaction
        try:
            if transaction is not None:
                if exc_type is None:
                    transaction.commit()
                else:
                    transaction.rollback()
        finally:
            self._clear_repositories()
            self._transaction = None
            self._session = None
            if session is not None:
                session.close()

    @property
    def notion_pages(self) -> NotionPageRepository:
        if self._notion_pages is None:
            raise UnitOfWorkInactiveError("Unit of Work is not active")
        return self._notion_pages

    @property
    def notion_blocks(self) -> NotionBlockRepository:
        if self._notion_blocks is None:
            raise UnitOfWorkInactiveError("Unit of Work is not active")
        return self._notion_blocks

    @property
    def chunks(self) -> ChunkRepository:
        if self._chunks is None:
            raise UnitOfWorkInactiveError("Unit of Work is not active")
        return self._chunks

    @property
    def source_documents(self) -> SourceDocumentRepository:
        if self._source_documents is None:
            raise UnitOfWorkInactiveError("Unit of Work is not active")
        return self._source_documents

    @property
    def change_requests(self) -> ChangeRequestRepository:
        if self._change_requests is None:
            raise UnitOfWorkInactiveError("Unit of Work is not active")
        return self._change_requests

    @property
    def database_dialect(self) -> Optional[str]:
        if self._session is None or self._session.bind is None:
            return None
        return self._session.bind.dialect.name

    def _clear_repositories(self) -> None:
        self._notion_pages = None
        self._notion_blocks = None
        self._chunks = None
        self._source_documents = None
        self._change_requests = None
