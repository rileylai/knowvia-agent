from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional, Set, TypeVar

from sqlalchemy.orm import Session

from src.db.models import WorkflowRun
from src.observability.logger import get_logger
from src.repositories import WorkflowRunRepository

WORKFLOW_STATUS_RUNNING = "running"
WORKFLOW_STATUS_SUCCEEDED = "succeeded"
WORKFLOW_STATUS_FAILED = "failed"

STANDARD_FAILURE_REASONS: Set[str] = {
    "NOTION_AUTH_FAILED",
    "NOTION_PAGE_NOT_FOUND",
    "NOTION_BLOCK_FETCH_FAILED",
    "NOTION_APPEND_NOT_VERIFIED",
    "STALE_PAGE_SNAPSHOT",
    "OCR_FAILED",
    "PDF_PARSE_FAILED",
    "URL_FETCH_FAILED",
    "URL_SSRF_BLOCKED",
    "URL_DNS_RESOLUTION_FAILED",
    "URL_REDIRECT_LIMIT_EXCEEDED",
    "URL_RESPONSE_TYPE_UNSUPPORTED",
    "URL_RESPONSE_TOO_LARGE",
    "YOUTUBE_TRANSCRIPT_NOT_FOUND",
    "PROVIDER_NOT_FOUND",
    "LLM_PROVIDER_ERROR",
    "LLM_OUTPUT_INVALID",
    "EMBEDDING_PROVIDER_NOT_CONFIGURED",
    "EMBEDDING_PROVIDER_ERROR",
    "VECTOR_DIMENSION_MISMATCH",
    "VECTOR_QUERY_FAILED",
    "VECTOR_UPSERT_FAILED",
    "CHANGE_REQUEST_NOT_FOUND",
    "WRITE_POLICY_VIOLATION",
    "DUPLICATE_SOURCE",
    "SYNTHETIC_DATA_NOT_ALLOWED",
    "TELEGRAM_NOT_CONFIGURED",
    "TELEGRAM_SEND_FAILED",
    "TELEGRAM_CALLBACK_ACK_FAILED",
    "TELEGRAM_PREVIEW_DELIVERY_FAILED",
    "TELEGRAM_FILE_DOWNLOAD_FAILED",
    "INVALID_ARGUMENT",
    "INVALID_CALLBACK",
    "UPLOAD_MEDIA_MISSING",
    "UPLOAD_SESSION_EXPIRED",
    "UPLOAD_SESSION_INVALID",
    "INVALID_UPLOAD_TYPE",
    "INVALID_UPLOAD_MIME",
    "EMPTY_UPLOAD",
    "UPLOAD_LIMIT_EXCEEDED",
    "UPLOAD_TOO_LARGE",
    "PDF_PAGE_LIMIT_EXCEEDED",
    "IMAGE_PIXEL_LIMIT_EXCEEDED",
    "INVALID_IMAGE",
    "EXTRACTED_TEXT_LIMIT_EXCEEDED",
    "TELEGRAM_QUEUE_UNAVAILABLE",
    "QUEUE_JOB_TIMEOUT",
    "REDIS_URL_NOT_CONFIGURED",
    "REDIS_UNAVAILABLE",
    "AUTHENTICATION_FAILED",
    "AUTHORIZATION_FAILED",
    "TELEGRAM_UPDATE_LEDGER_FAILED",
    "IDEMPOTENCY_KEY_CONFLICT",
    "IDEMPOTENCY_IN_PROGRESS",
    "IDEMPOTENCY_STORE_FAILED",
    "WORKFLOW_AUDIT_UPDATE_FAILED",
    "UNKNOWN_ERROR",
}

T = TypeVar("T")
SessionFactory = Callable[[], Session]


class WorkflowRunServiceError(Exception):
    pass


class WorkflowRunValidationError(WorkflowRunServiceError):
    pass


