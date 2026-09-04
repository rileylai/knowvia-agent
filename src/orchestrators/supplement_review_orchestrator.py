from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.orchestrators.notion_page_index_orchestrator import (
    SYNC_MODE_AUTO_AFTER_ACCEPT,
    NotionPageIndexError,
    NotionPageIndexOrchestrator,
)
from src.orchestrators.supplement_proposal_schema import (
    SupplementProposalValidationError,
    parse_supplement_proposal_json,
)
from src.repositories import ChangeRequestRepository, NotionPageRepository
from src.services import (
    InfrastructureExecutionTimeout,
    STANDARD_FAILURE_REASONS,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
)
from src.tools import ToolContext, ToolRegistry

CHANGE_REQUEST_STATUS_PENDING = "pending"
CHANGE_REQUEST_STATUS_ACCEPTED = "accepted"
CHANGE_REQUEST_STATUS_REJECTED = "rejected"
REVIEW_ACTION_ACCEPT = "accept"
REVIEW_ACTION_REJECT = "reject"
REVIEW_ACTION_EDIT_LATER = "edit_later"
NOTION_WRITER_TOOL_NAME = "notion_writer"


@dataclass
class SupplementReviewResult:
    workflow_run_id: int
    status: str
    change_request_id: int
    change_request_status: str
    review_action: str
    reviewer: Optional[str]
    reason: Optional[str]


@dataclass
class _AcceptMutationResult:
    change_request_id: int
    change_request_status: str
    follow_up_metadata: Dict[str, Any]


class SupplementReviewError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
        workflow_run_id: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason
        self.workflow_run_id = workflow_run_id


