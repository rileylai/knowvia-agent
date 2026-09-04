from __future__ import annotations

import secrets
import shlex
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Optional

from src.orchestrators.notion_full_index_orchestrator import (
    NotionFullIndexOrchestrator,
)
from src.orchestrators.notion_page_index_orchestrator import NotionPageIndexError
from src.queue import QueueClient, get_callable_import_path
from src.services import (
    TelegramFullIndexSession,
    TelegramIndexSessionStore,
    WorkflowObservabilityService,
    WorkflowRunService,
)
from src.services.workflow_observability import WorkflowStatusView


@dataclass(frozen=True)
class TelegramFullIndexView:
    session_id: str
    state: str
    reply_text: str


@dataclass(frozen=True)
class TelegramIndexResult:
    status: str
    reply_text: str
    workflow_run_id: Optional[int]
    discovered_page_count: int
    processed_page_count: int
    failed_page_count: int
    remaining_page_count: int
    failure_reason: Optional[str] = None
    estimated_cost_usd: Optional[float] = None
    stale: Optional[bool] = None


class TelegramIndexError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason
        self.metadata = metadata or {}


class TelegramIndexOrchestrator:
    """Own confirmation-gated full indexing and read-only index status."""

    def __init__(
        self,
        *,
        full_index_orchestrator: NotionFullIndexOrchestrator,
        index_session_store: TelegramIndexSessionStore,
        workflow_run_service: WorkflowRunService,
        workflow_observability_service: WorkflowObservabilityService,
        queue_client: Optional[QueueClient] = None,
        indexing_job_timeout_seconds: int = 10800,
    ) -> None:
        self._full_index_orchestrator = full_index_orchestrator
        self._index_session_store = index_session_store
        self._workflow_run_service = workflow_run_service
        self._workflow_observability_service = workflow_observability_service
        self._queue_client = queue_client
        self._indexing_job_timeout_seconds = indexing_job_timeout_seconds

    def start_full_index_session(
        self,
        *,
        chat_id: str,
        user_id: str,
    ) -> TelegramFullIndexView:
        session = self._index_session_store.create_full_index_session(
            session_id=self._new_session_id(),
            chat_id=chat_id,
            user_id=user_id,
        )
        return TelegramFullIndexView(
            session_id=session.session_id,
            state=session.state,
            reply_text=(
                "⚠️ Full Notion index confirmation\n\n"
                "This will read all currently accessible Notion pages and may "
                "take time or consume embedding quota.\n"
                "Duration: depends on page count. Embedding cost estimate: unknown "
                "until the run records provider usage.\n\n"
                "No Notion content will be written. Confirm only if you want to "
                "replace the complete derived index."
            ),
        )

    async def confirm_full_index(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        request_workflow_id: str,
    ) -> TelegramIndexResult:
        claim_status, session = self._index_session_store.claim_full_index(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        if session is None or claim_status == "missing":
            raise self._session_error()
        if claim_status != "claimed":
            return self._session_result(
                session,
                reply_text=(
                    "This full index request is already being processed or has "
                    "finished. Use /index-status to inspect it."
                ),
            )

        if self._queue_client is not None:
            return self._enqueue_full_index(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                request_workflow_id=request_workflow_id,
            )

        try:
            result = await self._full_index_orchestrator.index_all(
                request_workflow_id=request_workflow_id,
            )
        except NotionPageIndexError as exc:
            status_view = self._get_status_view(exc.workflow_run_id)
            metrics = self._metrics_from_view(status_view)
            state = "partially_failed" if metrics[1] else "failed"
            self._index_session_store.complete_full_index(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                state=state,
                workflow_run_id=exc.workflow_run_id,
                discovered_page_count=metrics[0],
                processed_page_count=metrics[1],
                failed_page_count=metrics[2],
                remaining_page_count=metrics[3],
                failure_reason=exc.failure_reason,
            )
            return TelegramIndexResult(
                status=state,
                reply_text=self._failure_reply(
                    state=state,
                    processed_page_count=metrics[1],
                    failed_page_count=metrics[2],
                    remaining_page_count=metrics[3],
                ),
                workflow_run_id=exc.workflow_run_id,
                discovered_page_count=metrics[0],
                processed_page_count=metrics[1],
                failed_page_count=metrics[2],
                remaining_page_count=metrics[3],
                failure_reason=exc.failure_reason,
                estimated_cost_usd=status_view.estimated_cost_usd if status_view else None,
                stale=status_view.stale if status_view else None,
            )

        status_view = self._get_status_view(result.workflow_run_id)
        estimated_cost = status_view.estimated_cost_usd if status_view else None
        self._index_session_store.complete_full_index(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            state="succeeded",
            workflow_run_id=result.workflow_run_id,
            discovered_page_count=result.discovered_page_count,
            processed_page_count=result.processed_page_count,
            failed_page_count=0,
            remaining_page_count=0,
        )
        return TelegramIndexResult(
            status="succeeded",
            reply_text=(
                f"Full Notion index completed: {result.processed_page_count}/"
                f"{result.discovered_page_count} page(s) processed. "
                f"Embedding cost: {self._format_cost(estimated_cost)}."
            ),
            workflow_run_id=result.workflow_run_id,
            discovered_page_count=result.discovered_page_count,
            processed_page_count=result.processed_page_count,
            failed_page_count=0,
            remaining_page_count=0,
            estimated_cost_usd=estimated_cost,
            stale=status_view.stale if status_view else None,
        )

    async def run_full_index_job(
        self,
        *,
        workflow_run_id: int,
        request_workflow_id: str,
    ):
        """Run a previously created durable full-index workflow."""

        return await self._full_index_orchestrator.index_all(
            request_workflow_id=request_workflow_id,
            workflow_run_id=workflow_run_id,
        )

    def _enqueue_full_index(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        request_workflow_id: str,
    ) -> TelegramIndexResult:
        workflow_run_id = self._full_index_orchestrator.start_full_index_workflow(
            request_workflow_id=request_workflow_id,
        )
        try:
            from src.worker.telegram import (
                TELEGRAM_FULL_INDEX_JOB_PATH,
                process_telegram_full_index_job,
            )

            if (
                get_callable_import_path(process_telegram_full_index_job)
                != TELEGRAM_FULL_INDEX_JOB_PATH
            ):
                raise RuntimeError("Telegram full-index worker path is not canonical")

            self._index_session_store.mark_full_index_queued(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                workflow_run_id=workflow_run_id,
            )
            self._queue_client.enqueue(
                queue_name="telegram",
                function=process_telegram_full_index_job,
                args=(workflow_run_id, request_workflow_id),
                description="Run one durable Telegram full-index workflow",
                timeout_seconds=self._indexing_job_timeout_seconds,
            )
        except Exception as exc:
            self._workflow_run_service.mark_workflow_failed(
                workflow_run_id,
                failure_reason="TELEGRAM_QUEUE_UNAVAILABLE",
                metadata_json=json.dumps(
                    {
                        "operation": "index_full",
                        "error_code": "TELEGRAM_QUEUE_UNAVAILABLE",
                    },
                    sort_keys=True,
                ),
            )
            self._index_session_store.complete_full_index(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                state="failed",
                workflow_run_id=workflow_run_id,
                discovered_page_count=0,
                processed_page_count=0,
                failed_page_count=0,
                remaining_page_count=0,
                failure_reason="TELEGRAM_QUEUE_UNAVAILABLE",
            )
            raise TelegramIndexError(
                error_code="TELEGRAM_QUEUE_UNAVAILABLE",
                message="Telegram full-index background queue is unavailable",
                http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                failure_reason="TELEGRAM_QUEUE_UNAVAILABLE",
                metadata={
                    "index_workflow_run_id": workflow_run_id,
                    "index_status": "failed",
                },
            ) from exc

        return TelegramIndexResult(
            status="running",
            reply_text=(
                f"Full Notion index queued as workflow #{workflow_run_id}. "
                "Use /index-status "
                f"{workflow_run_id} to follow progress."
            ),
            workflow_run_id=workflow_run_id,
            discovered_page_count=0,
            processed_page_count=0,
            failed_page_count=0,
            remaining_page_count=0,
        )

    def cancel_full_index(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> TelegramIndexResult:
        session = self._index_session_store.cancel_full_index(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        if session is None:
            raise self._session_error()
        return self._session_result(
            session,
            reply_text="Full Notion index cancelled. No index work was started.",
        )

    def get_index_status(self, *, command_text: str) -> TelegramIndexResult:
        workflow_run_id = self._parse_workflow_id(command_text)
        if workflow_run_id is None:
            latest = self._workflow_run_service.get_latest_workflow_run(
                workflow_type="indexing"
            )
            if latest is None:
                raise TelegramIndexError(
                error_code="WORKFLOW_NOT_FOUND",
                message="No Notion index workflow run was found.",
                http_status_code=HTTPStatus.NOT_FOUND,
                failure_reason="UNKNOWN_ERROR",
                )
            workflow_run_id = int(latest.id)
        status_view = self._get_status_view(workflow_run_id)
        if status_view is None or status_view.workflow_type != "indexing":
            raise TelegramIndexError(
                error_code="WORKFLOW_NOT_FOUND",
                message="The requested Notion index workflow run was not found.",
                http_status_code=HTTPStatus.NOT_FOUND,
                failure_reason="UNKNOWN_ERROR",
            )
        discovered, processed, failed, remaining = self._metrics_from_view(status_view)
        return TelegramIndexResult(
            status=status_view.status,
            reply_text=self._status_reply(
                status_view=status_view,
                discovered_page_count=discovered,
                processed_page_count=processed,
                failed_page_count=failed,
                remaining_page_count=remaining,
            ),
            workflow_run_id=status_view.workflow_run_id,
            discovered_page_count=discovered,
            processed_page_count=processed,
            failed_page_count=failed,
            remaining_page_count=remaining,
            failure_reason=status_view.failure_reason,
            estimated_cost_usd=status_view.estimated_cost_usd,
            stale=status_view.stale,
        )

    def _get_status_view(self, workflow_run_id: Optional[int]) -> Optional[WorkflowStatusView]:
        if workflow_run_id is None:
            return None
        return self._workflow_observability_service.get_workflow(workflow_run_id)

    @staticmethod
    def _metrics_from_view(
        status_view: Optional[WorkflowStatusView],
    ) -> tuple[int, int, int, int]:
        if status_view is None:
            return 0, 0, 0, 0
        metadata = status_view.metadata
        discovered = TelegramIndexOrchestrator._metadata_int(
            metadata,
            "discovered_page_count",
        )
        processed = TelegramIndexOrchestrator._metadata_int(
            metadata,
            "processed_page_count",
        )
        if not discovered:
            page_ids = metadata.get("page_ids")
            if isinstance(page_ids, list):
                discovered = len(page_ids)
        failed = 1 if metadata.get("failed_page_id") else 0
        remaining = TelegramIndexOrchestrator._metadata_int(
            metadata,
            "remaining_page_count",
        )
        if not remaining:
            remaining_ids = metadata.get("remaining_page_ids")
            if isinstance(remaining_ids, list):
                remaining = len(remaining_ids)
        if status_view.status == "succeeded":
            remaining = 0
        return discovered, processed, failed, remaining

    @staticmethod
    def _metadata_int(metadata: dict[str, Any], key: str) -> int:
        try:
            return max(0, int(metadata.get(key, 0) or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _status_reply(
        *,
        status_view: WorkflowStatusView,
        discovered_page_count: int,
        processed_page_count: int,
        failed_page_count: int,
        remaining_page_count: int,
    ) -> str:
        lines = [
            f"Notion index workflow #{status_view.workflow_run_id}",
            f"Status: {status_view.status}",
            f"Pages: {processed_page_count}/{discovered_page_count} processed",
            f"Failed pages: {failed_page_count}",
            f"Remaining pages: {remaining_page_count}",
            f"Embedding cost: {TelegramIndexOrchestrator._format_cost(status_view.estimated_cost_usd)}",
            f"Stale: {'yes' if status_view.stale else 'no'}",
        ]
        if status_view.failure_reason:
            lines.append(f"Failure reason: {status_view.failure_reason}")
        return "\n".join(lines)

    @staticmethod
    def _failure_reply(
        *,
        state: str,
        processed_page_count: int,
        failed_page_count: int,
        remaining_page_count: int,
    ) -> str:
        if state == "partially_failed":
            return (
                "Full Notion index partially completed: "
                f"{processed_page_count} page(s) processed, "
                f"{failed_page_count} failed, {remaining_page_count} remaining. "
                "Completed page commits were preserved. Use /index-status for details."
            )
        return (
            "Full Notion index failed before any page was committed. "
            "Use /index-status for details."
        )

    @staticmethod
    def _session_result(
        session: TelegramFullIndexSession,
        *,
        reply_text: str,
    ) -> TelegramIndexResult:
        return TelegramIndexResult(
            status=session.state,
            reply_text=reply_text,
            workflow_run_id=session.workflow_run_id,
            discovered_page_count=session.discovered_page_count,
            processed_page_count=session.processed_page_count,
            failed_page_count=session.failed_page_count,
            remaining_page_count=session.remaining_page_count,
            failure_reason=session.failure_reason,
        )

    @staticmethod
    def _format_cost(cost: Optional[float]) -> str:
        return f"${cost:.6f}" if cost is not None else "unknown"

    @staticmethod
    def _new_session_id() -> str:
        return f"index-full-{secrets.token_urlsafe(12)}"

    @staticmethod
    def _session_error() -> TelegramIndexError:
        return TelegramIndexError(
            error_code="INVALID_CALLBACK",
            message="This full-index confirmation is invalid or expired. Use /index-full to start again.",
            http_status_code=HTTPStatus.GONE,
            failure_reason="INVALID_CALLBACK",
        )

    @staticmethod
    def _parse_workflow_id(command_text: str) -> Optional[int]:
        try:
            tokens = shlex.split(command_text)
        except ValueError as exc:
            raise TelegramIndexError(
                error_code="INVALID_ARGUMENT",
                message="Usage: /index-status [workflow_id]",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            ) from exc
        if len(tokens) > 2:
            raise TelegramIndexError(
                error_code="INVALID_ARGUMENT",
                message="Usage: /index-status [workflow_id]",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            )
        if len(tokens) == 1:
            return None
        try:
            workflow_run_id = int(tokens[1])
        except ValueError as exc:
            raise TelegramIndexError(
                error_code="INVALID_ARGUMENT",
                message="workflow_id must be a positive integer",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            ) from exc
        if workflow_run_id <= 0:
            raise TelegramIndexError(
                error_code="INVALID_ARGUMENT",
                message="workflow_id must be a positive integer",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            )
        return workflow_run_id
