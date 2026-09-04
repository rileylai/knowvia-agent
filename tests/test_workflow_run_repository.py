from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.base import Base
from src.db.models import WorkflowRun
from src.repositories import WorkflowRunRepository


def _build_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[WorkflowRun.__table__])
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return local_session()


def test_workflow_run_repository_create_read_update_cycle() -> None:
    session = _build_test_session()
    repository = WorkflowRunRepository(session)

    created = repository.create_workflow_run(
        workflow_run_id=1,
        workflow_type="indexing",
        status="running",
    )
    assert created.id == 1
    assert created.workflow_type == "indexing"
    assert created.status == "running"

    loaded = repository.get_workflow_run_by_id(1)
    assert loaded is not None
    assert loaded.id == 1
    assert loaded.status == "running"

    finished_at = datetime.now(timezone.utc)
    updated = repository.update_workflow_run(
        1,
        status="succeeded",
        metadata_json='{"source":"unit-test"}',
        finished_at=finished_at,
    )
    assert updated is not None
    assert updated.id == 1
    assert updated.status == "succeeded"
    assert updated.metadata_json == '{"source":"unit-test"}'
    assert updated.finished_at is not None


def test_workflow_run_repository_update_returns_none_for_missing_id() -> None:
    session = _build_test_session()
    repository = WorkflowRunRepository(session)

    updated = repository.update_workflow_run(
        999,
        status="failed",
        failure_reason="UNKNOWN_ERROR",
    )

    assert updated is None
