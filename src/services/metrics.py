from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import List, Optional

from src.db.session import SessionFactory
from src.repositories import WorkflowRunRepository
from src.services.cost_budget import CostBudgetService
from src.services.workflow_run_service import WORKFLOW_STATUS_RUNNING


class MetricsService:
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
        self._cost_budget_service = cost_budget_service
        self._stale_after_seconds = stale_after_seconds

    def render_prometheus(self, *, now: Optional[datetime] = None) -> str:
        current_time = now or datetime.now(timezone.utc)
        session = self._session_factory()
        try:
            workflow_runs = WorkflowRunRepository(session).list_workflow_runs()
            stale_running_count = sum(
                1
                for workflow_run in workflow_runs
                if workflow_run.status == WORKFLOW_STATUS_RUNNING
                and _age_seconds(workflow_run.started_at, current_time)
                >= self._stale_after_seconds
            )
        finally:
            session.close()

        status_counts = Counter(
            (workflow_run.workflow_type, workflow_run.status)
            for workflow_run in workflow_runs
        )
        running_counts = Counter(
            workflow_run.workflow_type
            for workflow_run in workflow_runs
            if workflow_run.status == WORKFLOW_STATUS_RUNNING
        )
        budget = self._cost_budget_service.summarize(workflow_runs, now=current_time)
        lines: List[str] = [
            "# HELP learnloop_workflow_runs_total Completed and running workflow runs.",
            "# TYPE learnloop_workflow_runs_total counter",
        ]
        for (workflow_type, status), count in sorted(status_counts.items()):
            lines.append(
                "learnloop_workflow_runs_total"
                f'{{workflow_type="{_label(workflow_type)}",status="{_label(status)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP learnloop_workflow_running Current running workflows.",
                "# TYPE learnloop_workflow_running gauge",
            ]
        )
        for workflow_type, count in sorted(running_counts.items()):
            lines.append(
                f'learnloop_workflow_running{{workflow_type="{_label(workflow_type)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP learnloop_workflow_stale_running Current stale running workflows.",
                "# TYPE learnloop_workflow_stale_running gauge",
                f"learnloop_workflow_stale_running {stale_running_count}",
                "# HELP learnloop_cost_usd_total Known cost recorded since UTC midnight.",
                "# TYPE learnloop_cost_usd_total gauge",
                f'learnloop_cost_usd_total{{scope="daily"}} {budget.daily_cost_usd:.12f}',
                "# HELP learnloop_cost_unknown_workflows_total Workflows with unknown recorded cost.",
                "# TYPE learnloop_cost_unknown_workflows_total gauge",
                f"learnloop_cost_unknown_workflows_total {budget.unknown_cost_workflow_count}",
                "# HELP learnloop_cost_budget_exceeded Current budget alert state.",
                "# TYPE learnloop_cost_budget_exceeded gauge",
                f'learnloop_cost_budget_exceeded{{scope="daily"}} {int(budget.daily_status == "exceeded")}',
                f"learnloop_cost_budget_exceeded{{scope=\"workflow\"}} {int(budget.workflow_budget_exceeded_count > 0)}",
            ]
        )
        if budget.daily_budget_usd is not None:
            lines.append(
                f'learnloop_cost_budget_usd{{scope="daily"}} {budget.daily_budget_usd:.12f}'
            )
        if budget.workflow_budget_usd is not None:
            lines.append(
                f'learnloop_cost_budget_usd{{scope="workflow"}} {budget.workflow_budget_usd:.12f}'
            )
        lines.append("")
        return "\n".join(lines)


def _age_seconds(started_at: datetime, now: datetime) -> float:
    started = (
        started_at.replace(tzinfo=timezone.utc)
        if started_at.tzinfo is None
        else started_at.astimezone(timezone.utc)
    )
    current = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    return max(0.0, (current - started).total_seconds())


def _label(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_-" else "_" for character in value)
