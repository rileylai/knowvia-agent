from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from src.app.dependencies import (
    get_metrics_service,
    get_readiness_service,
    get_workflow_observability_service,
    require_api_bearer_token,
)
from src.app.schemas.ops import (
    CostBudgetResponse,
    ReadinessCheck,
    ReadinessResponse,
    WorkflowReconcileRequest,
    WorkflowStatusListResponse,
    WorkflowStatusResponse,
)
from src.services import (
    MetricsService,
    ReadinessReport,
    ReadinessService,
    WorkflowObservabilityService,
    WorkflowRunNotFoundError,
    WorkflowRunValidationError,
)


router = APIRouter()


def _build_response(report: ReadinessReport) -> ReadinessResponse:
    return ReadinessResponse(
        status="ready" if report.is_ready else "not_ready",
        mode=report.mode,
        checks={
            name: ReadinessCheck(
                status=check.status,
                detail=check.detail,
                failure_reason=check.failure_reason,
            )
            for name, check in report.checks.items()
        },
    )


@router.get("/ready", response_model=ReadinessResponse)
def readiness(
    readiness_service: ReadinessService = Depends(get_readiness_service),
) -> ReadinessResponse:
    response = _build_response(readiness_service.check())
    if response.status != "ready":
        payload = (
            response.model_dump()
            if hasattr(response, "model_dump")
            else response.dict()
        )
        return JSONResponse(status_code=503, content=payload)
    return response


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(metrics_service: MetricsService = Depends(get_metrics_service)) -> PlainTextResponse:
    try:
        payload = metrics_service.render_prometheus()
    except Exception:
        # Scrapes must not expose database or driver exception text.
        return PlainTextResponse(
            "# HELP learnloop_metrics_collection_failed Metrics collection failed.\n"
            "# TYPE learnloop_metrics_collection_failed gauge\n"
            "learnloop_metrics_collection_failed 1\n",
            status_code=503,
            media_type="text/plain; version=0.0.4",
        )
    return PlainTextResponse(payload, media_type="text/plain; version=0.0.4")


@router.get(
    "/api/ops/workflows",
    response_model=WorkflowStatusListResponse,
)
def list_workflows(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    _: None = Depends(require_api_bearer_token),
    observability_service: WorkflowObservabilityService = Depends(
        get_workflow_observability_service
    ),
) -> WorkflowStatusListResponse:
    try:
        workflows = observability_service.list_workflows(status=status, limit=limit)
    except ValueError as exc:
        raise _operator_http_error(
            error_code="INVALID_ARGUMENT",
            message="Invalid workflow status filter",
            status_code=400,
        ) from exc
    return WorkflowStatusListResponse(
        workflows=[_to_workflow_response(workflow) for workflow in workflows]
    )


@router.get(
    "/api/ops/workflows/{workflow_run_id}",
    response_model=WorkflowStatusResponse,
)
def get_workflow(
    workflow_run_id: int,
    _: None = Depends(require_api_bearer_token),
    observability_service: WorkflowObservabilityService = Depends(
        get_workflow_observability_service
    ),
) -> WorkflowStatusResponse:
    workflow = observability_service.get_workflow(workflow_run_id)
    if workflow is None:
        raise _operator_http_error(
            error_code="WORKFLOW_NOT_FOUND",
            message="Workflow run is not found",
            status_code=404,
        )
    return _to_workflow_response(workflow)


@router.post(
    "/api/ops/workflows/{workflow_run_id}/reconcile",
    response_model=WorkflowStatusResponse,
)
def reconcile_workflow(
    workflow_run_id: int,
    payload: WorkflowReconcileRequest,
    _: None = Depends(require_api_bearer_token),
    observability_service: WorkflowObservabilityService = Depends(
        get_workflow_observability_service
    ),
) -> WorkflowStatusResponse:
    try:
        workflow = observability_service.reconcile_workflow(
            workflow_run_id,
            status=payload.status,
            failure_reason=payload.failure_reason,
        )
    except WorkflowRunNotFoundError as exc:
        raise _operator_http_error(
            error_code="WORKFLOW_NOT_FOUND",
            message="Workflow run is not found",
            status_code=404,
        ) from exc
    except (ValueError, WorkflowRunValidationError) as exc:
        raise _operator_http_error(
            error_code="WORKFLOW_RECONCILIATION_CONFLICT",
            message="Workflow run cannot be reconciled in its current state",
            status_code=409,
        ) from exc
    return _to_workflow_response(workflow)


@router.get(
    "/api/ops/cost",
    response_model=CostBudgetResponse,
)
def cost_budget(
    _: None = Depends(require_api_bearer_token),
    observability_service: WorkflowObservabilityService = Depends(
        get_workflow_observability_service
    ),
) -> CostBudgetResponse:
    snapshot = observability_service.cost_snapshot(limit=10000)
    return CostBudgetResponse(
        daily_cost_usd=snapshot.daily_cost_usd,
        daily_budget_usd=snapshot.daily_budget_usd,
        daily_status=snapshot.daily_status,
        unknown_cost_workflow_count=snapshot.unknown_cost_workflow_count,
        workflow_budget_exceeded_count=snapshot.workflow_budget_exceeded_count,
        workflow_budget_usd=snapshot.workflow_budget_usd,
    )


def _to_workflow_response(workflow) -> WorkflowStatusResponse:
    return WorkflowStatusResponse(
        workflow_run_id=workflow.workflow_run_id,
        workflow_type=workflow.workflow_type,
        status=workflow.status,
        failure_reason=workflow.failure_reason,
        started_at=workflow.started_at,
        finished_at=workflow.finished_at,
        age_seconds=workflow.age_seconds,
        stale=workflow.stale,
        estimated_cost_usd=workflow.estimated_cost_usd,
        metadata=workflow.metadata,
    )


def _operator_http_error(*, error_code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error_code": error_code,
            "message": message,
            "failure_reason": "UNKNOWN_ERROR",
            "workflow_run_id": None,
        },
    )
