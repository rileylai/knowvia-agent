from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class ReadinessCheck(BaseModel):
    status: str
    detail: str
    failure_reason: Optional[str] = None


class ReadinessResponse(BaseModel):
    status: str
    mode: str
    checks: Dict[str, ReadinessCheck]


class WorkflowStatusResponse(BaseModel):
    workflow_run_id: int
    workflow_type: str
    status: str
    failure_reason: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    age_seconds: float
    stale: bool
    estimated_cost_usd: Optional[float] = None
    metadata: Dict[str, Any]


class WorkflowStatusListResponse(BaseModel):
    workflows: List[WorkflowStatusResponse]


class WorkflowReconcileRequest(BaseModel):
    status: Literal["succeeded", "failed"]
    failure_reason: Optional[str] = None


class CostBudgetResponse(BaseModel):
    daily_cost_usd: float
    daily_budget_usd: Optional[float] = None
    daily_status: str
    unknown_cost_workflow_count: int
    workflow_budget_exceeded_count: int
    workflow_budget_usd: Optional[float] = None
