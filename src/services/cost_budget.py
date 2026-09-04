from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Tuple

from src.db.models import WorkflowRun


COST_FIELDS = ("estimated_cost", "embedding_estimated_cost")
COST_SCOPE_TODAY = "today"
COST_SCOPE_7D = "7d"
COST_SCOPE_MONTH = "month"
COST_SCOPE_WORKFLOW = "workflow"
COST_SCOPES = frozenset(
    {
        COST_SCOPE_TODAY,
        COST_SCOPE_7D,
        COST_SCOPE_MONTH,
        COST_SCOPE_WORKFLOW,
    }
)


@dataclass(frozen=True)
class CostBudgetDecision:
    status: str
    allowed: bool
    estimated_cost_usd: Optional[float]
    budget_usd: Optional[float]


@dataclass(frozen=True)
class CostBudgetSnapshot:
    daily_cost_usd: float
    daily_budget_usd: Optional[float]
    daily_status: str
    unknown_cost_workflow_count: int
    workflow_budget_exceeded_count: int
    workflow_budget_usd: Optional[float]


@dataclass(frozen=True)
class CostScopeSnapshot:
    scope: str
    workflow_run_id: Optional[int]
    workflow_count: int
    total_cost_usd: float
    llm_cost_usd: float
    embedding_cost_usd: float
    unknown_cost_workflow_count: int
    budget_status: str
    budget_usd: Optional[float]
    workflow_budget_exceeded_count: int
    workflow_budget_usd: Optional[float]