class WorkflowRunAuditUpdateError(WorkflowRunServiceError):
    def __init__(self, *, workflow_run_id: int, action: str) -> None:
        super().__init__(
            "Workflow audit update failed: "
            f"workflow_run_id={workflow_run_id} action={action}"
        )
        self.workflow_run_id = workflow_run_id
        self.action = action
        self.error_code = "WORKFLOW_AUDIT_UPDATE_FAILED"
        self.failure_reason = "WORKFLOW_AUDIT_UPDATE_FAILED"
        self.http_status_code = 503


class WorkflowRunNotFoundError(WorkflowRunServiceError):
    def __init__(self, *, workflow_run_id: int, action: str) -> None:
        super().__init__(
            "Workflow run is not found: "
            f"workflow_run_id={workflow_run_id} action={action}"
        )
        self.workflow_run_id = workflow_run_id
        self.action = action


class WorkflowRunService:
    def __init__(
        self,
        session_factory: SessionFactory,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._session_factory = session_factory
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._logger = get_logger("learnloop.workflow")

    def _with_repository(self, operation: Callable[[WorkflowRunRepository], T]) -> T:
        session = self._session_factory()
        try:
            return operation(WorkflowRunRepository(session))
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def start_workflow(
        self,
        *,
        workflow_type: str,
        metadata_json: Optional[str] = None,
    ) -> WorkflowRun:
        normalized_type = workflow_type.strip()
        if not normalized_type:
            raise WorkflowRunValidationError("workflow_type must not be empty")

        return self._with_repository(
            lambda repository: repository.create_workflow_run(
                workflow_type=normalized_type,
                status=WORKFLOW_STATUS_RUNNING,
                metadata_json=metadata_json,
            )
        )

    def get_workflow_run(self, workflow_run_id: int) -> Optional[WorkflowRun]:
        return self._with_repository(
            lambda repository: repository.get_workflow_run_by_id(workflow_run_id)
        )

    def get_latest_workflow_run(self, *, workflow_type: str) -> Optional[WorkflowRun]:
        normalized_type = workflow_type.strip()
        if not normalized_type:
            raise WorkflowRunValidationError("workflow_type must not be empty")
        return self._with_repository(
            lambda repository: repository.get_latest_workflow_run(
                workflow_type=normalized_type
            )
        )

    def mark_workflow_succeeded(
        self,
        workflow_run_id: int,
        *,
        metadata_json: Optional[str] = None,
    ) -> WorkflowRun:
        try:
            workflow_run = self._with_repository(
                lambda repository: repository.update_workflow_run(
                    workflow_run_id,
                    status=WORKFLOW_STATUS_SUCCEEDED,
                    failure_reason=None,
                    metadata_json=metadata_json,
                    finished_at=self._now_provider(),
                )
            )
        except Exception as exc:
            self._log_audit_update_failure(
                workflow_run_id=workflow_run_id,
                action="mark_succeeded",
            )
            raise WorkflowRunAuditUpdateError(
                workflow_run_id=workflow_run_id,
                action="mark_succeeded",
            ) from exc
        if workflow_run is None:
            self._log_audit_update_failure(
                workflow_run_id=workflow_run_id,
                action="mark_succeeded_not_found",
            )
            raise WorkflowRunNotFoundError(
                workflow_run_id=workflow_run_id,
                action="mark_succeeded_not_found",
            )
        return workflow_run

    def mark_workflow_failed(
        self,
        workflow_run_id: int,
        *,
        failure_reason: str,
        metadata_json: Optional[str] = None,
    ) -> Optional[WorkflowRun]:
        normalized_failure_reason = failure_reason.strip().upper()
        if normalized_failure_reason not in STANDARD_FAILURE_REASONS:
            raise WorkflowRunValidationError(
                f"failure_reason is invalid: '{failure_reason}'"
            )

        try:
            workflow_run = self._with_repository(
                lambda repository: repository.update_workflow_run(
                    workflow_run_id,
                    status=WORKFLOW_STATUS_FAILED,
                    failure_reason=normalized_failure_reason,
                    metadata_json=metadata_json,
                    finished_at=self._now_provider(),
                )
            )
        except Exception:
            self._log_audit_update_failure(
                workflow_run_id=workflow_run_id,
                action="mark_failed",
            )
            return None
        if workflow_run is None:
            self._log_audit_update_failure(
                workflow_run_id=workflow_run_id,
                action="mark_failed_not_found",
            )
            return None
        return workflow_run

    def reconcile_stale_running_workflow(
        self,
        workflow_run_id: int,
        *,
        status: str,
        metadata_json: Optional[str] = None,
        failure_reason: Optional[str] = None,
        stale_before: Optional[datetime] = None,
    ) -> WorkflowRun:
        normalized_status = status.strip().lower()
        if normalized_status not in {
            WORKFLOW_STATUS_SUCCEEDED,
            WORKFLOW_STATUS_FAILED,
        }:
            raise WorkflowRunValidationError(
                "reconciliation status must be succeeded or failed"
            )
        normalized_failure_reason: Optional[str] = None
        if failure_reason is not None:
            normalized_failure_reason = failure_reason.strip().upper()
            if normalized_failure_reason not in STANDARD_FAILURE_REASONS:
                raise WorkflowRunValidationError(
                    f"failure_reason is invalid: '{failure_reason}'"
                )
        if normalized_status == WORKFLOW_STATUS_FAILED and normalized_failure_reason is None:
            raise WorkflowRunValidationError(
                "failed reconciliation requires failure_reason"
            )
        if normalized_status == WORKFLOW_STATUS_SUCCEEDED and normalized_failure_reason is not None:
            raise WorkflowRunValidationError(
                "succeeded reconciliation must not include failure_reason"
            )

        def reconcile(repository: WorkflowRunRepository) -> Optional[WorkflowRun]:
            workflow_run = repository.get_workflow_run_by_id(workflow_run_id)
            if workflow_run is None:
                raise WorkflowRunNotFoundError(
                    workflow_run_id=workflow_run_id,
                    action="reconcile_not_found",
                )
            if workflow_run.status != WORKFLOW_STATUS_RUNNING:
                raise WorkflowRunValidationError(
                    "Only running workflow runs can be reconciled"
                )
            if stale_before is not None:
                started_at = workflow_run.started_at
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                normalized_stale_before = stale_before
                if normalized_stale_before.tzinfo is None:
                    normalized_stale_before = normalized_stale_before.replace(
                        tzinfo=timezone.utc
                    )
                if started_at > normalized_stale_before:
                    raise WorkflowRunValidationError(
                        "Only stale running workflow runs can be reconciled"
                    )
            return repository.update_workflow_run(
                workflow_run_id,
                status=normalized_status,
                failure_reason=normalized_failure_reason,
                metadata_json=metadata_json,
                finished_at=self._now_provider(),
            )

        try:
            workflow_run = self._with_repository(reconcile)
        except WorkflowRunServiceError:
            raise
        except Exception as exc:
            self._log_audit_update_failure(
                workflow_run_id=workflow_run_id,
                action="reconcile",
            )
            raise WorkflowRunAuditUpdateError(
                workflow_run_id=workflow_run_id,
                action="reconcile",
            ) from exc
        if workflow_run is None:
            self._log_audit_update_failure(
                workflow_run_id=workflow_run_id,
                action="reconcile_not_found",
            )
            raise WorkflowRunNotFoundError(
                workflow_run_id=workflow_run_id,
                action="reconcile_not_found",
            )
        return workflow_run

    def _log_audit_update_failure(
        self,
        *,
        workflow_run_id: int,
        action: str,
    ) -> None:
        self._logger.error(
            "workflow_audit_update_failed",
            extra={
                "workflow_id": str(workflow_run_id),
                "audit_action": action,
                "audit_status": WORKFLOW_STATUS_RUNNING,
            },
        )