class SupplementReviewOrchestrator:
    def __init__(
        self,
        *,
        change_request_repository: ChangeRequestRepository,
        notion_page_repository: NotionPageRepository,
        unit_of_work_factory: UnitOfWorkFactory,
        tool_registry: ToolRegistry,
        page_index_orchestrator: NotionPageIndexOrchestrator,
        workflow_run_service: WorkflowRunService,
    ) -> None:
        self._change_request_repository = change_request_repository
        self._notion_page_repository = notion_page_repository
        self._unit_of_work_factory = unit_of_work_factory
        self._tool_registry = tool_registry
        self._page_index_orchestrator = page_index_orchestrator
        self._workflow_run_service = workflow_run_service

    async def accept_change_request(
        self,
        *,
        change_request_id: int,
        reviewer: Optional[str],
        request_workflow_id: str,
    ) -> SupplementReviewResult:
        return await self._execute_review_action(
            change_request_id=change_request_id,
            review_action=REVIEW_ACTION_ACCEPT,
            reviewer=reviewer,
            reason=None,
            request_workflow_id=request_workflow_id,
        )

    def change_target(
        self,
        *,
        change_request_id: int,
        target_notion_page_id: str,
        reviewer: Optional[str],
        request_workflow_id: str,
    ) -> SupplementReviewResult:
        if change_request_id <= 0 or not target_notion_page_id.strip():
            raise SupplementReviewError(
                error_code="INVALID_ARGUMENT",
                message="change_request_id and target_notion_page_id are required",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="supplement",
            metadata_json=json.dumps(
                {
                    "operation": "change_pending_target",
                    "change_request_id": change_request_id,
                    "reviewer": reviewer,
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )
        try:
            page = self._notion_page_repository.get_by_notion_page_id(
                target_notion_page_id.strip()
            )
            if page is None:
                raise SupplementReviewError(
                    error_code="NOTION_PAGE_NOT_FOUND",
                    message="The selected Notion page is not indexed.",
                    http_status_code=HTTPStatus.NOT_FOUND,
                    failure_reason="NOTION_PAGE_NOT_FOUND",
                )
            with self._unit_of_work_factory() as unit_of_work:
                updated = unit_of_work.change_requests.update_pending_target(
                    change_request_id,
                    target_notion_page_id=int(page.id),
                )
                if updated is None:
                    raise SupplementReviewError(
                        error_code="CHANGE_REQUEST_NOT_FOUND",
                        message="Change request is not found.",
                        http_status_code=HTTPStatus.NOT_FOUND,
                        failure_reason="CHANGE_REQUEST_NOT_FOUND",
                    )
                if updated.status.strip().lower() != CHANGE_REQUEST_STATUS_PENDING:
                    raise SupplementReviewError(
                        error_code="INVALID_STATE_TRANSITION",
                        message="Only pending proposals can change target.",
                        http_status_code=HTTPStatus.CONFLICT,
                        failure_reason="INVALID_STATE_TRANSITION",
                    )
                result_id = int(updated.id)
                result_status = updated.status
            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    {
                        "operation": "change_pending_target",
                        "change_request_id": result_id,
                        "change_request_status": result_status,
                    },
                    sort_keys=True,
                ),
            )
            return SupplementReviewResult(
                workflow_run_id=workflow_run.id,
                status="succeeded",
                change_request_id=result_id,
                change_request_status=result_status,
                review_action="change_target",
                reviewer=reviewer,
                reason=None,
            )
        except WorkflowRunAuditUpdateError:
            raise
        except SupplementReviewError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                review_action="change_target",
            )
            raise SupplementReviewError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=workflow_run.id,
            ) from exc

    async def reject_change_request(
        self,
        *,
        change_request_id: int,
        reviewer: Optional[str],
        reason: str,
        request_workflow_id: str,
    ) -> SupplementReviewResult:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise SupplementReviewError(
                error_code="INVALID_ARGUMENT",
                message="reason must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        return await self._execute_review_action(
            change_request_id=change_request_id,
            review_action=REVIEW_ACTION_REJECT,
            reviewer=reviewer,
            reason=normalized_reason,
            request_workflow_id=request_workflow_id,
        )

    async def mark_edit_later(
        self,
        *,
        change_request_id: int,
        reviewer: Optional[str],
        reason: Optional[str],
        request_workflow_id: str,
    ) -> SupplementReviewResult:
        normalized_reason: Optional[str] = None
        if reason is not None:
            candidate = reason.strip()
            if candidate:
                normalized_reason = candidate

        return await self._execute_review_action(
            change_request_id=change_request_id,
            review_action=REVIEW_ACTION_EDIT_LATER,
            reviewer=reviewer,
            reason=normalized_reason,
            request_workflow_id=request_workflow_id,
        )

    async def _execute_review_action(
        self,
        *,
        change_request_id: int,
        review_action: str,
        reviewer: Optional[str],
        reason: Optional[str],
        request_workflow_id: str,
    ) -> SupplementReviewResult:
        if change_request_id <= 0:
            raise SupplementReviewError(
                error_code="INVALID_ARGUMENT",
                message="change_request_id must be positive",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )

        normalized_reviewer: Optional[str] = None
        if reviewer is not None:
            candidate = reviewer.strip()
            if candidate:
                normalized_reviewer = candidate

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="supplement",
            metadata_json=json.dumps(
                {
                    "operation": "review_change_request",
                    "review_action": review_action,
                    "change_request_id": change_request_id,
                    "reviewer": normalized_reviewer,
                    "reason": reason,
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )

        try:
            change_request = self._change_request_repository.get_change_request_by_id(
                change_request_id
            )
            if change_request is None:
                raise SupplementReviewError(
                    error_code="CHANGE_REQUEST_NOT_FOUND",
                    message=(
                        "Change request is not found: "
                        f"change_request_id={change_request_id}"
                    ),
                    http_status_code=HTTPStatus.NOT_FOUND,
                    failure_reason="CHANGE_REQUEST_NOT_FOUND",
                )

            follow_up_metadata: Dict[str, Any] = {}
            if review_action == REVIEW_ACTION_ACCEPT:
                current_status = change_request.status.strip().lower()
                self._resolve_next_status(
                    review_action=review_action,
                    current_status=current_status,
                )
                accept_result = await self._append_and_reindex_after_accept(
                    change_request_id=change_request.id,
                    change_request_proposal_json=change_request.proposal_json,
                    target_notion_page_db_id=change_request.target_notion_page_id,
                    request_workflow_id=request_workflow_id,
                )
                follow_up_metadata = accept_result.follow_up_metadata
                result_change_request_id = accept_result.change_request_id
                result_change_request_status = accept_result.change_request_status
            else:
                with self._unit_of_work_factory() as unit_of_work:
                    change_request = unit_of_work.change_requests.get_change_request_by_id(
                        change_request_id
                    )
                    if change_request is None:
                        raise SupplementReviewError(
                            error_code="CHANGE_REQUEST_NOT_FOUND",
                            message=(
                                "Change request is not found: "
                                f"change_request_id={change_request_id}"
                            ),
                            http_status_code=HTTPStatus.NOT_FOUND,
                            failure_reason="CHANGE_REQUEST_NOT_FOUND",
                        )
                    current_status = change_request.status.strip().lower()
                    next_status = self._resolve_next_status(
                        review_action=review_action,
                        current_status=current_status,
                    )
                    updated = unit_of_work.change_requests.update_change_request_status(
                        change_request_id,
                        status=next_status,
                        failure_reason=None,
                    )
                    if updated is None:
                        raise SupplementReviewError(
                            error_code="CHANGE_REQUEST_NOT_FOUND",
                            message=(
                                "Change request is not found during update: "
                                f"change_request_id={change_request_id}"
                            ),
                            http_status_code=HTTPStatus.NOT_FOUND,
                            failure_reason="CHANGE_REQUEST_NOT_FOUND",
                        )
                    result_change_request_id = int(updated.id)
                    result_change_request_status = updated.status

            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    {
                        "operation": "review_change_request",
                        "review_action": review_action,
                        "change_request_id": result_change_request_id,
                        "change_request_status": result_change_request_status,
                        "reviewer": normalized_reviewer,
                        "reason": reason,
                        "follow_up": follow_up_metadata or None,
                    },
                    sort_keys=True,
                ),
            )

            return SupplementReviewResult(
                workflow_run_id=workflow_run.id,
                status="succeeded",
                change_request_id=result_change_request_id,
                change_request_status=result_change_request_status,
                review_action=review_action,
                reviewer=normalized_reviewer,
                reason=reason,
            )
        except WorkflowRunAuditUpdateError:
            raise
        except SupplementReviewError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                review_action=review_action,
            )
            raise SupplementReviewError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=workflow_run.id,
            ) from exc
        except InfrastructureExecutionTimeout:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="QUEUE_JOB_TIMEOUT",
                error_code="QUEUE_JOB_TIMEOUT",
                review_action=review_action,
            )
            raise SupplementReviewError(
                error_code="QUEUE_JOB_TIMEOUT",
                message="Review workflow timed out before completion",
                http_status_code=HTTPStatus.GATEWAY_TIMEOUT,
                failure_reason="QUEUE_JOB_TIMEOUT",
                workflow_run_id=workflow_run.id,
            ) from None
        except Exception as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                error_code="REVIEW_WORKFLOW_FAILED",
                review_action=review_action,
            )
            raise SupplementReviewError(
                error_code="REVIEW_WORKFLOW_FAILED",
                message=f"Failed to review change request: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

    async def _append_and_reindex_after_accept(
        self,
        *,
        change_request_id: int,
        change_request_proposal_json: str,
        target_notion_page_db_id: Optional[int],
        request_workflow_id: str,
    ) -> _AcceptMutationResult:
        if target_notion_page_db_id is None:
            raise SupplementReviewError(
                error_code="WRITE_POLICY_VIOLATION",
                message=(
                    "Accepted change request must include target_notion_page_id before "
                    "Notion append"
                ),
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="WRITE_POLICY_VIOLATION",
            )

        notion_page = self._notion_page_repository.get_by_id(target_notion_page_db_id)
        if notion_page is None:
            raise SupplementReviewError(
                error_code="NOTION_PAGE_NOT_FOUND",
                message=(
                    "Target Notion page is not found: "
                    f"target_notion_page_id={target_notion_page_db_id}"
                ),
                http_status_code=HTTPStatus.NOT_FOUND,
                failure_reason="NOTION_PAGE_NOT_FOUND",
            )

        try:
            proposal = parse_supplement_proposal_json(change_request_proposal_json)
        except SupplementProposalValidationError as exc:
            raise SupplementReviewError(
                error_code="INVALID_PROPOSAL_PAYLOAD",
                message=f"Stored proposal_json is invalid: {exc.message}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            ) from exc

        append_result = await self._append_to_ai_supplement_zone(
            page_id=notion_page.notion_page_id,
            change_request_id=change_request_id,
            request_workflow_id=request_workflow_id,
            topic_title=proposal.title,
            source_display_name=proposal.source.source_display_name,
            summary=proposal.summary,
            concepts=proposal.concepts,
            notes=proposal.notes,
        )

        index_workflow_id = self._page_index_orchestrator.start_indexing_workflow(
            page_id=notion_page.notion_page_id,
            request_workflow_id=request_workflow_id,
            sync_mode=SYNC_MODE_AUTO_AFTER_ACCEPT,
        )
        try:
            prepared_snapshot = await self._page_index_orchestrator.prepare_page_snapshot(
                page_id=notion_page.notion_page_id,
                request_workflow_id=request_workflow_id,
            )
        except NotionPageIndexError as exc:
            self._page_index_orchestrator.mark_indexing_workflow_failed(
                workflow_run_id=index_workflow_id,
                page_id=notion_page.notion_page_id,
                sync_mode=SYNC_MODE_AUTO_AFTER_ACCEPT,
                error_code=exc.error_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
            )
            raise SupplementReviewError(
                error_code="PAGE_REINDEX_FAILED",
                message=f"Failed to re-index appended page: {exc.message}",
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
            ) from exc
        except InfrastructureExecutionTimeout:
            self._page_index_orchestrator.mark_indexing_workflow_failed(
                workflow_run_id=index_workflow_id,
                page_id=notion_page.notion_page_id,
                sync_mode=SYNC_MODE_AUTO_AFTER_ACCEPT,
                error_code="QUEUE_JOB_TIMEOUT",
                failure_reason="QUEUE_JOB_TIMEOUT",
            )
            raise

        try:
            with self._unit_of_work_factory() as unit_of_work:
                locked_change_request = (
                    unit_of_work.change_requests.get_change_request_by_id_for_update(
                        change_request_id
                    )
                )
                if locked_change_request is None:
                    raise SupplementReviewError(
                        error_code="CHANGE_REQUEST_NOT_FOUND",
                        message=(
                            "Change request is not found during accept revalidation: "
                            f"change_request_id={change_request_id}"
                        ),
                        http_status_code=HTTPStatus.NOT_FOUND,
                        failure_reason="CHANGE_REQUEST_NOT_FOUND",
                    )

                self._resolve_next_status(
                    review_action=REVIEW_ACTION_ACCEPT,
                    current_status=locked_change_request.status.strip().lower(),
                )
                reindex_result = self._page_index_orchestrator.persist_prepared_page_snapshot(
                    prepared_snapshot=prepared_snapshot,
                    unit_of_work=unit_of_work,
                )
                updated = unit_of_work.change_requests.update_change_request_status(
                    change_request_id,
                    status=CHANGE_REQUEST_STATUS_ACCEPTED,
                    failure_reason=None,
                )
                if updated is None:
                    raise SupplementReviewError(
                        error_code="CHANGE_REQUEST_NOT_FOUND",
                        message=(
                            "Change request is not found during accept update: "
                            f"change_request_id={change_request_id}"
                        ),
                        http_status_code=HTTPStatus.NOT_FOUND,
                        failure_reason="CHANGE_REQUEST_NOT_FOUND",
                    )
                result_change_request_id = int(updated.id)
                result_change_request_status = updated.status
        except NotionPageIndexError as exc:
            self._page_index_orchestrator.mark_indexing_workflow_failed(
                workflow_run_id=index_workflow_id,
                page_id=notion_page.notion_page_id,
                sync_mode=SYNC_MODE_AUTO_AFTER_ACCEPT,
                error_code=exc.error_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
            )
            raise SupplementReviewError(
                error_code="PAGE_REINDEX_FAILED",
                message=f"Failed to re-index appended page: {exc.message}",
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
            ) from exc
        except SupplementReviewError as exc:
            self._page_index_orchestrator.mark_indexing_workflow_failed(
                workflow_run_id=index_workflow_id,
                page_id=notion_page.notion_page_id,
                sync_mode=SYNC_MODE_AUTO_AFTER_ACCEPT,
                error_code=exc.error_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
            )
            raise
        except InfrastructureExecutionTimeout:
            self._page_index_orchestrator.mark_indexing_workflow_failed(
                workflow_run_id=index_workflow_id,
                page_id=notion_page.notion_page_id,
                sync_mode=SYNC_MODE_AUTO_AFTER_ACCEPT,
                error_code="QUEUE_JOB_TIMEOUT",
                failure_reason="QUEUE_JOB_TIMEOUT",
            )
            raise
        except Exception as exc:
            self._page_index_orchestrator.mark_indexing_workflow_failed(
                workflow_run_id=index_workflow_id,
                page_id=notion_page.notion_page_id,
                sync_mode=SYNC_MODE_AUTO_AFTER_ACCEPT,
                error_code="PAGE_REINDEX_FAILED",
                failure_reason="UNKNOWN_ERROR",
            )
            raise SupplementReviewError(
                error_code="PAGE_REINDEX_FAILED",
                message=f"Failed to re-index appended page: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            ) from exc

        self._page_index_orchestrator.mark_indexing_workflow_succeeded(
            workflow_run_id=index_workflow_id,
            page_id=notion_page.notion_page_id,
            sync_mode=SYNC_MODE_AUTO_AFTER_ACCEPT,
            snapshot=reindex_result,
        )

        return _AcceptMutationResult(
            change_request_id=result_change_request_id,
            change_request_status=result_change_request_status,
            follow_up_metadata={
                "append_result": append_result,
                "reindex_result": {
                    "workflow_run_id": index_workflow_id,
                    "status": "succeeded",
                    "page_id": reindex_result.notion_page_id,
                    "indexed_block_count": reindex_result.indexed_block_count,
                },
            },
        )

    async def _append_to_ai_supplement_zone(
        self,
        *,
        page_id: str,
        change_request_id: int,
        request_workflow_id: str,
        topic_title: str,
        source_display_name: str,
        summary: str,
        concepts: list[str],
        notes: list[str],
    ) -> Dict[str, Any]:
        tool_result = await self._tool_registry.call_tool(
            NOTION_WRITER_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "accept_append",
                    "change_request_id": change_request_id,
                    "page_id": page_id,
                },
            ),
            arguments={
                "page_id": page_id,
                "change_request_id": change_request_id,
                "topic_title": topic_title,
                "source_display_name": source_display_name,
                "summary": summary,
                "concepts": concepts,
                "notes": notes,
                "idempotency_key": f"change-request-{change_request_id}",
            },
        )
        if tool_result.is_error:
            error_code = "UNKNOWN_ERROR"
            error_message = "Notion writer failed"
            if tool_result.error is not None:
                error_code = tool_result.error.code
                error_message = tool_result.error.message
            raise SupplementReviewError(
                error_code=error_code,
                message=error_message,
                http_status_code=self._http_status_for_tool_error(error_code),
                failure_reason=self._normalize_failure_reason(error_code),
            )
        structured_content = tool_result.structured_content or {}
        return {
            "page_id": structured_content.get("page_id", page_id),
            "change_request_id": structured_content.get(
                "change_request_id", change_request_id
            ),
            "target_path": structured_content.get("target_path"),
            "appended_block_count": structured_content.get("appended_block_count"),
            "created_date_group": structured_content.get("created_date_group"),
            "idempotent_replay": structured_content.get("idempotent_replay"),
            "section_lines": structured_content.get("section_lines"),
        }

    def _http_status_for_tool_error(self, error_code: str) -> int:
        normalized_error_code = error_code.strip().upper()
        if normalized_error_code == "INVALID_ARGUMENT":
            return HTTPStatus.BAD_REQUEST
        if normalized_error_code == "NOTION_PAGE_NOT_FOUND":
            return HTTPStatus.NOT_FOUND
        if normalized_error_code == "WRITE_POLICY_VIOLATION":
            return HTTPStatus.CONFLICT
        return HTTPStatus.INTERNAL_SERVER_ERROR

    def _resolve_next_status(self, *, review_action: str, current_status: str) -> str:
        if current_status != CHANGE_REQUEST_STATUS_PENDING:
            raise SupplementReviewError(
                error_code="INVALID_STATE_TRANSITION",
                message=(
                    "Only pending change requests can be reviewed: "
                    f"current_status={current_status}"
                ),
                http_status_code=HTTPStatus.CONFLICT,
            )

        if review_action == REVIEW_ACTION_ACCEPT:
            return CHANGE_REQUEST_STATUS_ACCEPTED
        if review_action == REVIEW_ACTION_REJECT:
            return CHANGE_REQUEST_STATUS_REJECTED
        if review_action == REVIEW_ACTION_EDIT_LATER:
            return CHANGE_REQUEST_STATUS_PENDING
        raise SupplementReviewError(
            error_code="INVALID_ARGUMENT",
            message=f"Unknown review action: {review_action}",
            http_status_code=HTTPStatus.BAD_REQUEST,
        )

    def _normalize_failure_reason(self, failure_reason: str) -> str:
        normalized = failure_reason.strip().upper()
        if normalized in STANDARD_FAILURE_REASONS:
            return normalized
        return "UNKNOWN_ERROR"

    def _mark_failed_workflow(
        self,
        *,
        workflow_run_id: int,
        failure_reason: str,
        error_code: str,
        review_action: str,
    ) -> None:
        self._workflow_run_service.mark_workflow_failed(
            workflow_run_id,
            failure_reason=self._normalize_failure_reason(failure_reason),
            metadata_json=json.dumps(
                {
                    "operation": "review_change_request",
                    "review_action": review_action,
                    "error_code": error_code,
                },
                sort_keys=True,
            ),
        )