class CostBudgetService:
    """Aggregate recorded workflow costs and evaluate operator thresholds."""

    def __init__(
        self,
        *,
        daily_budget_usd: Optional[float] = None,
        workflow_budget_usd: Optional[float] = None,
    ) -> None:
        self._daily_budget = _normalize_budget(daily_budget_usd)
        self._workflow_budget = _normalize_budget(workflow_budget_usd)

    @property
    def daily_budget_usd(self) -> Optional[float]:
        return _as_float(self._daily_budget)

    @property
    def workflow_budget_usd(self) -> Optional[float]:
        return _as_float(self._workflow_budget)

    def evaluate_workflow_cost(
        self,
        estimated_cost_usd: Optional[float],
    ) -> CostBudgetDecision:
        normalized_cost = _normalize_cost(estimated_cost_usd)
        if normalized_cost is None:
            return CostBudgetDecision(
                status="unknown",
                allowed=True,
                estimated_cost_usd=None,
                budget_usd=self.workflow_budget_usd,
            )
        if self._workflow_budget is None:
            return CostBudgetDecision(
                status="unconfigured",
                allowed=True,
                estimated_cost_usd=_as_float(normalized_cost),
                budget_usd=None,
            )
        if normalized_cost > self._workflow_budget:
            return CostBudgetDecision(
                status="exceeded",
                allowed=False,
                estimated_cost_usd=_as_float(normalized_cost),
                budget_usd=_as_float(self._workflow_budget),
            )
        return CostBudgetDecision(
            status="ok",
            allowed=True,
            estimated_cost_usd=_as_float(normalized_cost),
            budget_usd=_as_float(self._workflow_budget),
        )

    def summarize(
        self,
        workflow_runs: Iterable[WorkflowRun],
        *,
        now: Optional[datetime] = None,
    ) -> CostBudgetSnapshot:
        current_time = _as_utc(now or datetime.now(timezone.utc))
        day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        daily_total = Decimal("0")
        unknown_cost_workflow_count = 0
        workflow_budget_exceeded_count = 0

        for workflow_run in workflow_runs:
            cost, unknown = extract_workflow_cost(workflow_run)
            if unknown and _as_utc(workflow_run.started_at) >= day_start:
                unknown_cost_workflow_count += 1
            if cost is not None:
                if _as_utc(workflow_run.started_at) >= day_start:
                    daily_total += cost
                if self._workflow_budget is not None and cost > self._workflow_budget:
                    workflow_budget_exceeded_count += 1

        if self._daily_budget is None:
            daily_status = "unconfigured"
        elif daily_total > self._daily_budget:
            daily_status = "exceeded"
        elif unknown_cost_workflow_count > 0:
            daily_status = "unknown"
        else:
            daily_status = "ok"

        return CostBudgetSnapshot(
            daily_cost_usd=_as_float(daily_total) or 0.0,
            daily_budget_usd=self.daily_budget_usd,
            daily_status=daily_status,
            unknown_cost_workflow_count=unknown_cost_workflow_count,
            workflow_budget_exceeded_count=workflow_budget_exceeded_count,
            workflow_budget_usd=self.workflow_budget_usd,
        )

    def summarize_scope(
        self,
        workflow_runs: Iterable[WorkflowRun],
        *,
        scope: str,
        workflow_run_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> CostScopeSnapshot:
        normalized_scope = _normalize_scope(scope)
        current_time = _as_utc(now or datetime.now(timezone.utc))
        scoped_runs = list(workflow_runs)
        if normalized_scope != COST_SCOPE_WORKFLOW:
            scope_start = _scope_start(normalized_scope, current_time)
            scoped_runs = [
                workflow_run
                for workflow_run in scoped_runs
                if _as_utc(workflow_run.started_at) >= scope_start
            ]

        total_cost = Decimal("0")
        llm_cost = Decimal("0")
        embedding_cost = Decimal("0")
        unknown_count = 0
        workflow_budget_exceeded_count = 0
        for workflow_run in scoped_runs:
            breakdown = extract_workflow_cost_breakdown(workflow_run)
            if breakdown.unknown:
                unknown_count += 1
            if breakdown.llm_cost is not None:
                llm_cost += breakdown.llm_cost
            if breakdown.embedding_cost is not None:
                embedding_cost += breakdown.embedding_cost
            known_cost = breakdown.total_cost
            if (
                known_cost is not None
                and self._workflow_budget is not None
                and known_cost > self._workflow_budget
            ):
                workflow_budget_exceeded_count += 1
        total_cost = llm_cost + embedding_cost

        budget_status = "not_applicable"
        budget_usd: Optional[float] = None
        if normalized_scope == COST_SCOPE_TODAY:
            budget_usd = self.daily_budget_usd
            if self._daily_budget is None:
                budget_status = "unconfigured"
            elif total_cost > self._daily_budget:
                budget_status = "exceeded"
            elif unknown_count:
                budget_status = "unknown"
            else:
                budget_status = "ok"
        elif normalized_scope == COST_SCOPE_WORKFLOW:
            budget_usd = self.workflow_budget_usd
            if scoped_runs:
                known_cost = total_cost if not unknown_count else None
                budget_status = self.evaluate_workflow_cost(known_cost).status
            else:
                budget_status = "not_found"

        return CostScopeSnapshot(
            scope=normalized_scope,
            workflow_run_id=workflow_run_id,
            workflow_count=len(scoped_runs),
            total_cost_usd=_as_float(total_cost) or 0.0,
            llm_cost_usd=_as_float(llm_cost) or 0.0,
            embedding_cost_usd=_as_float(embedding_cost) or 0.0,
            unknown_cost_workflow_count=unknown_count,
            budget_status=budget_status,
            budget_usd=budget_usd,
            workflow_budget_exceeded_count=workflow_budget_exceeded_count,
            workflow_budget_usd=self.workflow_budget_usd,
        )


def extract_workflow_cost(workflow_run: WorkflowRun) -> Tuple[Optional[Decimal], bool]:
    """Return recorded cost and whether a cost-bearing workflow is unknown."""
    breakdown = extract_workflow_cost_breakdown(workflow_run)
    return breakdown.total_cost, breakdown.unknown


@dataclass(frozen=True)
class WorkflowCostBreakdown:
    llm_cost: Optional[Decimal]
    embedding_cost: Optional[Decimal]
    unknown: bool

    @property
    def total_cost(self) -> Optional[Decimal]:
        costs = [cost for cost in (self.llm_cost, self.embedding_cost) if cost is not None]
        if not costs:
            return None
        return sum(costs, Decimal("0"))


def extract_workflow_cost_breakdown(workflow_run: WorkflowRun) -> WorkflowCostBreakdown:
    """Extract only backend-recorded LLM and embedding cost fields."""
    if not workflow_run.metadata_json:
        return WorkflowCostBreakdown(llm_cost=None, embedding_cost=None, unknown=False)
    try:
        metadata = json.loads(workflow_run.metadata_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return WorkflowCostBreakdown(llm_cost=None, embedding_cost=None, unknown=False)
    if not isinstance(metadata, dict):
        return WorkflowCostBreakdown(llm_cost=None, embedding_cost=None, unknown=False)

    normalized_costs: dict[str, Optional[Decimal]] = {}
    unknown = False
    for field_name in COST_FIELDS:
        if field_name not in metadata:
            continue
        value = metadata[field_name]
        if value is None:
            unknown = True
            continue
        normalized = _normalize_cost(value)
        if normalized is None:
            unknown = True
        else:
            normalized_costs[field_name] = normalized

    return WorkflowCostBreakdown(
        llm_cost=normalized_costs.get("estimated_cost"),
        embedding_cost=normalized_costs.get("embedding_estimated_cost"),
        unknown=unknown,
    )


def _normalize_scope(scope: str) -> str:
    normalized = scope.strip().lower()
    if normalized not in COST_SCOPES:
        raise ValueError("cost scope is invalid")
    return normalized


def _scope_start(scope: str, now: datetime) -> datetime:
    if scope == COST_SCOPE_TODAY:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if scope == COST_SCOPE_7D:
        return now - timedelta(days=7)
    if scope == COST_SCOPE_MONTH:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError("workflow scope does not have a period start")


def _normalize_budget(value: Optional[float]) -> Optional[Decimal]:
    if value is None:
        return None
    normalized = _normalize_cost(value)
    if normalized is None or normalized <= 0:
        raise ValueError("cost budget must be a positive finite number")
    return normalized


def _normalize_cost(value: object) -> Optional[Decimal]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        candidate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not candidate.is_finite() or candidate < 0:
        return None
    return candidate


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None
