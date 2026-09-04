from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.db.models import WorkflowRun
from src.db.session import SessionFactory
from src.observability.redaction import sanitize_sensitive_text
from src.repositories import WorkflowRunRepository
from src.services.cost_budget import (
    COST_SCOPE_WORKFLOW,
    CostBudgetService,
    CostScopeSnapshot,
    CostBudgetSnapshot,
    extract_workflow_cost,
)
from src.services.workflow_run_service import (
    WORKFLOW_STATUS_FAILED,
    WORKFLOW_STATUS_RUNNING,
    WORKFLOW_STATUS_SUCCEEDED,
    WorkflowRunService,
)


@dataclass(frozen=True)
class WorkflowStatusView:
    workflow_run_id: int
    workflow_type: str
    status: str
    failure_reason: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]
    age_seconds: float
    stale: bool
    estimated_cost_usd: Optional[float]
    metadata: Dict[str, Any]


class WorkflowObservabilityService:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        cost_budget_service: CostBudgetService,
        stale_after_seconds: int = 3600,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self._session_factory = session_factory
        self._workflow_run_service = WorkflowRunService(session_factory)
        self._cost_budget_service = cost_budget_service
        self._stale_after_seconds = stale_after_seconds

    @property
    def stale_after_seconds(self) -> int:
        return self._stale_after_seconds

    def get_workflow(
        self,
        workflow_run_id: int,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[WorkflowStatusView]:
        session = self._session_factory()
        try:
            workflow_run = WorkflowRunRepository(session).get_workflow_run_by_id(
                workflow_run_id
            )
            return (
                None
                if workflow_run is None
                else self._to_status_view(workflow_run, now=now)
            )
        finally:
            session.close()

    def list_workflows(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        now: Optional[datetime] = None,
    ) -> List[WorkflowStatusView]:
        normalized_status = self._normalize_status_filter(status)
        session = self._session_factory()
        try:
            workflow_runs = WorkflowRunRepository(session).list_workflow_runs(
                status=normalized_status,
                limit=limit,
            )
            return [self._to_status_view(run, now=now) for run in workflow_runs]
        finally:
            session.close()

    def list_stale_workflows(
        self,
        *,
        now: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[WorkflowStatusView]:
        current_time = _as_utc(now or datetime.now(timezone.utc))
        stale_before = current_time - timedelta(seconds=self._stale_after_seconds)
        session = self._session_factory()
        try:
            workflow_runs = WorkflowRunRepository(session).list_running_workflows_before(
                started_before=stale_before,
                limit=limit,
            )
            return [self._to_status_view(run, now=current_time) for run in workflow_runs]
        finally:
            session.close()

    def cost_snapshot(
        self,
        *,
        now: Optional[datetime] = None,
        limit: int = 10000,
    ) -> CostBudgetSnapshot:
        session = self._session_factory()
        try:
            workflow_runs = WorkflowRunRepository(session).list_workflow_runs(
                limit=limit,
            )
            return self._cost_budget_service.summarize(workflow_runs, now=now)
        finally:
            session.close()

    def cost_summary(
        self,
        *,
        scope: str,
        workflow_run_id: Optional[int] = None,
        now: Optional[datetime] = None,
        limit: int = 10000,
    ) -> Optional[CostScopeSnapshot]:
        if scope == COST_SCOPE_WORKFLOW and workflow_run_id is None:
            raise ValueError("workflow cost scope requires workflow_run_id")
        session = self._session_factory()
        try:
            repository = WorkflowRunRepository(session)
            if workflow_run_id is not None:
                workflow_run = repository.get_workflow_run_by_id(workflow_run_id)
                if workflow_run is None:
                    return None
                workflow_runs = [workflow_run]
            else:
                workflow_runs = repository.list_workflow_runs(limit=limit)
            return self._cost_budget_service.summarize_scope(
                workflow_runs,
                scope=scope,
                workflow_run_id=workflow_run_id,
                now=now,
            )
        finally:
            session.close()

    def reconcile_workflow(
        self,
        workflow_run_id: int,
        *,
        status: str,
        failure_reason: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> WorkflowStatusView:
        current_time = _as_utc(now or datetime.now(timezone.utc))
        normalized_status = status.strip().lower()
        if normalized_status == WORKFLOW_STATUS_FAILED and failure_reason is None:
            raise ValueError("failed reconciliation requires failure_reason")
        if normalized_status == WORKFLOW_STATUS_SUCCEEDED and failure_reason is not None:
            raise ValueError("succeeded reconciliation must not include failure_reason")

        reconciled = self._workflow_run_service.reconcile_stale_running_workflow(
            workflow_run_id,
            status=normalized_status,
            failure_reason=failure_reason,
            metadata_json=json.dumps(
                {
                    "operation": "operator_reconcile",
                    "reconciled_by": "operator",
                    "resolution_status": normalized_status,
                },
                sort_keys=True,
            ),
            stale_before=current_time - timedelta(seconds=self._stale_after_seconds),
        )
        return self._to_status_view(reconciled, now=current_time)

    def _to_status_view(
        self,
        workflow_run: WorkflowRun,
        *,
        now: Optional[datetime] = None,
    ) -> WorkflowStatusView:
        current_time = _as_utc(now or datetime.now(timezone.utc))
        started_at = _as_utc(workflow_run.started_at)
        age_seconds = max(0.0, (current_time - started_at).total_seconds())
        cost, _ = extract_workflow_cost(workflow_run)
        return WorkflowStatusView(
            workflow_run_id=int(workflow_run.id),
            workflow_type=workflow_run.workflow_type,
            status=workflow_run.status,
            failure_reason=workflow_run.failure_reason,
            started_at=workflow_run.started_at,
            finished_at=workflow_run.finished_at,
            age_seconds=round(age_seconds, 3),
            stale=(
                workflow_run.status == WORKFLOW_STATUS_RUNNING
                and age_seconds >= self._stale_after_seconds
            ),
            estimated_cost_usd=(float(cost) if cost is not None else None),
            metadata=_safe_metadata(workflow_run.metadata_json),
        )

    def _normalize_status_filter(self, status: Optional[str]) -> Optional[str]:
        if status is None:
            return None
        normalized = status.strip().lower()
        if normalized not in {
            WORKFLOW_STATUS_RUNNING,
            WORKFLOW_STATUS_SUCCEEDED,
            WORKFLOW_STATUS_FAILED,
        }:
            raise ValueError("status must be running, succeeded, or failed")
        return normalized


def _safe_metadata(metadata_json: Optional[str]) -> Dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        parsed = json.loads(metadata_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return _redact_value(parsed)


def _redact_value(value: Any, *, key: Optional[str] = None) -> Any:
    normalized_key = (key or "").strip().lower()
    if normalized_key in {
        "raw_text",
        "source_text",
        "api_key",
        "openai_api_key",
        "notion_token",
        "telegram_bot_token",
        "bot_token",
        "authorization",
        "api_bearer_token",
        "telegram_webhook_secret",
    }:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_sensitive_text(value)
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
