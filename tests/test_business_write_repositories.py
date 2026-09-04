from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import ChangeRequest, SourceDocument
from src.repositories import ChangeRequestRepository, SourceDocumentRepository


def _build_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[SourceDocument.__table__, ChangeRequest.__table__],
    )
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return local_session()


def test_remaining_business_write_repositories_flush_without_commit() -> None:
    session = _build_test_session()
    source_documents = SourceDocumentRepository(session)
    change_requests = ChangeRequestRepository(session)

    source_document = source_documents.create_source_document(
        source_type="chat_text",
        source_display_name="Flush test",
        raw_text="Transaction boundary test",
        content_hash="hash-flush-test",
    )
    change_request = change_requests.create_change_request(
        source_document_id=source_document.id,
        target_notion_page_id=None,
        status="pending",
        proposal_json="{}",
    )
    updated = change_requests.update_change_request_status(
        change_request.id,
        status="rejected",
        failure_reason="TEST_REJECTION",
    )

    assert updated is not None
    assert session.query(SourceDocument).count() == 1
    assert session.query(ChangeRequest).count() == 1
    assert updated.status == "rejected"
    assert updated.failure_reason == "TEST_REJECTION"

    session.rollback()

    assert session.query(SourceDocument).count() == 0
    assert session.query(ChangeRequest).count() == 0
