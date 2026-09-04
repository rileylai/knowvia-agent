from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from src.db.models import WorkflowRun


class WorkflowRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _allocate_workflow_run_id_for_sqlite(self) -> int:
        max_id = self._session.query(func.max(WorkflowRun.id)).scalar()
        return int(max_id or 0) + 1

    def create_workflow_run(
        self,
        *,
        workflow_type: str,
        status: str,
        workflow_run_id: Optional[int] = None,
        failure_reason: Optional[str] = None,
        metadata_json: Optional[str] = None,
        finished_at: Optional[datetime] = None,
    ) -> WorkflowRun:
        workflow_run = WorkflowRun(
            workflow_type=workflow_type,
            status=status,
            failure_reason=failure_reason,
            metadata_json=metadata_json,
            finished_at=finished_at,
        )
        if workflow_run_id is not None:
            workflow_run.id = workflow_run_id
        elif self._session.bind is not None and self._session.bind.dialect.name == "sqlite":
            workflow_run.id = self._allocate_workflow_run_id_for_sqlite()

        self._session.add(workflow_run)
        self._session.commit()
        self._session.refresh(workflow_run)
        return workflow_run

    def get_workflow_run_by_id(self, workflow_run_id: int) -> Optional[WorkflowRun]:
        return self._session.get(WorkflowRun, workflow_run_id)

    def get_latest_workflow_run(self, *, workflow_type: str) -> Optional[WorkflowRun]:
        return (
            self._session.query(WorkflowRun)
            .filter(WorkflowRun.workflow_type == workflow_type)
            .order_by(desc(WorkflowRun.started_at), desc(WorkflowRun.id))
            .first()
        )

    def list_workflow_runs(
        self,
        *,
        started_after: Optional[datetime] = None,
        status: Optional[str] = None,
        limit: int = 10000,
    ) -> List[WorkflowRun]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        query = self._session.query(WorkflowRun)
        if started_after is not None:
            query = query.filter(WorkflowRun.started_at >= started_after)
        if status is not None:
            query = query.filter(WorkflowRun.status == status)
        return list(
            query.order_by(desc(WorkflowRun.started_at), desc(WorkflowRun.id))
            .limit(limit)
            .all()
        )

    def list_running_workflows_before(
        self,
        *,
        started_before: datetime,
        limit: int = 100,
    ) -> List[WorkflowRun]:
        if limit <= 0:
            raise ValueError("limit must be positive")

        return list(
            self._session.query(WorkflowRun)
            .filter(
                WorkflowRun.status == "running",
                WorkflowRun.started_at < started_before,
            )
            .order_by(WorkflowRun.started_at.asc(), WorkflowRun.id.asc())
            .limit(limit)
            .all()
        )

    def update_workflow_run(
        self,
        workflow_run_id: int,
        *,
        status: str,
        failure_reason: Optional[str] = None,
        metadata_json: Optional[str] = None,
        finished_at: Optional[datetime] = None,
        commit: bool = True,
    ) -> Optional[WorkflowRun]:
        workflow_run = self.get_workflow_run_by_id(workflow_run_id)
        if workflow_run is None:
            return None

        workflow_run.status = status
        workflow_run.failure_reason = failure_reason
        workflow_run.metadata_json = metadata_json
        workflow_run.finished_at = finished_at
        if commit:
            self._session.commit()
            self._session.refresh(workflow_run)
        else:
            self._session.flush()
        return workflow_run
