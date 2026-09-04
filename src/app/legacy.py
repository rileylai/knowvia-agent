"""Opt-in LearnLoop compatibility application for legacy API verification."""

from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from src.app.api import (
    notion_index_router,
    ops_router,
    qa_router,
    source_ingest_router,
    supplement_router,
    telegram_router,
)
from src.app.api_idempotency import api_idempotency_middleware
from src.app.config import get_settings
from src.app.dependencies import require_api_bearer_token
from src.db.session import get_db_session_factory
from src.observability.logger import configure_logging, get_logger
from src.services import ApiIdempotencyService, WorkflowRunAuditUpdateError


def create_legacy_app() -> FastAPI:
    """Create the isolated compatibility app; active runtime never imports it."""

    settings = get_settings()
    configure_logging(settings.log_level)
    request_logger = get_logger("learnloop.request")
    legacy_app = FastAPI(title="LearnLoop Agent Legacy Compatibility")
    legacy_app.state.api_idempotency_service = ApiIdempotencyService(
        get_db_session_factory()
    )
    protected_api_dependency = [Depends(require_api_bearer_token)]

    legacy_app.include_router(notion_index_router, dependencies=protected_api_dependency)
    legacy_app.include_router(ops_router)
    legacy_app.include_router(qa_router, dependencies=protected_api_dependency)
    legacy_app.include_router(source_ingest_router, dependencies=protected_api_dependency)
    legacy_app.include_router(supplement_router, dependencies=protected_api_dependency)
    legacy_app.include_router(telegram_router)
    legacy_app.middleware("http")(api_idempotency_middleware)

    @legacy_app.exception_handler(WorkflowRunAuditUpdateError)
    async def workflow_run_audit_update_exception_handler(
        request: Request,
        exc: WorkflowRunAuditUpdateError,
    ) -> JSONResponse:
        _ = request
        return JSONResponse(
            status_code=exc.http_status_code,
            content={
                "error_code": exc.error_code,
                "message": "Workflow audit update failed after business work completed",
                "failure_reason": exc.failure_reason,
                "workflow_run_id": exc.workflow_run_id,
            },
        )

    @legacy_app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        workflow_id = request.headers.get("X-Workflow-ID") or str(uuid4())
        request.state.workflow_id = workflow_id
        start_time = perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration_ms = round((perf_counter() - start_time) * 1000, 2)
            request_logger.exception(
                "request_failed",
                extra={
                    "workflow_id": workflow_id,
                    "path": request.url.path,
                    "method": request.method,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            raise

        duration_ms = round((perf_counter() - start_time) * 1000, 2)
        request_logger.info(
            "request_completed",
            extra={
                "workflow_id": workflow_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Workflow-ID"] = workflow_id
        return response

    @legacy_app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return legacy_app


app = create_legacy_app()
