from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import (
    get_cost_budget_service,
    get_metrics_service,
    get_workflow_observability_service,
)
from src.app.main import app
from src.db.base import Base
from src.db.models import WorkflowRun
from src.services import (
    CostBudgetService,
    MetricsService,
    WorkflowObservabilityService,
    WorkflowRunService,
    WorkflowRunValidationError,
)


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[WorkflowRun.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _old_running_workflow(session_factory, *, metadata_json=None):
    workflow_service = WorkflowRunService(session_factory)
    workflow = workflow_service.start_workflow(
        workflow_type="qa",
        metadata_json=metadata_json,
    )
    session = session_factory()
    try:
        stored = session.get(WorkflowRun, workflow.id)
        assert stored is not None
        stored.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.commit()
    finally:
        session.close()
    return workflow.id


def test_workflow_status_redacts_private_metadata_and_marks_stale() -> None:
    session_factory = _session_factory()
    workflow_id = _old_running_workflow(
        session_factory,
        metadata_json=json.dumps(
            {
                "raw_text": "private note",
                "api_key": "secret-value",
                "nested": {"source_text": "private source"},
            }
        ),
    )
    service = WorkflowObservabilityService(
        session_factory,
        cost_budget_service=CostBudgetService(),
        stale_after_seconds=60,
    )

    status = service.get_workflow(workflow_id)

    assert status is not None
    assert status.stale is True
    assert status.metadata["raw_text"] == "[REDACTED]"
    assert status.metadata["api_key"] == "[REDACTED]"
    assert status.metadata["nested"]["source_text"] == "[REDACTED]"
    assert "private note" not in json.dumps(status.metadata)


def test_reconcile_requires_stale_running_workflow_and_terminal_reason() -> None:
    session_factory = _session_factory()
    workflow_id = _old_running_workflow(session_factory)
    service = WorkflowObservabilityService(
        session_factory,
        cost_budget_service=CostBudgetService(),
        stale_after_seconds=60,
    )

    with pytest.raises(ValueError):
        service.reconcile_workflow(workflow_id, status="failed")

    reconciled = service.reconcile_workflow(
        workflow_id,
        status="failed",
        failure_reason="UNKNOWN_ERROR",
    )
    assert reconciled.status == "failed"
    assert reconciled.stale is False

    with pytest.raises(WorkflowRunValidationError):
        service.reconcile_workflow(
            workflow_id,
            status="succeeded",
        )


def test_cost_budget_summary_and_unknown_cost_are_deterministic() -> None:
    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    known = WorkflowRun(
        id=1,
        workflow_type="qa",
        status="succeeded",
        started_at=now,
        metadata_json=json.dumps(
            {"estimated_cost": 0.7, "embedding_estimated_cost": 0.4}
        ),
    )
    unknown = WorkflowRun(
        id=2,
        workflow_type="qa",
        status="succeeded",
        started_at=now,
        metadata_json=json.dumps({"estimated_cost": None}),
    )
    service = CostBudgetService(daily_budget_usd=1.0, workflow_budget_usd=1.0)

    snapshot = service.summarize([known, unknown], now=now)

    assert snapshot.daily_cost_usd == pytest.approx(1.1)
    assert snapshot.daily_status == "exceeded"
    assert snapshot.unknown_cost_workflow_count == 1
    assert snapshot.workflow_budget_exceeded_count == 1
    assert service.evaluate_workflow_cost(1.1).allowed is False
    assert service.evaluate_workflow_cost(None).status == "unknown"


def test_metrics_scrape_is_stable_and_contains_no_workflow_metadata() -> None:
    session_factory = _session_factory()
    _old_running_workflow(
        session_factory,
        metadata_json=json.dumps({"source_text": "private source"}),
    )
    metrics_service = MetricsService(
        session_factory,
        cost_budget_service=CostBudgetService(daily_budget_usd=1.0),
        stale_after_seconds=60,
    )

    payload = metrics_service.render_prometheus(
        now=datetime.now(timezone.utc),
    )

    assert "learnloop_workflow_runs_total" in payload
    assert "learnloop_workflow_stale_running 1" in payload
    assert "private source" not in payload
    assert "source_text" not in payload


def test_metrics_route_hides_collection_failure_details() -> None:
    class FailingMetricsService:
        def render_prometheus(self):
            raise RuntimeError("source_text=private database detail")

    app.dependency_overrides[get_metrics_service] = lambda: FailingMetricsService()
    try:
        response = TestClient(app).get("/metrics")
    finally:
        app.dependency_overrides.pop(get_metrics_service, None)

    assert response.status_code == 503
    assert "source_text" not in response.text
    assert "private database detail" not in response.text
    assert "learnloop_metrics_collection_failed 1" in response.text


def test_operator_workflow_status_route_uses_protected_surface() -> None:
    session_factory = _session_factory()
    workflow_id = WorkflowRunService(session_factory).start_workflow(
        workflow_type="qa",
        metadata_json=json.dumps({"query_length": 10}),
    ).id
    observability_service = WorkflowObservabilityService(
        session_factory,
        cost_budget_service=CostBudgetService(),
    )
    app.dependency_overrides[get_workflow_observability_service] = (
        lambda: observability_service
    )
    app.dependency_overrides[get_cost_budget_service] = lambda: CostBudgetService()
    app.dependency_overrides[get_metrics_service] = lambda: MetricsService(
        session_factory,
        cost_budget_service=CostBudgetService(),
    )
    try:
        response = TestClient(app).get(f"/api/ops/workflows/{workflow_id}")
    finally:
        app.dependency_overrides.pop(get_workflow_observability_service, None)
        app.dependency_overrides.pop(get_cost_budget_service, None)
        app.dependency_overrides.pop(get_metrics_service, None)

    assert response.status_code == 200
    assert response.json()["workflow_run_id"] == workflow_id
