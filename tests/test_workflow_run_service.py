from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import SourceDocument, WorkflowRun
from src.services import (
    WORKFLOW_STATUS_FAILED,
    WORKFLOW_STATUS_RUNNING,
    WORKFLOW_STATUS_SUCCEEDED,
    WorkflowRunNotFoundError,
    WorkflowRunService,
    WorkflowRunValidationError,
)


def _build_test_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[SourceDocument.__table__, WorkflowRun.__table__],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_workflow_run_service_start_workflow_persists_running_status() -> None:
    session_factory = _build_test_session_factory()
    service = WorkflowRunService(session_factory)

    created = service.start_workflow(
        workflow_type="indexing",
        metadata_json='{"sync_mode":"manual"}',
    )

    assert created.id == 1
    assert created.status == WORKFLOW_STATUS_RUNNING
    assert created.failure_reason is None
    assert created.metadata_json == '{"sync_mode":"manual"}'


def test_workflow_run_service_mark_succeeded_updates_status() -> None:
    session_factory = _build_test_session_factory()
    service = WorkflowRunService(session_factory)

    created = service.start_workflow(workflow_type="ingestion")
    updated = service.mark_workflow_succeeded(
        created.id,
        metadata_json='{"result":"ok"}',
    )

    assert updated.status == WORKFLOW_STATUS_SUCCEEDED
    assert updated.failure_reason is None
    assert updated.metadata_json == '{"result":"ok"}'
    assert updated.finished_at is not None


def test_workflow_run_service_mark_failed_updates_standardized_failure_reason() -> None:
    session_factory = _build_test_session_factory()
    service = WorkflowRunService(session_factory)

    created = service.start_workflow(workflow_type="ingestion")
    updated = service.mark_workflow_failed(
        created.id,
        failure_reason="url_fetch_failed",
        metadata_json='{"url":"https://example.com"}',
    )

    assert updated.status == WORKFLOW_STATUS_FAILED
    assert updated.failure_reason == "URL_FETCH_FAILED"
    assert updated.finished_at is not None


def test_workflow_run_service_rejects_unknown_failure_reason() -> None:
    session_factory = _build_test_session_factory()
    service = WorkflowRunService(session_factory)

    created = service.start_workflow(workflow_type="qa")
    with pytest.raises(WorkflowRunValidationError):
        service.mark_workflow_failed(created.id, failure_reason="INVALID_REASON")


def test_workflow_run_service_raises_not_found_on_missing_workflow_run() -> None:
    session_factory = _build_test_session_factory()
    fixed_now = datetime.now(timezone.utc)
    service = WorkflowRunService(session_factory, now_provider=lambda: fixed_now)

    with pytest.raises(WorkflowRunNotFoundError):
        service.mark_workflow_succeeded(999)


def test_workflow_run_service_audit_commit_does_not_commit_business_session() -> None:
    session_factory = _build_test_session_factory()
    business_session: Session = session_factory()
    service = WorkflowRunService(session_factory)

    business_session.add(
        SourceDocument(
            source_type="chat_text",
            source_display_name="uncommitted business source",
            content_hash="hash-uncommitted",
            raw_text="private local draft",
        )
    )

    created = service.start_workflow(workflow_type="ingestion")

    verification_session: Session = session_factory()
    try:
        persisted_workflow = verification_session.get(WorkflowRun, created.id)
        persisted_sources = verification_session.query(SourceDocument).all()
    finally:
        verification_session.close()
        business_session.rollback()
        business_session.close()

    assert persisted_workflow is not None
    assert persisted_workflow.status == WORKFLOW_STATUS_RUNNING
    assert persisted_sources == []
