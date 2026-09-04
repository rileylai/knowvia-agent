import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import SourceDocument, WorkflowRun
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.orchestrators.source_document_orchestrator import (
    SourceDocumentOrchestrator,
    SourceDocumentWorkflowError,
)
from src.repositories import WorkflowRunRepository
from src.services import (
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
    WorkflowRunValidationError,
)


def _build_session_factory():
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


def test_success_audit_failure_keeps_business_commit_and_running_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _build_session_factory()
    workflow_service = WorkflowRunService(session_factory)

    original_update = WorkflowRunRepository.update_workflow_run

    def fail_audit_update(*args, **kwargs):
        _ = original_update, args, kwargs
        raise RuntimeError("injected audit database failure")

    monkeypatch.setattr(WorkflowRunRepository, "update_workflow_run", fail_audit_update)
    orchestrator = SourceDocumentOrchestrator(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=workflow_service,
    )

    with pytest.raises(WorkflowRunAuditUpdateError) as exc_info:
        asyncio.run(
            orchestrator.create_source_document(
                source_type="chat_text",
                source_display_name="audit-failure-source",
                raw_text="business content",
                request_workflow_id="request-audit-failure",
            )
        )

    assert exc_info.value.error_code == "WORKFLOW_AUDIT_UPDATE_FAILED"
    verify_session = session_factory()
    try:
        assert verify_session.query(SourceDocument).count() == 1
        workflow_run = verify_session.query(WorkflowRun).one()
        assert workflow_run.status == "running"
    finally:
        verify_session.close()


def test_business_exception_is_preserved_when_failure_audit_update_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = _build_session_factory()
    workflow_service = WorkflowRunService(session_factory)

    def fail_audit_update(*args, **kwargs):
        _ = args, kwargs
        raise RuntimeError("injected audit database failure")

    monkeypatch.setattr(WorkflowRunRepository, "update_workflow_run", fail_audit_update)

    class FailingUnitOfWork:
        def __enter__(self):
            raise RuntimeError("injected business failure")

        def __exit__(self, exc_type, exc_value, traceback):
            _ = exc_type, exc_value, traceback
            return False

    orchestrator = SourceDocumentOrchestrator(
        unit_of_work_factory=lambda: FailingUnitOfWork(),
        workflow_run_service=workflow_service,
    )

    with pytest.raises(SourceDocumentWorkflowError) as exc_info:
        asyncio.run(
            orchestrator.create_source_document(
                source_type="chat_text",
                source_display_name="business-failure-source",
                raw_text="business content",
                request_workflow_id="request-business-failure",
            )
        )

    assert exc_info.value.error_code == "SOURCE_DOCUMENT_CREATE_FAILED"
    assert exc_info.value.failure_reason == "UNKNOWN_ERROR"
    verify_session = session_factory()
    try:
        workflow_run = verify_session.query(WorkflowRun).one()
        assert workflow_run.status == "running"
    finally:
        verify_session.close()


def test_reconcile_stale_running_workflow_requires_running_status() -> None:
    session_factory = _build_session_factory()
    workflow_service = WorkflowRunService(session_factory)
    workflow_run = workflow_service.start_workflow(workflow_type="ingestion")

    reconciled = workflow_service.reconcile_stale_running_workflow(
        workflow_run.id,
        status="succeeded",
        metadata_json='{"reconciled":true}',
    )
    assert reconciled.status == "succeeded"

    with pytest.raises(WorkflowRunValidationError):
        workflow_service.reconcile_stale_running_workflow(
            workflow_run.id,
            status="failed",
            failure_reason="UNKNOWN_ERROR",
        )
