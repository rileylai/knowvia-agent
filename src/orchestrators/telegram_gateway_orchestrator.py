from __future__ import annotations

import json
import hashlib
import shlex
from time import perf_counter
import uuid
from dataclasses import asdict, dataclass
from http import HTTPStatus
from typing import Any, Optional

from src.observability.redaction import sanitize_sensitive_text
from src.observability.logger import get_logger
from src.queue import QueueClient, QueueRetryPolicy, get_callable_import_path
from src.orchestrators.telegram_ingestion_orchestrator import (
    TelegramDocumentAttachment,
    TelegramIngestionError,
    TelegramIngestionOrchestrator,
    TelegramPhotoAttachment,
)
from src.orchestrators.telegram_qa_orchestrator import (
    TelegramQAError,
    TelegramQAOrchestrator,
)
from src.orchestrators.telegram_review_orchestrator import (
    TelegramReviewError,
    TelegramReviewOrchestrator,
)
from src.orchestrators.telegram_page_orchestrator import TelegramPageOrchestrator
from src.orchestrators.telegram_sync_orchestrator import (
    TelegramSyncError,
    TelegramSyncOrchestrator,
    TelegramSyncView,
)
from src.orchestrators.telegram_index_orchestrator import (
    TelegramIndexError,
    TelegramIndexOrchestrator,
    TelegramIndexResult,
    TelegramFullIndexView,
)
from src.orchestrators.telegram_operator_orchestrator import (
    TelegramPendingItem,
    TelegramOperatorError,
    TelegramOperatorOrchestrator,
)
from src.services import (
    STANDARD_FAILURE_REASONS,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
    TrustBoundaryError,
    TrustBoundaryService,
    TelegramUpdateClaim,
    TelegramUpdateIdempotencyError,
    TelegramUpdateIdempotencyService,
    TELEGRAM_CALLBACK_KIND_PICKER,
    TELEGRAM_CALLBACK_KIND_OPERATOR,
    TELEGRAM_CALLBACK_KIND_REVIEW,
    TELEGRAM_PICKER_CALLBACK_ACTIONS,
    TELEGRAM_OPERATOR_CALLBACK_ACTIONS,
    TELEGRAM_REVIEW_CALLBACK_ACTIONS,
    TelegramSessionStore,
    TELEGRAM_SYNC_MAX_SELECTED_PAGES,
    TelegramIndexSessionStore,
)
from src.services.latency_evidence import LatencyEvidence, elapsed_ms
from src.tools import ToolContext, ToolRegistry

TELEGRAM_BOT_TOOL_NAME = "telegram_bot"
_SAFE_PROPOSAL_FAILURE_METADATA_FIELDS = frozenset(
    {
        "failure_stage",
        "validation_field",
        "validator_version",
        "source_document_id",
        "source_attachment_count",
        "session_state",
        "session_retry_available",
        "llm_ms",
        "ocr_ms",
        "download_ms",
        "persist_ms",
        "preview_delivery_ms",
        "total_business_ms",
        "source_normalized_char_count",
        "candidate_field_char_count",
        "evidence_claim_count",
        "extracted_claim_count",
        "matched_claim_count",
        "unsupported_claim_count",
        "first_unsupported_claim_index",
        "first_unsupported_reason",
        "failed_field_count",
        "validation_granularity",
        "validation_unit_count",
        "matched_validation_unit_count",
        "failed_validation_unit_count",
        "failed_logical_region_count",
        "failed_logical_regions",
        "failed_proposal_field_count",
        "summary_validation_unit_count",
        "concept_validation_unit_count",
        "note_validation_unit_count",
        "failed_summary_validation_unit_count",
        "failed_concept_validation_unit_count",
        "failed_note_validation_unit_count",
        "first_unsupported_validation_unit_index",
        "body_repair_eligible",
        "repair_scope",
        "matched_exact_ascii_anchor_count",
        "matched_cjk_anchor_count",
        "unmatched_general_token_count",
        "unmatched_general_ascii_count",
        "concept_count",
        "note_count",
        "covered_concept_count",
        "uncovered_concept_count",
        "notes_with_application_count",
        "failure_reason_counts",
        "failed_validation_unit_details",
        "summary_repair_eligible",
        "source_snapshot_digest",
        "prompt_source_digest",
        "validation_source_digest",
        "title_anchor_count",
        "matched_title_anchor_count",
        "unmatched_title_anchor_count",
        "title_failure_reason",
        "matched_high_specificity_anchor_count",
        "unmatched_high_specificity_anchor_count",
        "matched_general_anchor_count",
        "unmatched_general_anchor_count",
        "matched_technical_identifier_count",
        "unmatched_technical_identifier_count",
        "numeric_anchor_count",
        "unmatched_numeric_anchor_count",
        "title_repair_attempted",
        "title_repair_succeeded",
        "title_repair_failure_reason",
        "title_fallback_attempted",
        "title_fallback_succeeded",
        "summary_repair_attempted",
        "summary_repair_succeeded",
        "body_repair_attempted",
        "body_repair_succeeded",
        "provider_name",
        "model",
        "prompt_id",
        "prompt_version",
        "prompt_safety_version",
        "token_input",
        "token_output",
        "estimated_cost",
        "proposal_workflow_run_id",
        "title_fallback_used",
    }
)


@dataclass
class TelegramGatewayResult:
    workflow_run_id: Optional[int]
    status: str
    handled: bool
    command: Optional[str]
    reply_text: Optional[str]
    telegram_message_id: Optional[int]
    skipped_reason: Optional[str]
    source_document_id: Optional[int]
    change_request_id: Optional[int]
    source_type: Optional[str]
    target_notion_page_id: Optional[str]
    qa_workflow_run_id: Optional[int]
    insufficient_info: Optional[bool]
    citations: list[str]
    review_workflow_run_id: Optional[int]
    review_action: Optional[str]
    change_request_status: Optional[str]
    target_set: bool = False
    business_status: str = "not_started"
    callback_ack_status: Optional[str] = None
    preview_delivery_status: Optional[str] = None
    sync_workflow_run_id: Optional[int] = None
    sync_status: Optional[str] = None
    sync_discovered_page_count: Optional[int] = None
    sync_selected_page_count: Optional[int] = None
    sync_succeeded_page_count: Optional[int] = None
    sync_failed_page_count: Optional[int] = None
    index_workflow_run_id: Optional[int] = None
    index_status: Optional[str] = None
    index_discovered_page_count: Optional[int] = None
    index_processed_page_count: Optional[int] = None
    index_failed_page_count: Optional[int] = None
    index_remaining_page_count: Optional[int] = None
    index_failure_reason: Optional[str] = None
    index_estimated_cost_usd: Optional[float] = None
    index_stale: Optional[bool] = None
    cost_scope: Optional[str] = None
    cost_workflow_run_id: Optional[int] = None
    cost_total_usd: Optional[float] = None
    cost_llm_usd: Optional[float] = None
    cost_embedding_usd: Optional[float] = None
    cost_unknown_workflow_count: Optional[int] = None
    cost_budget_status: Optional[str] = None
    cost_budget_usd: Optional[float] = None
    cost_workflow_budget_exceeded_count: Optional[int] = None
    cost_workflow_budget_usd: Optional[float] = None
    workflow_detail_run_id: Optional[int] = None
    workflow_detail_type: Optional[str] = None
    workflow_detail_status: Optional[str] = None
    workflow_detail_failure_reason: Optional[str] = None
    workflow_detail_age_seconds: Optional[float] = None
    workflow_detail_stale: Optional[bool] = None
    workflow_detail_estimated_cost_usd: Optional[float] = None
    workflow_recent_count: Optional[int] = None
    pending_count: Optional[int] = None
    status_liveness: Optional[str] = None
    status_readiness: Optional[str] = None
    status_checks: Optional[dict[str, str]] = None
    stats_page_count: Optional[int] = None
    stats_block_count: Optional[int] = None
    stats_chunk_count: Optional[int] = None
    stats_vector_count: Optional[int] = None
    stats_proposal_count: Optional[int] = None
    stats_pending_proposal_count: Optional[int] = None
    stats_accepted_proposal_count: Optional[int] = None
    stats_rejected_proposal_count: Optional[int] = None
    stats_latest_full_index_at: Optional[str] = None
    stats_latest_incremental_sync_at: Optional[str] = None


@dataclass
class TelegramCallbackAttachment:
    callback_query_id: str
    callback_data: str


class TelegramGatewayError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
        workflow_run_id: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason
        self.workflow_run_id = workflow_run_id
        self.metadata = metadata or {}


class TelegramGatewayOrchestrator:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        workflow_run_service: WorkflowRunService,
        telegram_ingestion_orchestrator: Optional[TelegramIngestionOrchestrator] = None,
        telegram_qa_orchestrator: Optional[TelegramQAOrchestrator] = None,
        telegram_review_orchestrator: Optional[TelegramReviewOrchestrator] = None,
        telegram_page_orchestrator: Optional[TelegramPageOrchestrator] = None,
        telegram_sync_orchestrator: Optional[TelegramSyncOrchestrator] = None,
        telegram_session_store: Optional[TelegramSessionStore] = None,
        telegram_index_orchestrator: Optional[TelegramIndexOrchestrator] = None,
        telegram_index_session_store: Optional[TelegramIndexSessionStore] = None,
        telegram_operator_orchestrator: Optional[TelegramOperatorOrchestrator] = None,
        trust_boundary: Optional[TrustBoundaryService] = None,
        update_idempotency_service: Optional[TelegramUpdateIdempotencyService] = None,
        queue_client: Optional[QueueClient] = None,
        telegram_job_timeout_seconds: int = 180,
        telegram_review_job_timeout_seconds: int = 10800,
    ) -> None:
        self._tool_registry = tool_registry
        self._workflow_run_service = workflow_run_service
        self._telegram_ingestion_orchestrator = telegram_ingestion_orchestrator
        self._telegram_qa_orchestrator = telegram_qa_orchestrator
        self._telegram_review_orchestrator = telegram_review_orchestrator
        self._telegram_page_orchestrator = telegram_page_orchestrator
        self._telegram_sync_orchestrator = telegram_sync_orchestrator
        self._telegram_session_store = telegram_session_store
        self._telegram_index_orchestrator = telegram_index_orchestrator
        self._telegram_index_session_store = telegram_index_session_store
        self._telegram_operator_orchestrator = telegram_operator_orchestrator
        self._trust_boundary = trust_boundary
        self._update_idempotency_service = update_idempotency_service
        self._queue_client = queue_client
        self._telegram_job_timeout_seconds = telegram_job_timeout_seconds
        self._telegram_review_job_timeout_seconds = telegram_review_job_timeout_seconds
        self._logger = get_logger("learnloop.telegram.gateway")

    async def handle_webhook(
        self,
        *,
        update_id: Optional[int],
        chat_id: Optional[str],
        text: Optional[str],
        caption: Optional[str],
        document: Optional[TelegramDocumentAttachment],
        photos: list[TelegramPhotoAttachment],
        request_workflow_id: str,
        user_id: Optional[str] = None,
        message_id: Optional[int] = None,
        media_group_id: Optional[str] = None,
        callback: Optional[TelegramCallbackAttachment] = None,
    ) -> TelegramGatewayResult:
        self._require_allowed_chat(chat_id)
        claim = self._claim_update(update_id)
        if claim is not None and not claim.owner:
            return self._replay_or_report_duplicate(claim)

        return await self._process_claimed_webhook(
            update_id=update_id,
            chat_id=chat_id,
            text=text,
            caption=caption,
            document=document,
            photos=photos,
            user_id=user_id,
            message_id=message_id,
            media_group_id=media_group_id,
            callback=callback,
            request_workflow_id=request_workflow_id,
        )

    async def handle_claimed_webhook(
        self,
        *,
        update_id: Optional[int],
        chat_id: Optional[str],
        text: Optional[str],
        caption: Optional[str],
        document: Optional[TelegramDocumentAttachment],
        photos: list[TelegramPhotoAttachment],
        request_workflow_id: str,
        user_id: Optional[str] = None,
        message_id: Optional[int] = None,
        media_group_id: Optional[str] = None,
        callback: Optional[TelegramCallbackAttachment] = None,
    ) -> TelegramGatewayResult:
        """Process work after the API route has claimed the update ledger."""

        self._require_allowed_chat(chat_id)
        return await self._process_claimed_webhook(
            update_id=update_id,
            chat_id=chat_id,
            text=text,
            caption=caption,
            document=document,
            photos=photos,
            user_id=user_id,
            message_id=message_id,
            media_group_id=media_group_id,
            callback=callback,
            request_workflow_id=request_workflow_id,
        )

    async def run_full_index_job(
        self,
        *,
        workflow_run_id: int,
        request_workflow_id: str,
    ):
        if self._telegram_index_orchestrator is None:
            raise TelegramGatewayError(
                error_code="TELEGRAM_INDEX_NOT_CONFIGURED",
                message="Telegram full indexing is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run_id,
            )
        return await self._telegram_index_orchestrator.run_full_index_job(
            workflow_run_id=workflow_run_id,
            request_workflow_id=request_workflow_id,
        )

    async def enqueue_webhook(
        self,
        *,
        update_id: Optional[int],
        chat_id: Optional[str],
        text: Optional[str],
        caption: Optional[str],
        document: Optional[TelegramDocumentAttachment],
        photos: list[TelegramPhotoAttachment],
        request_workflow_id: str,
        user_id: Optional[str] = None,
        message_id: Optional[int] = None,
        media_group_id: Optional[str] = None,
        callback: Optional[TelegramCallbackAttachment] = None,
    ) -> TelegramGatewayResult:
        """Claim and enqueue a Telegram update, returning without long work."""

        self._require_allowed_chat(chat_id)
        claim = self._claim_update(update_id)
        if claim is not None and not claim.owner:
            return self._replay_or_report_duplicate(claim)

        if self._queue_client is None:
            return await self._process_claimed_webhook(
                update_id=update_id,
                chat_id=chat_id,
                text=text,
                caption=caption,
                document=document,
                photos=photos,
                user_id=user_id,
                message_id=message_id,
                media_group_id=media_group_id,
                callback=callback,
                request_workflow_id=request_workflow_id,
            )

        retry_policy = QueueRetryPolicy(max_retries=2, retry_intervals=(5, 30))
        try:
            from src.worker.telegram import (
                TELEGRAM_WEBHOOK_JOB_PATH,
                process_telegram_webhook_job,
            )

            if (
                get_callable_import_path(process_telegram_webhook_job)
                != TELEGRAM_WEBHOOK_JOB_PATH
            ):
                raise RuntimeError("Telegram worker callable path is not canonical")

            self._queue_client.enqueue(
                queue_name="telegram",
                function=process_telegram_webhook_job,
                args=(
                    update_id,
                    chat_id,
                    text,
                    caption,
                    asdict(document) if document is not None else None,
                    [asdict(photo) for photo in photos],
                    request_workflow_id,
                    user_id,
                    message_id,
                    media_group_id,
                    asdict(callback) if callback is not None else None,
                ),
                description="Process one Telegram webhook update",
                retry_policy=retry_policy,
                timeout_seconds=self._timeout_seconds_for_webhook(
                    chat_id=chat_id,
                    user_id=user_id,
                    text=text,
                    caption=caption,
                    callback=callback,
                ),
            )
        except Exception as exc:
            error = TelegramGatewayError(
                error_code="TELEGRAM_QUEUE_UNAVAILABLE",
                message="Telegram background queue is unavailable",
                http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                failure_reason="TELEGRAM_QUEUE_UNAVAILABLE",
            )
            self._mark_update_failed(update_id, error)
            raise error from exc

        return TelegramGatewayResult(
            workflow_run_id=None,
            status="running",
            handled=False,
            command=None,
            reply_text=None,
            telegram_message_id=None,
            skipped_reason="QUEUED",
            source_document_id=None,
            change_request_id=None,
            source_type=None,
            target_notion_page_id=None,
            qa_workflow_run_id=None,
            insufficient_info=None,
            citations=[],
            review_workflow_run_id=None,
            review_action=None,
            change_request_status=None,
        )

    async def _process_claimed_webhook(
        self,
        *,
        update_id: Optional[int],
        chat_id: Optional[str],
        text: Optional[str],
        caption: Optional[str],
        document: Optional[TelegramDocumentAttachment],
        photos: list[TelegramPhotoAttachment],
        user_id: Optional[str],
        message_id: Optional[int],
        media_group_id: Optional[str],
        callback: Optional[TelegramCallbackAttachment],
        request_workflow_id: str,
    ) -> TelegramGatewayResult:

        try:
            result = await self._handle_new_webhook(
                update_id=update_id,
                chat_id=chat_id,
                text=text,
                caption=caption,
                document=document,
                photos=photos,
                user_id=user_id,
                message_id=message_id,
                media_group_id=media_group_id,
                callback=callback,
                request_workflow_id=request_workflow_id,
            )
        except TelegramGatewayError as exc:
            await self._notify_user_of_gateway_error(
                error=exc,
                chat_id=chat_id,
                callback=callback,
                request_workflow_id=request_workflow_id,
            )
            self._mark_update_failed(update_id, exc)
            raise
        except WorkflowRunAuditUpdateError as exc:
            self._mark_update_failed(
                update_id,
                TelegramGatewayError(
                    error_code=exc.error_code,
                    message="Telegram workflow audit update failed",
                    http_status_code=exc.http_status_code,
                    failure_reason=exc.failure_reason,
                    workflow_run_id=exc.workflow_run_id,
                ),
            )
            raise
        except Exception as exc:
            self._mark_update_failed(
                update_id,
                TelegramGatewayError(
                    error_code="TELEGRAM_GATEWAY_FAILED",
                    message="Telegram gateway workflow failed",
                    http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                    failure_reason="UNKNOWN_ERROR",
                ),
            )
            raise

        if self._update_idempotency_service is not None:
            try:
                self._update_idempotency_service.mark_succeeded(
                    update_id,
                    workflow_run_id=result.workflow_run_id,
                    result=asdict(result),
                )
            except Exception as exc:
                raise TelegramGatewayError(
                    error_code="TELEGRAM_UPDATE_LEDGER_FAILED",
                    message="Telegram update ledger could not be completed",
                    http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                    failure_reason="TELEGRAM_UPDATE_LEDGER_FAILED",
                    workflow_run_id=result.workflow_run_id,
                ) from exc
        return result

    async def _notify_user_of_gateway_error(
        self,
        *,
        error: TelegramGatewayError,
        chat_id: Optional[str],
        callback: Optional[TelegramCallbackAttachment],
        request_workflow_id: str,
    ) -> None:
        if error.error_code not in {
            "LLM_OUTPUT_INVALID",
            "UPLOAD_SESSION_EXPIRED",
            "UPLOAD_SESSION_INVALID",
            "INVALID_CALLBACK",
            "TELEGRAM_QUEUE_UNAVAILABLE",
            "EMPTY_UPLOAD",
            "TELEGRAM_PREVIEW_DELIVERY_FAILED",
        }:
            return
        user_messages = {
            "LLM_OUTPUT_INVALID": (
                "Proposal validation failed for the existing source. "
                "Use /retry-proposal to retry the proposal only; upload and OCR will not be repeated."
            ),
            "TELEGRAM_PREVIEW_DELIVERY_FAILED": (
                "Proposal was created, but preview delivery failed. "
                "Please wait for recovery; do not upload again."
            ),
        }
        safe_message = user_messages.get(
            error.error_code,
            sanitize_sensitive_text(error.message)[:190],
        )
        callback_acknowledged = False
        callback_ack_already_attempted = error.metadata.get("callback_ack_status") in {
            "succeeded",
            "failed",
        }
        try:
            if (
                callback is not None
                and not callback_ack_already_attempted
                and error.error_code != "TELEGRAM_PREVIEW_DELIVERY_FAILED"
            ):
                callback_acknowledged, _ = await self._answer_callback_query(
                    callback=callback,
                    request_workflow_id=request_workflow_id,
                    text=safe_message,
                    operation="telegram_callback_error",
                )
            if chat_id and (
                callback is None
                or callback_ack_already_attempted
                or not callback_acknowledged
                or error.error_code == "TELEGRAM_PREVIEW_DELIVERY_FAILED"
            ):
                result = await self._tool_registry.call_tool(
                    TELEGRAM_BOT_TOOL_NAME,
                    context=ToolContext(
                        workflow_id=request_workflow_id,
                        metadata={
                            "operation": "telegram_error_reply",
                            "chat_id": chat_id,
                        },
                    ),
                    arguments={"chat_id": chat_id, "text": safe_message},
                )
                if result.is_error:
                    self._logger.warning(
                        "telegram_recovery_message_failed",
                        extra={"workflow_id": request_workflow_id},
                    )
        except Exception:
            # The original deterministic gateway error remains authoritative.
            self._logger.warning(
                "telegram_recovery_message_failed",
                extra={
                    "workflow_id": request_workflow_id,
                    "failure_reason": "TELEGRAM_SEND_FAILED",
                },
            )

    async def _answer_callback_query(
        self,
        *,
        callback: TelegramCallbackAttachment,
        request_workflow_id: str,
        text: Optional[str] = None,
        operation: str,
    ) -> tuple[bool, Optional[str]]:
        """Acknowledge Telegram UX state without owning business success."""

        try:
            result = await self._tool_registry.call_tool(
                TELEGRAM_BOT_TOOL_NAME,
                context=ToolContext(
                    workflow_id=request_workflow_id,
                    metadata={
                        "operation": operation,
                    },
                ),
                arguments={
                    "action": "answer_callback_query",
                    "callback_query_id": callback.callback_query_id,
                    **({"text": text} if text else {}),
                },
            )
        except Exception:
            result = None

        if result is None or result.is_error:
            self._logger.warning(
                "telegram_callback_ack_failed",
                extra={
                    "workflow_id": request_workflow_id,
                    "failure_reason": "TELEGRAM_CALLBACK_ACK_FAILED",
                    "callback_ack_status": "failed",
                },
            )
            return False, "TELEGRAM_CALLBACK_ACK_FAILED"
        return True, None

    async def _handle_new_webhook(
        self,
        *,
        update_id: Optional[int],
        chat_id: Optional[str],
        text: Optional[str],
        caption: Optional[str],
        document: Optional[TelegramDocumentAttachment],
        photos: list[TelegramPhotoAttachment],
        user_id: Optional[str],
        message_id: Optional[int],
        media_group_id: Optional[str],
        callback: Optional[TelegramCallbackAttachment],
        request_workflow_id: str,
    ) -> TelegramGatewayResult:
        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="telegram",
            metadata_json=json.dumps(
                {
                    "operation": "telegram_webhook",
                    "update_id": update_id,
                    "chat_id": chat_id,
                    "request_workflow_id": request_workflow_id,
                    "has_document": document is not None,
                    "photo_count": len(photos),
                },
                sort_keys=True,
            ),
        )

        callback_ack_status = "not_applicable"
        callback_ack_failure_reason: Optional[str] = None
        business_status = "not_started"
        business_started = perf_counter()
        latency = LatencyEvidence()
        preview_delivery_status = "not_applicable"
        preview_session_id: Optional[str] = None
        preview_delivery_required = False
        callback_action_name: Optional[str] = None
        sync_workflow_run_id: Optional[int] = None
        sync_status: Optional[str] = None
        sync_discovered_page_count: Optional[int] = None
        sync_selected_page_count: Optional[int] = None
        sync_succeeded_page_count: Optional[int] = None
        sync_failed_page_count: Optional[int] = None
        index_workflow_run_id: Optional[int] = None
        index_status: Optional[str] = None
        index_discovered_page_count: Optional[int] = None
        index_processed_page_count: Optional[int] = None
        index_failed_page_count: Optional[int] = None
        index_remaining_page_count: Optional[int] = None
        index_failure_reason: Optional[str] = None
        index_estimated_cost_usd: Optional[float] = None
        index_stale: Optional[bool] = None
        cost_scope: Optional[str] = None
        cost_workflow_run_id: Optional[int] = None
        cost_total_usd: Optional[float] = None
        cost_llm_usd: Optional[float] = None
        cost_embedding_usd: Optional[float] = None
        cost_unknown_workflow_count: Optional[int] = None
        cost_budget_status: Optional[str] = None
        cost_budget_usd: Optional[float] = None
        cost_workflow_budget_exceeded_count: Optional[int] = None
        cost_workflow_budget_usd: Optional[float] = None
        workflow_detail_run_id: Optional[int] = None
        workflow_detail_type: Optional[str] = None
        workflow_detail_status: Optional[str] = None
        workflow_detail_failure_reason: Optional[str] = None
        workflow_detail_age_seconds: Optional[float] = None
        workflow_detail_stale: Optional[bool] = None
        workflow_detail_estimated_cost_usd: Optional[float] = None
        workflow_recent_count: Optional[int] = None
        pending_count: Optional[int] = None
        status_liveness: Optional[str] = None
        status_readiness: Optional[str] = None
        status_checks: Optional[dict[str, str]] = None
        stats_page_count: Optional[int] = None
        stats_block_count: Optional[int] = None
        stats_chunk_count: Optional[int] = None
        stats_vector_count: Optional[int] = None
        stats_proposal_count: Optional[int] = None
        stats_pending_proposal_count: Optional[int] = None
        stats_accepted_proposal_count: Optional[int] = None
        stats_rejected_proposal_count: Optional[int] = None
        stats_latest_full_index_at: Optional[str] = None
        stats_latest_incremental_sync_at: Optional[str] = None

        try:
            normalized_text = (text or "").strip()
            normalized_caption = (caption or "").strip()
            normalized_input_text = self._select_command_text(
                text=normalized_text,
                caption=normalized_caption,
            )
            normalized_chat_id = (chat_id or "").strip()
            normalized_user_id = (user_id or normalized_chat_id).strip()
            has_media = (document is not None) or bool(photos)

            if not normalized_chat_id or (
                not normalized_input_text and not has_media and callback is None
            ):
                skipped_reason = "NO_TEXT_MESSAGE"
                self._workflow_run_service.mark_workflow_succeeded(
                    workflow_run.id,
                    metadata_json=json.dumps(
                        {
                            "operation": "telegram_webhook",
                            "handled": False,
                            "skipped_reason": skipped_reason,
                        },
                        sort_keys=True,
                    ),
                )
                return TelegramGatewayResult(
                    workflow_run_id=workflow_run.id,
                    status="succeeded",
                    handled=False,
                    command=None,
                    reply_text=None,
                    telegram_message_id=None,
                    skipped_reason=skipped_reason,
                    source_document_id=None,
                    change_request_id=None,
                    source_type=None,
                    target_notion_page_id=None,
                    qa_workflow_run_id=None,
                    insufficient_info=None,
                    citations=[],
                    review_workflow_run_id=None,
                    review_action=None,
                    change_request_status=None,
                )

            command = self._parse_command(normalized_input_text)
            if command == "start":
                command = "help"
            source_document_id: Optional[int] = None
            change_request_id: Optional[int] = None
            source_type: Optional[str] = None
            target_notion_page_id: Optional[str] = None
            target_notion_path: Optional[str] = None
            target_set = False
            qa_workflow_run_id: Optional[int] = None
            insufficient_info: Optional[bool] = None
            citations: list[str] = []
            review_workflow_run_id: Optional[int] = None
            review_action: Optional[str] = None
            change_request_status: Optional[str] = None
            reply_markup: Optional[dict[str, Any]] = None
            reply_texts: list[str] = []
            callback_query_id: Optional[str] = None
            ingestion_result = None
            reply_text = ""
            if callback is not None:
                command = "callback"
                callback_query_id = callback.callback_query_id
                callback_action = self._resolve_callback_action(
                    callback=callback,
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
                callback_action_name = callback_action.action
                self._validate_callback_action(
                    callback_action=callback_action,
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
                if callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_OPERATOR:
                    if self._telegram_session_store is None or not self._telegram_session_store.claim_callback(
                        token=callback_action.token,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    ):
                        raise TelegramGatewayError(
                            error_code="INVALID_CALLBACK",
                            message="This sync button was already used or has expired.",
                            http_status_code=HTTPStatus.GONE,
                            failure_reason="INVALID_CALLBACK",
                        )
                callback_ack_status, callback_ack_failure_reason = (
                    "succeeded",
                    None,
                )
                ack_ok, ack_failure_reason = await self._answer_callback_query(
                    callback=callback,
                    request_workflow_id=request_workflow_id,
                    operation="telegram_callback_ack",
                )
                if not ack_ok:
                    callback_ack_status = "failed"
                    callback_ack_failure_reason = ack_failure_reason
                if callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_OPERATOR:
                    if callback_action.action == "pending_view":
                        if self._telegram_operator_orchestrator is None:
                            raise TelegramGatewayError(
                                error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                                message="Telegram pending review is not configured",
                                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                                failure_reason="UNKNOWN_ERROR",
                            )
                        if callback_action.change_request_id is None:
                            raise TelegramGatewayError(
                                error_code="INVALID_CALLBACK",
                                message="This pending proposal action is invalid.",
                                http_status_code=HTTPStatus.BAD_REQUEST,
                                failure_reason="INVALID_CALLBACK",
                            )
                        pending_result = self._telegram_operator_orchestrator.get_pending_detail(
                            change_request_id=callback_action.change_request_id,
                        )
                        reply_text = pending_result.reply_text
                        pending_count = len(pending_result.items)
                        change_request_id = callback_action.change_request_id
                        change_request_status = "pending"
                        reply_markup = self._build_review_markup_for_change_request(
                            change_request_id=callback_action.change_request_id,
                            session_id=callback_action.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                            allow_accept=(
                                bool(pending_result.items)
                                and pending_result.items[0].target_path != "unassigned"
                            ),
                        )
                        business_status = "succeeded"
                    elif self._telegram_sync_orchestrator is None:
                        raise TelegramGatewayError(
                            error_code="TELEGRAM_SYNC_NOT_CONFIGURED",
                            message="Telegram sync is not configured",
                            http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                            failure_reason="UNKNOWN_ERROR",
                        )
                    if callback_action.action == "sync_toggle":
                        if not callback_action.target_notion_page_id:
                            raise TelegramGatewayError(
                                error_code="INVALID_CALLBACK",
                                message="This sync page selection is invalid.",
                                http_status_code=HTTPStatus.BAD_REQUEST,
                                failure_reason="INVALID_CALLBACK",
                            )
                        sync_view = self._telegram_sync_orchestrator.toggle_page(
                            session_id=callback_action.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                            page_id=callback_action.target_notion_page_id,
                        )
                        reply_text, reply_markup = self._build_sync_picker(
                            view=sync_view,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                        )
                        sync_status = sync_view.state
                        sync_discovered_page_count = sync_view.discovered_page_count
                        sync_selected_page_count = sync_view.selected_page_count
                        business_status = "succeeded"
                    elif callback_action.action == "sync_confirm":
                        sync_result = await self._telegram_sync_orchestrator.confirm_session(
                            session_id=callback_action.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                            request_workflow_id=request_workflow_id,
                        )
                        reply_text = sync_result.reply_text
                        sync_workflow_run_id = sync_result.workflow_run_id
                        sync_status = sync_result.status
                        sync_discovered_page_count = sync_result.discovered_page_count
                        sync_selected_page_count = sync_result.selected_page_count
                        sync_succeeded_page_count = sync_result.succeeded_page_count
                        sync_failed_page_count = sync_result.failed_page_count
                        business_status = "succeeded"
                    elif callback_action.action == "sync_cancel":
                        sync_result = self._telegram_sync_orchestrator.cancel_session(
                            session_id=callback_action.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                        )
                        reply_text = sync_result.reply_text
                        sync_workflow_run_id = sync_result.workflow_run_id
                        sync_status = sync_result.status
                        sync_discovered_page_count = sync_result.discovered_page_count
                        sync_selected_page_count = sync_result.selected_page_count
                        sync_succeeded_page_count = sync_result.succeeded_page_count
                        sync_failed_page_count = sync_result.failed_page_count
                        business_status = "succeeded"
                    elif callback_action.action == "index_full_confirm":
                        if self._telegram_index_orchestrator is None:
                            raise TelegramGatewayError(
                                error_code="TELEGRAM_INDEX_NOT_CONFIGURED",
                                message="Telegram full indexing is not configured",
                                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                                failure_reason="UNKNOWN_ERROR",
                            )
                        index_result = await self._telegram_index_orchestrator.confirm_full_index(
                            session_id=callback_action.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                            request_workflow_id=request_workflow_id,
                        )
                        reply_text = index_result.reply_text
                        index_workflow_run_id = index_result.workflow_run_id
                        index_status = index_result.status
                        index_discovered_page_count = index_result.discovered_page_count
                        index_processed_page_count = index_result.processed_page_count
                        index_failed_page_count = index_result.failed_page_count
                        index_remaining_page_count = index_result.remaining_page_count
                        index_failure_reason = index_result.failure_reason
                        index_estimated_cost_usd = index_result.estimated_cost_usd
                        index_stale = index_result.stale
                        business_status = "succeeded"
                    elif callback_action.action == "index_full_cancel":
                        if self._telegram_index_orchestrator is None:
                            raise TelegramGatewayError(
                                error_code="TELEGRAM_INDEX_NOT_CONFIGURED",
                                message="Telegram full indexing is not configured",
                                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                                failure_reason="UNKNOWN_ERROR",
                            )
                        index_result = self._telegram_index_orchestrator.cancel_full_index(
                            session_id=callback_action.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                        )
                        reply_text = index_result.reply_text
                        index_workflow_run_id = index_result.workflow_run_id
                        index_status = index_result.status
                        index_discovered_page_count = index_result.discovered_page_count
                        index_processed_page_count = index_result.processed_page_count
                        index_failed_page_count = index_result.failed_page_count
                        index_remaining_page_count = index_result.remaining_page_count
                        index_failure_reason = index_result.failure_reason
                        index_estimated_cost_usd = index_result.estimated_cost_usd
                        index_stale = index_result.stale
                        business_status = "succeeded"
                # Review callbacks are a distinct protocol family. Dispatch them
                # before the generic picker/session branch so a restored or legacy
                # review button can never be treated as "ready for review" again.
                elif (
                    callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_REVIEW
                    and callback_action.action in {"accept", "reject"}
                ):
                    if self._telegram_review_orchestrator is None:
                        raise TelegramGatewayError(
                            error_code="TELEGRAM_REVIEW_NOT_CONFIGURED",
                            message="Telegram review orchestrator is not configured",
                            http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                            failure_reason="UNKNOWN_ERROR",
                        )
                    if callback_action.change_request_id is None:
                        raise TelegramGatewayError(
                            error_code="INVALID_CALLBACK",
                            message="This review action is invalid.",
                            http_status_code=HTTPStatus.BAD_REQUEST,
                            failure_reason="INVALID_CALLBACK",
                        )
                    review_command = f"/{callback_action.action} {callback_action.change_request_id}"
                    if callback_action.action == "reject":
                        # The inline button has no text field for a rejection
                        # reason; keep the existing orchestrator contract by
                        # supplying a deterministic, non-sensitive reason.
                        review_command += " Telegram review callback"
                    review_result = await self._telegram_review_orchestrator.handle_review_command(
                        command=callback_action.action,
                        command_text=review_command,
                        chat_id=normalized_chat_id,
                        request_workflow_id=request_workflow_id,
                    )
                    business_status = "succeeded"
                    reply_text = review_result.reply_text
                    review_workflow_run_id = review_result.review_workflow_run_id
                    change_request_id = review_result.change_request_id
                    change_request_status = review_result.change_request_status
                    review_action = review_result.review_action
                elif (
                    callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_PICKER
                    and callback_action.action in {
                        "open_page",
                        "back",
                        "root",
                        "next_page",
                        "previous_page",
                    }
                ):
                    if self._telegram_page_orchestrator is None:
                        raise TelegramGatewayError(
                            error_code="TELEGRAM_PAGES_NOT_CONFIGURED",
                            message="Telegram page picker is not configured",
                            http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                            failure_reason="UNKNOWN_ERROR",
                        )
                    current_page_id = (
                        callback_action.navigation_page_id
                        if callback_action.action != "root"
                        else None
                    )
                    if callback_action.action == "back" and current_page_id is not None:
                        current_page_id = self._telegram_page_orchestrator.build_hierarchy().parent_id(
                            current_page_id
                        )
                    if callback_action.action == "root":
                        current_page_id = None
                    reply_text, reply_markup = self._build_hierarchy_picker(
                        mode=callback_action.picker_mode,
                        session_id=callback_action.session_id,
                        change_request_id=callback_action.change_request_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                        current_page_id=current_page_id,
                        page_number=(
                            callback_action.navigation_page_number
                            if callback_action.action in {"next_page", "previous_page"}
                            else 1
                        ),
                    )
                    business_status = "succeeded"
                    change_request_id = callback_action.change_request_id
                elif (
                    callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_PICKER
                    and callback_action.action == "select_target"
                    and callback_action.picker_mode != "change_target"
                ):
                    if self._telegram_ingestion_orchestrator is None:
                        raise TelegramGatewayError(
                            error_code="TELEGRAM_INGESTION_NOT_CONFIGURED",
                            message="Telegram ingestion orchestrator is not configured",
                            http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                            failure_reason="UNKNOWN_ERROR",
                        )
                    target_notion_page_id = callback_action.target_notion_page_id
                    target_notion_path = callback_action.target_notion_path
                    if not target_notion_page_id or not target_notion_path:
                        raise TelegramGatewayError(
                            error_code="INVALID_CALLBACK",
                            message="This page selection is invalid. Please upload again.",
                            http_status_code=HTTPStatus.BAD_REQUEST,
                            failure_reason="INVALID_CALLBACK",
                        )
                    ingestion_result = await self._telegram_ingestion_orchestrator.handle_target_selection(
                        session_id=callback_action.session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                        target_notion_page_id=target_notion_page_id,
                        target_notion_path=target_notion_path,
                        request_workflow_id=request_workflow_id,
                    )
                    self._merge_stage_latency(
                        latency,
                        ingestion_result.latency_metadata,
                    )
                    business_status = "succeeded"
                    source_document_id = ingestion_result.source_document_id
                    change_request_id = ingestion_result.change_request_id
                    source_type = ingestion_result.source_type
                    target_set = bool(ingestion_result.target_notion_page_id)
                    reply_text = ingestion_result.reply_text
                    if change_request_id is not None:
                        if ingestion_result.session_id and self._telegram_ingestion_orchestrator.claim_upload_preview(
                            session_id=ingestion_result.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                        ):
                            reply_markup = self._build_review_markup(
                                ingestion_result=ingestion_result,
                                chat_id=normalized_chat_id,
                                user_id=normalized_user_id,
                            )
                            preview_session_id = ingestion_result.session_id
                            preview_delivery_required = True
                            preview_delivery_status = "pending"
                        else:
                            reply_text = "This proposal is already ready for review."
                elif (
                    callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_REVIEW
                    and callback_action.action == "change_target"
                ):
                    if self._telegram_page_orchestrator is None:
                        raise TelegramGatewayError(
                            error_code="TELEGRAM_PAGES_NOT_CONFIGURED",
                            message="Telegram page orchestrator is not configured",
                            http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                            failure_reason="UNKNOWN_ERROR",
                        )
                    if callback_action.change_request_id is None:
                        raise TelegramGatewayError(
                            error_code="INVALID_CALLBACK",
                            message="This target action is invalid.",
                            http_status_code=HTTPStatus.BAD_REQUEST,
                            failure_reason="INVALID_CALLBACK",
                        )
                    reply_text, reply_markup = self._build_review_target_picker(
                        change_request_id=callback_action.change_request_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    )
                    business_status = "succeeded"
                    change_request_id = callback_action.change_request_id
                elif (
                    callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_PICKER
                    and callback_action.action in {"select_target", "change_target_select"}
                    and (
                        callback_action.picker_mode == "change_target"
                        or callback_action.action == "change_target_select"
                    )
                ):
                    if (
                        self._telegram_review_orchestrator is None
                        or callback_action.change_request_id is None
                        or not callback_action.target_notion_page_id
                    ):
                        raise TelegramGatewayError(
                            error_code="INVALID_CALLBACK",
                            message="This target selection is invalid.",
                            http_status_code=HTTPStatus.BAD_REQUEST,
                            failure_reason="INVALID_CALLBACK",
                        )
                    review_result = await self._telegram_review_orchestrator.handle_change_target(
                        change_request_id=callback_action.change_request_id,
                        target_notion_page_id=callback_action.target_notion_page_id,
                        chat_id=normalized_chat_id,
                        request_workflow_id=request_workflow_id,
                    )
                    business_status = "succeeded"
                    reply_text = review_result.reply_text
                    review_workflow_run_id = review_result.review_workflow_run_id
                    change_request_id = review_result.change_request_id
                    change_request_status = review_result.change_request_status
                    review_action = review_result.review_action
                    target_notion_page_id = callback_action.target_notion_page_id
                    target_notion_path = callback_action.target_notion_path
                    target_set = bool(target_notion_page_id)
                    reply_markup = self._build_review_markup_for_change_request(
                        change_request_id=int(change_request_id),
                        session_id=callback_action.session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    )
                else:
                    raise TelegramGatewayError(
                        error_code="INVALID_CALLBACK",
                        message="This button is no longer valid. Please upload again.",
                        http_status_code=HTTPStatus.BAD_REQUEST,
                        failure_reason="INVALID_CALLBACK",
                    )
            elif command == "health":
                reply_text = self._build_reply_for_command(command)
            elif command == "help":
                reply_text = self._build_reply_for_command(command)
            elif command == "sync":
                if self._telegram_sync_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_SYNC_NOT_CONFIGURED",
                        message="Telegram sync is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                sync_view = await self._telegram_sync_orchestrator.start_session(
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                    request_workflow_id=request_workflow_id,
                )
                reply_text, reply_markup = self._build_sync_picker(
                    view=sync_view,
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
                sync_status = sync_view.state
                sync_discovered_page_count = sync_view.discovered_page_count
                sync_selected_page_count = sync_view.selected_page_count
                business_status = "succeeded"
            elif command == "index-full":
                if self._telegram_index_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_INDEX_NOT_CONFIGURED",
                        message="Telegram full indexing is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                index_view = self._telegram_index_orchestrator.start_full_index_session(
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
                reply_text, reply_markup = self._build_full_index_warning(
                    view=index_view,
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
                index_status = index_view.state
                business_status = "succeeded"
            elif command == "index-status":
                if self._telegram_index_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_INDEX_NOT_CONFIGURED",
                        message="Telegram index status is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                index_result = self._telegram_index_orchestrator.get_index_status(
                    command_text=normalized_input_text,
                )
                reply_text = index_result.reply_text
                index_workflow_run_id = index_result.workflow_run_id
                index_status = index_result.status
                index_discovered_page_count = index_result.discovered_page_count
                index_processed_page_count = index_result.processed_page_count
                index_failed_page_count = index_result.failed_page_count
                index_remaining_page_count = index_result.remaining_page_count
                index_failure_reason = index_result.failure_reason
                index_estimated_cost_usd = index_result.estimated_cost_usd
                index_stale = index_result.stale
                business_status = "succeeded"
            elif command == "cost":
                if self._telegram_operator_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                        message="Telegram operator status is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                cost_result = self._telegram_operator_orchestrator.get_cost(
                    command_text=normalized_input_text,
                )
                reply_text = cost_result.reply_text
                cost_scope = cost_result.scope
                cost_workflow_run_id = cost_result.workflow_run_id
                cost_total_usd = cost_result.total_cost_usd
                cost_llm_usd = cost_result.llm_cost_usd
                cost_embedding_usd = cost_result.embedding_cost_usd
                cost_unknown_workflow_count = cost_result.unknown_cost_workflow_count
                cost_budget_status = cost_result.budget_status
                cost_budget_usd = cost_result.budget_usd
                cost_workflow_budget_exceeded_count = (
                    cost_result.workflow_budget_exceeded_count
                )
                cost_workflow_budget_usd = cost_result.workflow_budget_usd
                business_status = "succeeded"
            elif command == "workflow":
                if self._telegram_operator_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                        message="Telegram operator status is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                workflow_result = self._telegram_operator_orchestrator.get_workflow(
                    command_text=normalized_input_text,
                )
                reply_text = workflow_result.reply_text
                workflow_detail_run_id = workflow_result.workflow_run_id
                workflow_detail_type = workflow_result.workflow_type
                workflow_detail_status = workflow_result.workflow_status
                workflow_detail_failure_reason = workflow_result.failure_reason
                workflow_detail_age_seconds = workflow_result.age_seconds
                workflow_detail_stale = workflow_result.stale
                workflow_detail_estimated_cost_usd = (
                    workflow_result.estimated_cost_usd
                )
                workflow_recent_count = workflow_result.recent_workflow_count
                business_status = "succeeded"
            elif command == "status":
                if self._telegram_operator_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                        message="Telegram readiness status is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                status_result = self._telegram_operator_orchestrator.get_status(
                    command_text=normalized_input_text,
                )
                reply_text = status_result.reply_text
                status_liveness = status_result.liveness_status
                status_readiness = status_result.readiness_status
                status_checks = {
                    check.name: check.status for check in status_result.checks
                }
                business_status = "succeeded"
            elif command == "stats":
                if self._telegram_operator_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                        message="Telegram knowledge statistics are not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                stats_result = self._telegram_operator_orchestrator.get_stats(
                    command_text=normalized_input_text,
                )
                reply_text = stats_result.reply_text
                stats_page_count = stats_result.page_count
                stats_block_count = stats_result.block_count
                stats_chunk_count = stats_result.chunk_count
                stats_vector_count = stats_result.vector_count
                stats_proposal_count = stats_result.proposal_count
                stats_pending_proposal_count = stats_result.pending_proposal_count
                stats_accepted_proposal_count = stats_result.accepted_proposal_count
                stats_rejected_proposal_count = stats_result.rejected_proposal_count
                stats_latest_full_index_at = stats_result.latest_successful_full_index_at
                stats_latest_incremental_sync_at = (
                    stats_result.latest_successful_incremental_sync_at
                )
                business_status = "succeeded"
            elif command == "pending":
                if self._telegram_operator_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                        message="Telegram pending review is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                pending_result = self._telegram_operator_orchestrator.get_pending(
                    command_text=normalized_input_text,
                )
                reply_text = pending_result.reply_text
                pending_count = len(pending_result.items)
                reply_markup = self._build_pending_markup(
                    items=pending_result.items,
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
                business_status = "succeeded"
            elif command == "pages":
                if self._telegram_page_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_PAGES_NOT_CONFIGURED",
                        message="Telegram page orchestrator is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                pages_result = self._telegram_page_orchestrator.list_pages()
                reply_text = pages_result.reply_text
                reply_texts = pages_result.reply_texts or [reply_text]
            elif command == "ask":
                if self._telegram_qa_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_QA_NOT_CONFIGURED",
                        message="Telegram QA orchestrator is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                qa_result = await self._telegram_qa_orchestrator.handle_ask_command(
                    command_text=normalized_input_text,
                    request_workflow_id=request_workflow_id,
                )
                reply_text = qa_result.reply_text
                qa_workflow_run_id = qa_result.qa_workflow_run_id
                insufficient_info = qa_result.insufficient_info
                citations = qa_result.citation_paths
            elif command in {"accept", "reject"}:
                if self._telegram_review_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_REVIEW_NOT_CONFIGURED",
                        message="Telegram review orchestrator is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                review_result = await self._telegram_review_orchestrator.handle_review_command(
                    command=command,
                    command_text=normalized_input_text,
                    chat_id=normalized_chat_id,
                    request_workflow_id=request_workflow_id,
                )
                reply_text = review_result.reply_text
                review_workflow_run_id = review_result.review_workflow_run_id
                change_request_id = review_result.change_request_id
                change_request_status = review_result.change_request_status
                review_action = review_result.review_action
            elif command == "retry-proposal":
                if self._telegram_ingestion_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_INGESTION_NOT_CONFIGURED",
                        message="Telegram ingestion orchestrator is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                retry_session = self._telegram_ingestion_orchestrator.get_latest_upload(
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
                if retry_session is None:
                    raise TelegramGatewayError(
                        error_code="UPLOAD_SESSION_EXPIRED",
                        message="No failed proposal session is available. Use /ingest to start a new upload.",
                        http_status_code=HTTPStatus.GONE,
                        failure_reason="UPLOAD_SESSION_EXPIRED",
                    )
                ingestion_result = await self._telegram_ingestion_orchestrator.retry_existing_proposal(
                    session_id=retry_session.session_id,
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                    request_workflow_id=request_workflow_id,
                )
                business_status = "succeeded"
                source_document_id = ingestion_result.source_document_id
                change_request_id = ingestion_result.change_request_id
                source_type = ingestion_result.source_type
                target_notion_page_id = ingestion_result.target_notion_page_id
                target_notion_path = ingestion_result.target_notion_path
                target_set = bool(target_notion_page_id)
                reply_text = ingestion_result.reply_text
                if (
                    change_request_id is not None
                    and target_set
                    and ingestion_result.session_id
                    and self._telegram_ingestion_orchestrator.claim_upload_preview(
                        session_id=ingestion_result.session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    )
                ):
                    reply_markup = self._build_review_markup(
                        ingestion_result=ingestion_result,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    )
                    preview_session_id = ingestion_result.session_id
                    preview_delivery_required = True
                    preview_delivery_status = "pending"
            elif command == "ingest" or (has_media and command == "unknown"):
                if self._telegram_ingestion_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="TELEGRAM_INGESTION_NOT_CONFIGURED",
                        message="Telegram ingestion orchestrator is not configured",
                        http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                        failure_reason="UNKNOWN_ERROR",
                    )
                command = "ingest"
                target_notion_page_id = self._parse_ingest_target(normalized_input_text)
                if target_notion_page_id is not None and not has_media:
                    session = self._telegram_ingestion_orchestrator.get_latest_upload(
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    )
                    if session is None:
                        raise TelegramGatewayError(
                            error_code="UPLOAD_SESSION_EXPIRED",
                            message="No unexpired upload session was found. Please upload the file again.",
                            http_status_code=HTTPStatus.GONE,
                            failure_reason="UPLOAD_SESSION_EXPIRED",
                        )
                    target_page = self._find_page(target_notion_page_id)
                    if target_page is None:
                        raise TelegramGatewayError(
                            error_code="NOTION_PAGE_NOT_FOUND",
                            message="The selected Notion page is no longer indexed. Use /pages and choose again.",
                            http_status_code=HTTPStatus.NOT_FOUND,
                            failure_reason="NOTION_PAGE_NOT_FOUND",
                        )
                    ingestion_result = await self._telegram_ingestion_orchestrator.handle_target_selection(
                        session_id=session.session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                        target_notion_page_id=target_page.page_id,
                        target_notion_path=target_page.notion_path,
                        request_workflow_id=request_workflow_id,
                    )
                    business_status = "succeeded"
                    preview_session_id = session.session_id
                elif target_notion_page_id is None and not has_media:
                    session = self._telegram_ingestion_orchestrator.get_latest_upload(
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    )
                    if session is None:
                        raise TelegramGatewayError(
                            error_code="UPLOAD_SESSION_EXPIRED",
                            message="No unexpired upload session was found. Please upload a PDF or image first.",
                            http_status_code=HTTPStatus.GONE,
                            failure_reason="UPLOAD_SESSION_EXPIRED",
                        )
                    reply_text, reply_markup = self._build_page_picker(
                        session_id=session.session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    )
                    target_notion_page_id = session.target_notion_page_id
                elif target_notion_page_id is not None:
                    target_page = self._find_page(target_notion_page_id)
                    if target_page is None:
                        raise TelegramGatewayError(
                            error_code="NOTION_PAGE_NOT_FOUND",
                            message="The selected Notion page is no longer indexed. Use /pages and choose again.",
                            http_status_code=HTTPStatus.NOT_FOUND,
                            failure_reason="NOTION_PAGE_NOT_FOUND",
                        )
                    direct_session_id = self._build_upload_session_id(
                        update_id=update_id,
                        message_id=message_id,
                        media_group_id=media_group_id,
                    )
                    direct_session = self._telegram_ingestion_orchestrator.store_upload(
                        session_id=direct_session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                        media_group_id=media_group_id,
                        message_id=message_id,
                        document=document,
                        photos=photos,
                        command_text=normalized_input_text or None,
                    )
                    self._telegram_ingestion_orchestrator.mark_upload_awaiting_target(
                        session_id=direct_session.session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                    )
                    ingestion_result = await self._telegram_ingestion_orchestrator.handle_target_selection(
                        session_id=direct_session.session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                        target_notion_page_id=target_page.page_id,
                        target_notion_path=target_page.notion_path,
                        request_workflow_id=request_workflow_id,
                    )
                    business_status = "succeeded"
                    preview_session_id = direct_session.session_id
                    # Keep the text-command fallback compatible with the old
                    # user-facing page-id display; persisted proposal targets
                    # remain resolved canonical page rows.
                    ingestion_result.target_notion_path = target_page.page_id
                    ingestion_result.reply_text = ingestion_result.reply_text.replace(
                        f"Target Notion page: {target_page.notion_path}",
                        f"Target Notion page: {target_page.page_id}",
                    )
                else:
                    session_id = self._build_upload_session_id(
                        update_id=update_id,
                        message_id=message_id,
                        media_group_id=media_group_id,
                    )
                    session = self._telegram_ingestion_orchestrator.store_upload(
                        session_id=session_id,
                        chat_id=normalized_chat_id,
                        user_id=normalized_user_id,
                        media_group_id=media_group_id,
                        message_id=message_id,
                        document=document,
                        photos=photos,
                        command_text=normalized_input_text or None,
                    )
                    if media_group_id:
                        if self._queue_client is None:
                            raise TelegramGatewayError(
                                error_code="TELEGRAM_QUEUE_UNAVAILABLE",
                                message=(
                                    "Media groups require the Redis/RQ queue; "
                                    "please try a single PDF or image instead."
                                ),
                                http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                                failure_reason="TELEGRAM_QUEUE_UNAVAILABLE",
                            )
                        if self._telegram_ingestion_orchestrator.claim_upload_settle(
                            session_id=session.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                        ):
                            scheduled_session = self._telegram_ingestion_orchestrator.get_upload(
                                session_id=session.session_id,
                                chat_id=normalized_chat_id,
                                user_id=normalized_user_id,
                            )
                            self._schedule_upload_settle(
                                session_id=session.session_id,
                                chat_id=normalized_chat_id,
                                user_id=normalized_user_id,
                                request_workflow_id=request_workflow_id,
                                settle_version=(
                                    scheduled_session.settle_version
                                    if scheduled_session is not None
                                    else None
                                ),
                            )
                        if not self._telegram_ingestion_orchestrator.claim_upload_receipt(
                            session_id=session.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                        ):
                            reply_text = ""
                        else:
                            reply_text = (
                                "Received media group. I will finish collecting the "
                                "files, then show target pages."
                            )
                    else:
                        reply_text, reply_markup = self._build_page_picker(
                            session_id=session.session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                        )
                    target_notion_page_id = session.target_notion_page_id
                if ingestion_result is not None:
                    self._merge_stage_latency(
                        latency,
                        ingestion_result.latency_metadata,
                    )
                    source_document_id = ingestion_result.source_document_id
                    change_request_id = ingestion_result.change_request_id
                    source_type = ingestion_result.source_type
                    target_notion_page_id = ingestion_result.target_notion_page_id
                    target_notion_path = ingestion_result.target_notion_path
                    target_set = bool(target_notion_page_id)
                    if not reply_text:
                        reply_text = ingestion_result.reply_text
                    if (
                        change_request_id is not None
                        and target_set
                        and reply_markup is None
                    ):
                        if (
                            ingestion_result.session_id
                            and self._telegram_ingestion_orchestrator.claim_upload_preview(
                                session_id=ingestion_result.session_id,
                                chat_id=normalized_chat_id,
                                user_id=normalized_user_id,
                            )
                        ):
                            reply_markup = self._build_review_markup(
                                ingestion_result=ingestion_result,
                                chat_id=normalized_chat_id,
                                user_id=normalized_user_id,
                            )
                            preview_session_id = ingestion_result.session_id
                            preview_delivery_required = True
                            preview_delivery_status = "pending"
            else:
                reply_text = self._build_reply_for_command(command)

            if business_status == "not_started":
                business_status = "succeeded"

            telegram_message_id: Optional[int] = None

            outgoing_texts = reply_texts or ([reply_text] if reply_text else [])
            if outgoing_texts:
                preview_delivery_started = perf_counter()
                telegram_message_id = None
                for text_index, outgoing_text in enumerate(outgoing_texts):
                    tool_result = await self._tool_registry.call_tool(
                        TELEGRAM_BOT_TOOL_NAME,
                        context=ToolContext(
                            workflow_id=request_workflow_id,
                            metadata={
                                "operation": "telegram_reply",
                                "command": command,
                                "chat_id": normalized_chat_id,
                            },
                        ),
                        arguments={
                            "chat_id": normalized_chat_id,
                            "text": outgoing_text,
                            **(
                                {"reply_markup": reply_markup}
                                if reply_markup is not None and text_index == 0
                                else {}
                            ),
                        },
                    )
                    if tool_result.is_error:
                        break
                if tool_result.is_error:
                    if preview_delivery_required:
                        latency.add(
                            preview_delivery_ms=elapsed_ms(preview_delivery_started)
                        )
                    error_code = "UNKNOWN_ERROR"
                    error_message = "Telegram reply failed"
                    if tool_result.error is not None:
                        error_code = tool_result.error.code
                        error_message = tool_result.error.message
                    if preview_delivery_required:
                        preview_delivery_status = "failed"
                        if preview_session_id is not None:
                            self._telegram_ingestion_orchestrator.complete_upload_preview(
                                session_id=preview_session_id,
                                chat_id=normalized_chat_id,
                                user_id=normalized_user_id,
                                success=False,
                                failure_reason="TELEGRAM_PREVIEW_DELIVERY_FAILED",
                            )
                        raise TelegramGatewayError(
                            error_code="TELEGRAM_PREVIEW_DELIVERY_FAILED",
                            message="Telegram proposal preview could not be delivered",
                            http_status_code=HTTPStatus.BAD_GATEWAY,
                            failure_reason="TELEGRAM_PREVIEW_DELIVERY_FAILED",
                            workflow_run_id=workflow_run.id,
                            metadata={
                                "business_status": "succeeded",
                                "callback_ack_status": callback_ack_status,
                                "preview_delivery_status": "failed",
                                "preview_delivery_failure_reason": (
                                    "TELEGRAM_PREVIEW_DELIVERY_FAILED"
                                ),
                                "source_document_id": source_document_id,
                                "change_request_id": change_request_id,
                                **latency.as_dict(),
                            },
                        )
                    raise TelegramGatewayError(
                        error_code=error_code,
                        message=error_message,
                        http_status_code=self._http_status_for_tool_error(error_code),
                        failure_reason=self._normalize_failure_reason(error_code),
                    )

                structured_content = tool_result.structured_content or {}
                raw_message_id = structured_content.get("message_id")
                if isinstance(raw_message_id, int):
                    telegram_message_id = raw_message_id
                if preview_delivery_required:
                    latency.add(
                        preview_delivery_ms=elapsed_ms(preview_delivery_started)
                    )
                    preview_delivery_status = "succeeded"
                    if preview_session_id is not None:
                        self._telegram_ingestion_orchestrator.complete_upload_preview(
                            session_id=preview_session_id,
                            chat_id=normalized_chat_id,
                            user_id=normalized_user_id,
                            success=True,
                        )

            latency.add(total_business_ms=elapsed_ms(business_started))
            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    {
                        "operation": "telegram_webhook",
                        "handled": True,
                        "command": command,
                        "chat_id": normalized_chat_id,
                        "telegram_message_id": telegram_message_id,
                        "source_document_id": source_document_id,
                        "change_request_id": change_request_id,
                        "source_type": source_type,
                        "target_set": target_set,
                        "qa_workflow_run_id": qa_workflow_run_id,
                        "insufficient_info": insufficient_info,
                        "citation_count": len(citations),
                        "review_workflow_run_id": review_workflow_run_id,
                        "review_action": review_action,
                        "callback_action": callback_action_name,
                        "change_request_status": change_request_status,
                        "business_status": business_status,
                        "callback_ack_status": callback_ack_status,
                        "callback_ack_failure_reason": callback_ack_failure_reason,
                        "preview_delivery_status": preview_delivery_status,
                        "sync_workflow_run_id": sync_workflow_run_id,
                        "sync_status": sync_status,
                        "sync_discovered_page_count": sync_discovered_page_count,
                        "sync_selected_page_count": sync_selected_page_count,
                        "sync_succeeded_page_count": sync_succeeded_page_count,
                        "sync_failed_page_count": sync_failed_page_count,
                        "index_workflow_run_id": index_workflow_run_id,
                        "index_status": index_status,
                        "index_discovered_page_count": index_discovered_page_count,
                        "index_processed_page_count": index_processed_page_count,
                        "index_failed_page_count": index_failed_page_count,
                        "index_remaining_page_count": index_remaining_page_count,
                        "index_failure_reason": index_failure_reason,
                        "index_estimated_cost_usd": index_estimated_cost_usd,
                        "index_stale": index_stale,
                        "cost_scope": cost_scope,
                        "cost_workflow_run_id": cost_workflow_run_id,
                        "cost_total_usd": cost_total_usd,
                        "cost_llm_usd": cost_llm_usd,
                        "cost_embedding_usd": cost_embedding_usd,
                        "cost_unknown_workflow_count": cost_unknown_workflow_count,
                        "cost_budget_status": cost_budget_status,
                        "cost_budget_usd": cost_budget_usd,
                        "cost_workflow_budget_exceeded_count": cost_workflow_budget_exceeded_count,
                        "cost_workflow_budget_usd": cost_workflow_budget_usd,
                        "workflow_detail_run_id": workflow_detail_run_id,
                        "workflow_detail_type": workflow_detail_type,
                        "workflow_detail_status": workflow_detail_status,
                        "workflow_detail_failure_reason": workflow_detail_failure_reason,
                        "workflow_detail_age_seconds": workflow_detail_age_seconds,
                        "workflow_detail_stale": workflow_detail_stale,
                        "workflow_detail_estimated_cost_usd": workflow_detail_estimated_cost_usd,
                        "workflow_recent_count": workflow_recent_count,
                        "pending_count": pending_count,
                        "status_liveness": status_liveness,
                        "status_readiness": status_readiness,
                        "status_checks": status_checks,
                        "stats_page_count": stats_page_count,
                        "stats_block_count": stats_block_count,
                        "stats_chunk_count": stats_chunk_count,
                        "stats_vector_count": stats_vector_count,
                        "stats_proposal_count": stats_proposal_count,
                        "stats_pending_proposal_count": stats_pending_proposal_count,
                        "stats_accepted_proposal_count": stats_accepted_proposal_count,
                        "stats_rejected_proposal_count": stats_rejected_proposal_count,
                        "stats_latest_full_index_at": stats_latest_full_index_at,
                        "stats_latest_incremental_sync_at": stats_latest_incremental_sync_at,
                        **latency.as_dict(),
                    },
                    sort_keys=True,
                ),
            )

            return TelegramGatewayResult(
                workflow_run_id=workflow_run.id,
                status="succeeded",
                handled=True,
                command=command,
                reply_text=reply_text,
                telegram_message_id=telegram_message_id,
                skipped_reason=None,
                source_document_id=source_document_id,
                change_request_id=change_request_id,
                source_type=source_type,
                target_notion_page_id=target_notion_page_id,
                qa_workflow_run_id=qa_workflow_run_id,
                insufficient_info=insufficient_info,
                citations=citations,
                review_workflow_run_id=review_workflow_run_id,
                review_action=review_action,
                change_request_status=change_request_status,
                target_set=target_set,
                business_status=business_status,
                callback_ack_status=callback_ack_status,
                preview_delivery_status=preview_delivery_status,
                sync_workflow_run_id=sync_workflow_run_id,
                sync_status=sync_status,
                sync_discovered_page_count=sync_discovered_page_count,
                sync_selected_page_count=sync_selected_page_count,
                sync_succeeded_page_count=sync_succeeded_page_count,
                sync_failed_page_count=sync_failed_page_count,
                index_workflow_run_id=index_workflow_run_id,
                index_status=index_status,
                index_discovered_page_count=index_discovered_page_count,
                index_processed_page_count=index_processed_page_count,
                index_failed_page_count=index_failed_page_count,
                index_remaining_page_count=index_remaining_page_count,
                index_failure_reason=index_failure_reason,
                index_estimated_cost_usd=index_estimated_cost_usd,
                index_stale=index_stale,
                cost_scope=cost_scope,
                cost_workflow_run_id=cost_workflow_run_id,
                cost_total_usd=cost_total_usd,
                cost_llm_usd=cost_llm_usd,
                cost_embedding_usd=cost_embedding_usd,
                cost_unknown_workflow_count=cost_unknown_workflow_count,
                cost_budget_status=cost_budget_status,
                cost_budget_usd=cost_budget_usd,
                cost_workflow_budget_exceeded_count=cost_workflow_budget_exceeded_count,
                cost_workflow_budget_usd=cost_workflow_budget_usd,
                workflow_detail_run_id=workflow_detail_run_id,
                workflow_detail_type=workflow_detail_type,
                workflow_detail_status=workflow_detail_status,
                workflow_detail_failure_reason=workflow_detail_failure_reason,
                workflow_detail_age_seconds=workflow_detail_age_seconds,
                workflow_detail_stale=workflow_detail_stale,
                workflow_detail_estimated_cost_usd=workflow_detail_estimated_cost_usd,
                workflow_recent_count=workflow_recent_count,
                pending_count=pending_count,
                status_liveness=status_liveness,
                status_readiness=status_readiness,
                status_checks=status_checks,
                stats_page_count=stats_page_count,
                stats_block_count=stats_block_count,
                stats_chunk_count=stats_chunk_count,
                stats_vector_count=stats_vector_count,
                stats_proposal_count=stats_proposal_count,
                stats_pending_proposal_count=stats_pending_proposal_count,
                stats_accepted_proposal_count=stats_accepted_proposal_count,
                stats_rejected_proposal_count=stats_rejected_proposal_count,
                stats_latest_full_index_at=stats_latest_full_index_at,
                stats_latest_incremental_sync_at=stats_latest_incremental_sync_at,
            )
        except WorkflowRunAuditUpdateError:
            raise
        except TelegramIngestionError as exc:
            failed_session = None
            if self._telegram_ingestion_orchestrator is not None:
                failed_session = self._telegram_ingestion_orchestrator.get_latest_upload(
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
            propagated_metadata = {
                key: value
                for key, value in exc.metadata.items()
                if key in _SAFE_PROPOSAL_FAILURE_METADATA_FIELDS
            }
            failed_source_document_id = source_document_id or propagated_metadata.get(
                "source_document_id"
            ) or (
                failed_session.source_document_id if failed_session is not None else None
            )
            session_metadata = {
                "source_attachment_count": (
                    len(failed_session.attachments)
                    if failed_session is not None
                    else None
                ),
                "session_state": (
                    failed_session.state if failed_session is not None else None
                ),
                "session_retry_available": bool(
                    failed_session is not None
                    and failed_session.source_document_id is not None
                    and failed_session.failure_reason == "LLM_OUTPUT_INVALID"
                ),
            }
            failure_metadata = {
                "business_status": "failed",
                "callback_action": callback_action_name,
                "callback_ack_status": callback_ack_status,
                "preview_delivery_status": preview_delivery_status,
                **propagated_metadata,
                "source_document_id": failed_source_document_id,
                **session_metadata,
            }
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                metadata=failure_metadata,
            )
            raise TelegramGatewayError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
                workflow_run_id=workflow_run.id,
                metadata=failure_metadata,
            ) from exc

        except TelegramIndexError as exc:
            failure_metadata = {
                "business_status": "failed",
                "callback_action": callback_action_name,
                "callback_ack_status": callback_ack_status,
                "preview_delivery_status": preview_delivery_status,
                **{
                    key: value
                    for key, value in exc.metadata.items()
                    if key
                    in {
                        "index_workflow_run_id",
                        "index_status",
                        "index_discovered_page_count",
                        "index_processed_page_count",
                        "index_failed_page_count",
                        "index_remaining_page_count",
                    }
                },
            }
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                metadata=failure_metadata,
            )
            raise TelegramGatewayError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
                workflow_run_id=workflow_run.id,
                metadata=failure_metadata,
            ) from exc
        except TelegramOperatorError as exc:
            failure_metadata = {
                "business_status": "failed",
                "callback_action": callback_action_name,
                "callback_ack_status": callback_ack_status,
                "preview_delivery_status": preview_delivery_status,
            }
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                metadata=failure_metadata,
            )
            raise TelegramGatewayError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
                workflow_run_id=workflow_run.id,
                metadata=failure_metadata,
            ) from exc
        except TelegramSyncError as exc:
            failure_metadata = {
                "business_status": "failed",
                "callback_action": callback_action_name,
                "callback_ack_status": callback_ack_status,
                "preview_delivery_status": preview_delivery_status,
                **{
                    key: value
                    for key, value in exc.metadata.items()
                    if key
                    in {
                        "sync_discovered_page_count",
                        "sync_selected_page_count",
                    }
                },
            }
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                metadata=failure_metadata,
            )
            raise TelegramGatewayError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
                workflow_run_id=workflow_run.id,
                metadata=failure_metadata,
            ) from exc
        except TelegramQAError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                metadata={
                    "business_status": "failed",
                    "callback_action": callback_action_name,
                    "callback_ack_status": callback_ack_status,
                    "preview_delivery_status": preview_delivery_status,
                },
            )
            raise TelegramGatewayError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
                workflow_run_id=workflow_run.id,
                metadata={
                    "business_status": "failed",
                    "callback_action": callback_action_name,
                    "callback_ack_status": callback_ack_status,
                    "preview_delivery_status": preview_delivery_status,
                },
            ) from exc
        except TelegramReviewError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                metadata={
                    "business_status": "failed",
                    "callback_ack_status": callback_ack_status,
                    "preview_delivery_status": preview_delivery_status,
                },
            )
            raise TelegramGatewayError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
                workflow_run_id=workflow_run.id,
                metadata={
                    "business_status": "failed",
                    "callback_ack_status": callback_ack_status,
                    "preview_delivery_status": preview_delivery_status,
                },
            ) from exc
        except TelegramGatewayError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
                metadata={
                    "business_status": exc.metadata.get(
                        "business_status", business_status
                    ),
                    "callback_action": exc.metadata.get(
                        "callback_action", callback_action_name
                    ),
                    "callback_ack_status": exc.metadata.get(
                        "callback_ack_status", callback_ack_status
                    ),
                    "preview_delivery_status": exc.metadata.get(
                        "preview_delivery_status", preview_delivery_status
                    ),
                    **{
                        key: value
                        for key, value in exc.metadata.items()
                        if key
                        not in {
                            "business_status",
                            "callback_ack_status",
                            "preview_delivery_status",
                        }
                    },
                },
            )
            raise TelegramGatewayError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=workflow_run.id,
                metadata=exc.metadata,
            ) from exc
        except Exception as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                error_code="TELEGRAM_GATEWAY_FAILED",
                metadata={
                    "business_status": "failed",
                    "callback_action": callback_action_name,
                    "callback_ack_status": callback_ack_status,
                    "preview_delivery_status": preview_delivery_status,
                },
            )
            raise TelegramGatewayError(
                error_code="TELEGRAM_GATEWAY_FAILED",
                message=(
                    "Telegram gateway workflow failed: "
                    f"{sanitize_sensitive_text(str(exc))}"
                ),
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

    def _require_allowed_chat(self, chat_id: Optional[str]) -> None:
        if self._trust_boundary is None:
            return
        try:
            self._trust_boundary.require_allowed_telegram_chat(chat_id)
        except TrustBoundaryError as exc:
            raise TelegramGatewayError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
            ) from exc

    def _claim_update(self, update_id: Optional[int]) -> Optional[TelegramUpdateClaim]:
        if self._update_idempotency_service is None:
            return None
        try:
            return self._update_idempotency_service.claim(update_id)
        except TelegramUpdateIdempotencyError as exc:
            raise TelegramGatewayError(
                error_code="TELEGRAM_UPDATE_LEDGER_FAILED",
                message="Telegram update ledger could not be claimed",
                http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                failure_reason="TELEGRAM_UPDATE_LEDGER_FAILED",
            ) from exc

    def _replay_or_report_duplicate(
        self,
        claim: TelegramUpdateClaim,
    ) -> TelegramGatewayResult:
        if claim.status == "succeeded":
            if not claim.result_json:
                raise TelegramGatewayError(
                    error_code="TELEGRAM_UPDATE_LEDGER_FAILED",
                    message="Telegram update ledger result is missing",
                    http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                    failure_reason="TELEGRAM_UPDATE_LEDGER_FAILED",
                    workflow_run_id=claim.workflow_run_id,
                )
            try:
                return TelegramGatewayResult(**json.loads(claim.result_json))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise TelegramGatewayError(
                    error_code="TELEGRAM_UPDATE_LEDGER_FAILED",
                    message="Telegram update ledger result is invalid",
                    http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                    failure_reason="TELEGRAM_UPDATE_LEDGER_FAILED",
                    workflow_run_id=claim.workflow_run_id,
                ) from exc

        if claim.status == "failed":
            failure = {}
            if claim.failure_json:
                try:
                    failure = json.loads(claim.failure_json)
                except (TypeError, ValueError, json.JSONDecodeError):
                    failure = {}
            raise TelegramGatewayError(
                error_code=str(failure.get("error_code", "TELEGRAM_GATEWAY_FAILED")),
                message=str(failure.get("message", "Telegram update previously failed")),
                http_status_code=int(failure.get("http_status_code", HTTPStatus.INTERNAL_SERVER_ERROR)),
                failure_reason=str(failure.get("failure_reason", "UNKNOWN_ERROR")),
                workflow_run_id=claim.workflow_run_id,
            )

        return TelegramGatewayResult(
            workflow_run_id=claim.workflow_run_id,
            status="running",
            handled=False,
            command=None,
            reply_text=None,
            telegram_message_id=None,
            skipped_reason="DUPLICATE_UPDATE_IN_PROGRESS",
            source_document_id=None,
            change_request_id=None,
            source_type=None,
            target_notion_page_id=None,
            qa_workflow_run_id=None,
            insufficient_info=None,
            citations=[],
            review_workflow_run_id=None,
            review_action=None,
            change_request_status=None,
        )

    def _mark_update_failed(
        self,
        update_id: Optional[int],
        error: TelegramGatewayError,
    ) -> None:
        if self._update_idempotency_service is None:
            return
        self._update_idempotency_service.mark_failed(
            update_id,
            workflow_run_id=error.workflow_run_id,
            failure={
                "error_code": error.error_code,
                "message": error.message,
                "http_status_code": error.http_status_code,
                "failure_reason": error.failure_reason,
            },
        )

    async def settle_upload_session(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        request_workflow_id: str,
        settle_version: Optional[int] = None,
    ) -> str:
        if self._telegram_ingestion_orchestrator is None:
            raise TelegramGatewayError(
                error_code="TELEGRAM_INGESTION_NOT_CONFIGURED",
                message="Telegram ingestion orchestrator is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        session = self._telegram_ingestion_orchestrator.get_upload(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        if session is None:
            raise TelegramGatewayError(
                error_code="UPLOAD_SESSION_EXPIRED",
                message="This media group session expired before it could be settled.",
                http_status_code=HTTPStatus.GONE,
                failure_reason="UPLOAD_SESSION_EXPIRED",
            )
        if not session.attachments:
            self._telegram_ingestion_orchestrator.fail_upload(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                failure_reason="EMPTY_UPLOAD",
            )
            raise TelegramGatewayError(
                error_code="EMPTY_UPLOAD",
                message="No media was found in this upload session. Please upload the file again.",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="EMPTY_UPLOAD",
            )
        if not self._telegram_ingestion_orchestrator.claim_upload_settled(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            settle_version=settle_version,
        ):
            return "stale"
        session = self._telegram_ingestion_orchestrator.get_upload(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        if session is None or not session.attachments:
            return "stale"
        if not self._telegram_ingestion_orchestrator.claim_upload_picker(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        ):
            return "duplicate"
        reply_text, reply_markup = self._build_page_picker(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            claim_picker=False,
        )
        tool_result = await self._tool_registry.call_tool(
            TELEGRAM_BOT_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "telegram_media_group_picker",
                    "chat_id": chat_id,
                    "session_id": session_id,
                },
            ),
            arguments={
                "chat_id": chat_id,
                "text": reply_text,
                "reply_markup": reply_markup,
            },
        )
        if tool_result.is_error:
            error_code = tool_result.error.code if tool_result.error else "TELEGRAM_SEND_FAILED"
            raise TelegramGatewayError(
                error_code=error_code,
                message="Telegram page picker could not be sent.",
                http_status_code=self._http_status_for_tool_error(error_code),
                failure_reason=self._normalize_failure_reason(error_code),
            )
        return "settled"

    def _parse_command(self, text: str) -> str:
        if not text.strip():
            return "unknown"
        command_text = text.split(maxsplit=1)[0].strip().lower()
        if command_text.startswith("/"):
            command_text = command_text[1:]
        if not command_text:
            return "unknown"
        return command_text

    def _timeout_seconds_for_webhook(
        self,
        *,
        chat_id: Optional[str],
        user_id: Optional[str],
        text: Optional[str],
        caption: Optional[str],
        callback: Optional[TelegramCallbackAttachment],
    ) -> int:
        """Select a long bound only for deterministically identified Accept work."""

        if callback is not None:
            normalized_chat_id = (chat_id or "").strip()
            normalized_user_id = (user_id or normalized_chat_id).strip()
            try:
                callback_action = self._resolve_callback_action(
                    callback=callback,
                    chat_id=normalized_chat_id,
                    user_id=normalized_user_id,
                )
            except TelegramGatewayError:
                # The worker remains responsible for the normal invalid/expired
                # callback outcome. An unresolved callback is never granted the
                # long-running review bound.
                callback_action = None
            if (
                callback_action is not None
                and callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_REVIEW
                and callback_action.action == "accept"
            ):
                return self._telegram_review_job_timeout_seconds
            return self._telegram_job_timeout_seconds

        command_text = self._select_command_text(
            text=(text or "").strip(),
            caption=(caption or "").strip(),
        )
        if self._parse_command(command_text) == "accept":
            return self._telegram_review_job_timeout_seconds
        return self._telegram_job_timeout_seconds

    def _select_command_text(self, *, text: str, caption: str) -> str:
        for candidate in (text, caption):
            if candidate.startswith("/"):
                return candidate
        return text or caption

    def _build_upload_session_id(
        self,
        *,
        update_id: Optional[int],
        message_id: Optional[int],
        media_group_id: Optional[str],
    ) -> str:
        if media_group_id:
            digest = hashlib.sha256(media_group_id.encode("utf-8")).hexdigest()[:24]
            return f"group-{digest}"
        if update_id is not None:
            return f"single-update-{update_id}"
        if message_id is not None:
            return f"single-message-{message_id}"
        return f"single-{uuid.uuid4().hex}"

    def _find_page(self, page_id: str):
        if self._telegram_page_orchestrator is None:
            return None
        pages = self._telegram_page_orchestrator.list_pages().pages
        return next((page for page in pages if page.page_id == page_id), None)

    def _resolve_callback_action(
        self,
        *,
        callback: TelegramCallbackAttachment,
        chat_id: str,
        user_id: str,
    ):
        raw_data = callback.callback_data.strip()
        if not raw_data.startswith("ll:"):
            raise TelegramGatewayError(
                error_code="INVALID_CALLBACK",
                message="This button is invalid or expired. Please upload the file again.",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_CALLBACK",
            )
        token = raw_data[3:].strip()
        if not token:
            raise TelegramGatewayError(
                error_code="INVALID_CALLBACK",
                message="This button is invalid or expired. Please upload the file again.",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_CALLBACK",
            )
        if self._telegram_session_store is not None:
            action = self._telegram_session_store.resolve_callback(
                token=token,
                chat_id=chat_id,
                user_id=user_id,
            )
        elif self._telegram_ingestion_orchestrator is not None:
            action = self._telegram_ingestion_orchestrator.resolve_callback(
                token=token,
                chat_id=chat_id,
                user_id=user_id,
            )
        else:
            action = None
        if action is None:
            raise TelegramGatewayError(
                error_code="INVALID_CALLBACK",
                message="This button is invalid or expired. Please upload the file again.",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_CALLBACK",
            )
        return action

    def _validate_callback_action(
        self,
        *,
        callback_action,
        chat_id: str,
        user_id: str,
    ) -> None:
        """Validate callback ownership/state before acknowledging or doing work."""

        # Validate the semantic family first. Review callbacks must not depend
        # on upload-session state because the session may already be in the
        # ready-for-review/proposal-created state when the user clicks Accept.
        if callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_REVIEW:
            if callback_action.action not in TELEGRAM_REVIEW_CALLBACK_ACTIONS:
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This review action is invalid.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            if callback_action.change_request_id is None or callback_action.change_request_id <= 0:
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This review action is invalid.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            return

        if callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_OPERATOR:
            if callback_action.action not in TELEGRAM_OPERATOR_CALLBACK_ACTIONS:
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This sync action is invalid.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            if not callback_action.session_id:
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This operator session is invalid.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            if callback_action.action == "pending_view":
                if (
                    callback_action.change_request_id is None
                    or callback_action.change_request_id <= 0
                ):
                    raise TelegramGatewayError(
                        error_code="INVALID_CALLBACK",
                        message="This pending proposal action is invalid.",
                        http_status_code=HTTPStatus.BAD_REQUEST,
                        failure_reason="INVALID_CALLBACK",
                    )
                return
            if callback_action.action == "sync_toggle" and not callback_action.target_notion_page_id:
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This sync page selection is invalid.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            return

        if callback_action.callback_kind == TELEGRAM_CALLBACK_KIND_PICKER:
            if callback_action.action not in TELEGRAM_PICKER_CALLBACK_ACTIONS:
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This button is no longer valid. Please upload again.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            mode = callback_action.picker_mode
            if callback_action.action == "change_target_select":
                mode = "change_target"
            if mode not in {"upload", "change_target"}:
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This page picker action is invalid.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            if mode == "change_target" and (
                callback_action.change_request_id is None
                or callback_action.change_request_id <= 0
            ):
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This target selection is invalid.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            if callback_action.action == "open_page":
                node = self._telegram_page_orchestrator.build_hierarchy().get_node(
                    callback_action.navigation_page_id or ""
                ) if self._telegram_page_orchestrator is not None else None
                if node is None or not node.has_children:
                    raise TelegramGatewayError(
                        error_code="INVALID_CALLBACK",
                        message="This page cannot be opened.",
                        http_status_code=HTTPStatus.BAD_REQUEST,
                        failure_reason="INVALID_CALLBACK",
                    )
            if callback_action.action in {"back", "next_page", "previous_page"}:
                navigation_node = (
                    self._telegram_page_orchestrator.build_hierarchy().get_node(
                        callback_action.navigation_page_id or ""
                    )
                    if self._telegram_page_orchestrator is not None
                    else None
                )
                if navigation_node is None or (
                    callback_action.action in {"next_page", "previous_page"}
                    and not navigation_node.has_children
                ):
                    raise TelegramGatewayError(
                        error_code="INVALID_CALLBACK",
                        message="This page navigation is no longer valid.",
                        http_status_code=HTTPStatus.BAD_REQUEST,
                        failure_reason="INVALID_CALLBACK",
                    )
            if callback_action.action == "back" and not callback_action.navigation_page_id:
                raise TelegramGatewayError(
                    error_code="INVALID_CALLBACK",
                    message="This navigation action is invalid.",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                    failure_reason="INVALID_CALLBACK",
                )
            if callback_action.action in {"select_target", "change_target_select"}:
                if (
                    self._telegram_ingestion_orchestrator is None
                    or not callback_action.target_notion_page_id
                    or (
                        callback_action.action == "select_target"
                        and not callback_action.target_notion_path
                    )
                ):
                    raise TelegramGatewayError(
                        error_code="INVALID_CALLBACK",
                        message="This page selection is invalid. Please upload again.",
                        http_status_code=HTTPStatus.BAD_REQUEST,
                        failure_reason="INVALID_CALLBACK",
                    )
                if callback_action.action == "select_target":
                    selected_page = self._find_page(callback_action.target_notion_page_id)
                    if (
                        selected_page is None
                        or selected_page.notion_path != callback_action.target_notion_path
                    ):
                        raise TelegramGatewayError(
                            error_code="NOTION_PAGE_NOT_FOUND",
                            message="The selected Notion page is no longer indexed. Use /pages and choose again.",
                            http_status_code=HTTPStatus.NOT_FOUND,
                            failure_reason="NOTION_PAGE_NOT_FOUND",
                        )
            if mode == "upload":
                if self._telegram_ingestion_orchestrator is None:
                    raise TelegramGatewayError(
                        error_code="INVALID_CALLBACK",
                        message="This upload picker is invalid.",
                        http_status_code=HTTPStatus.BAD_REQUEST,
                        failure_reason="INVALID_CALLBACK",
                    )
                session = self._telegram_ingestion_orchestrator.get_upload(
                    session_id=callback_action.session_id,
                    chat_id=chat_id,
                    user_id=user_id,
                )
                if session is None:
                    raise TelegramGatewayError(
                        error_code="UPLOAD_SESSION_EXPIRED",
                        message="This upload session expired. Please upload the file again.",
                        http_status_code=HTTPStatus.GONE,
                        failure_reason="UPLOAD_SESSION_EXPIRED",
                    )
                if session.state not in {
                    "awaiting_target",
                    "processing",
                    "proposal_created",
                } and not (
                    session.state == "failed"
                    and session.failure_reason == "LLM_OUTPUT_INVALID"
                    and session.source_document_id is not None
                    and session.source_type == "screenshot"
                ):
                    raise TelegramGatewayError(
                        error_code="UPLOAD_SESSION_INVALID",
                        message=(
                            "This upload session is no longer selectable. Use "
                            "/retry-proposal for the existing source."
                        ),
                        http_status_code=HTTPStatus.CONFLICT,
                        failure_reason="UPLOAD_SESSION_INVALID",
                    )
            return

        raise TelegramGatewayError(
            error_code="INVALID_CALLBACK",
            message="This button is no longer valid. Please upload again.",
            http_status_code=HTTPStatus.BAD_REQUEST,
            failure_reason="INVALID_CALLBACK",
        )

    def _build_page_picker(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        claim_picker: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        if self._telegram_page_orchestrator is None or self._telegram_ingestion_orchestrator is None:
            raise TelegramGatewayError(
                error_code="TELEGRAM_PAGES_NOT_CONFIGURED",
                message="Telegram page picker is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        if not self._telegram_page_orchestrator.list_pages().pages:
            raise TelegramGatewayError(
                error_code="NOTION_PAGES_EMPTY",
                message="No indexed Notion pages are available. Index Notion pages, then upload again.",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="NOTION_PAGE_NOT_FOUND",
            )
        if claim_picker:
            self._telegram_ingestion_orchestrator.claim_upload_picker(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
            )
        return self._build_hierarchy_picker(
            mode="upload",
            session_id=session_id,
            change_request_id=None,
            chat_id=chat_id,
            user_id=user_id,
            current_page_id=None,
            page_number=1,
        )

    def _build_sync_picker(
        self,
        *,
        view: TelegramSyncView,
        chat_id: str,
        user_id: str,
    ) -> tuple[str, dict[str, Any]]:
        if self._telegram_session_store is None:
            raise TelegramGatewayError(
                error_code="TELEGRAM_SYNC_NOT_CONFIGURED",
                message="Telegram sync session store is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        selected = set(view.selected_page_ids)
        lines = [
            "🔄 Select Notion pages to synchronize",
            "",
            (
                f"Selected {view.selected_page_count}/{TELEGRAM_SYNC_MAX_SELECTED_PAGES}. "
                "This reads Notion and replaces only derived index data."
            ),
            "Confirm when ready; no Notion content will be written.",
            "",
        ]
        buttons: list[list[dict[str, str]]] = []
        for page in view.pages:
            marker = "☑" if page.page_id in selected else "☐"
            lines.append(f"{marker} {page.display_path}")
            token = self._telegram_session_store.create_callback(
                session_id=view.session_id,
                chat_id=chat_id,
                user_id=user_id,
                action="sync_toggle",
                callback_kind=TELEGRAM_CALLBACK_KIND_OPERATOR,
                target_notion_page_id=page.page_id,
                target_notion_path=page.display_path,
            )
            buttons.append(
                [{"text": f"{marker} {page.display_path}", "callback_data": f"ll:{token}"}]
            )
        if view.selected_page_count:
            confirm_token = self._telegram_session_store.create_callback(
                session_id=view.session_id,
                chat_id=chat_id,
                user_id=user_id,
                action="sync_confirm",
                callback_kind=TELEGRAM_CALLBACK_KIND_OPERATOR,
            )
            buttons.append(
                [{"text": "Confirm sync", "callback_data": f"ll:{confirm_token}"}]
            )
        cancel_token = self._telegram_session_store.create_callback(
            session_id=view.session_id,
            chat_id=chat_id,
            user_id=user_id,
            action="sync_cancel",
            callback_kind=TELEGRAM_CALLBACK_KIND_OPERATOR,
        )
        buttons.append([{"text": "Cancel", "callback_data": f"ll:{cancel_token}"}])
        return "\n".join(lines), {"inline_keyboard": buttons}

    def _build_full_index_warning(
        self,
        *,
        view: TelegramFullIndexView,
        chat_id: str,
        user_id: str,
    ) -> tuple[str, dict[str, Any]]:
        if self._telegram_session_store is None:
            raise TelegramGatewayError(
                error_code="TELEGRAM_INDEX_NOT_CONFIGURED",
                message="Telegram index callback store is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        confirm_token = self._telegram_session_store.create_callback(
            session_id=view.session_id,
            chat_id=chat_id,
            user_id=user_id,
            action="index_full_confirm",
            callback_kind=TELEGRAM_CALLBACK_KIND_OPERATOR,
        )
        cancel_token = self._telegram_session_store.create_callback(
            session_id=view.session_id,
            chat_id=chat_id,
            user_id=user_id,
            action="index_full_cancel",
            callback_kind=TELEGRAM_CALLBACK_KIND_OPERATOR,
        )
        return view.reply_text, {
            "inline_keyboard": [
                [{"text": "Confirm full index", "callback_data": f"ll:{confirm_token}"}],
                [{"text": "Cancel", "callback_data": f"ll:{cancel_token}"}],
            ]
        }

    def _build_hierarchy_picker(
        self,
        *,
        mode: str,
        session_id: str,
        change_request_id: Optional[int],
        chat_id: str,
        user_id: str,
        current_page_id: Optional[str],
        page_number: int,
    ) -> tuple[str, dict[str, Any]]:
        if self._telegram_page_orchestrator is None or self._telegram_ingestion_orchestrator is None:
            raise TelegramGatewayError(
                error_code="TELEGRAM_PAGES_NOT_CONFIGURED",
                message="Telegram page picker is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        try:
            view = self._telegram_page_orchestrator.build_picker_view(
                mode=mode,
                current_page_id=current_page_id,
                page_number=page_number,
            )
        except KeyError as exc:
            raise TelegramGatewayError(
                error_code="INVALID_CALLBACK",
                message="This page navigation is no longer valid. Please open the picker again.",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_CALLBACK",
            ) from exc
        buttons: list[list[dict[str, str]]] = []
        for item in view.buttons:
            navigation_page_id = item.navigation_page_id
            if item.action == "open_page":
                navigation_page_id = item.page_id
            token = self._telegram_ingestion_orchestrator.create_callback(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                action=item.action,
                callback_kind=TELEGRAM_CALLBACK_KIND_PICKER,
                change_request_id=change_request_id,
                target_notion_page_id=item.target_notion_page_id,
                target_notion_path=item.target_notion_path,
                picker_mode=mode,
                navigation_page_id=navigation_page_id,
                navigation_page_number=item.navigation_page_number,
            )
            buttons.append(
                [{"text": item.label, "callback_data": f"ll:{token}"}]
            )
        return view.text, {"inline_keyboard": buttons}

    def _build_review_markup(
        self,
        *,
        ingestion_result,
        chat_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        if self._telegram_ingestion_orchestrator is None or ingestion_result.change_request_id is None:
            return {}
        return self._build_review_markup_for_change_request(
            change_request_id=int(ingestion_result.change_request_id),
            session_id=ingestion_result.session_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    def _create_callback_token(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        action: str,
        callback_kind: str,
        change_request_id: Optional[int] = None,
    ) -> str:
        callback_store = self._telegram_session_store
        if callback_store is not None:
            return callback_store.create_callback(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                action=action,
                callback_kind=callback_kind,
                change_request_id=change_request_id,
            )
        if self._telegram_ingestion_orchestrator is not None:
            return self._telegram_ingestion_orchestrator.create_callback(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                action=action,
                callback_kind=callback_kind,
                change_request_id=change_request_id,
            )
        raise TelegramGatewayError(
            error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
            message="Telegram callback storage is not configured",
            http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            failure_reason="UNKNOWN_ERROR",
        )

    def _build_pending_markup(
        self,
        *,
        items: tuple[TelegramPendingItem, ...],
        chat_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        buttons: list[list[dict[str, str]]] = []
        for item in items:
            session_id = f"pending-{item.change_request_id}"
            view_token = self._create_callback_token(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                action="pending_view",
                callback_kind=TELEGRAM_CALLBACK_KIND_OPERATOR,
                change_request_id=item.change_request_id,
            )
            accept_token = self._create_callback_token(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                action="accept",
                callback_kind=TELEGRAM_CALLBACK_KIND_REVIEW,
                change_request_id=item.change_request_id,
            ) if item.target_path != "unassigned" else None
            reject_token = self._create_callback_token(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                action="reject",
                callback_kind=TELEGRAM_CALLBACK_KIND_REVIEW,
                change_request_id=item.change_request_id,
            )
            change_target_token = self._create_callback_token(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                action="change_target",
                callback_kind=TELEGRAM_CALLBACK_KIND_REVIEW,
                change_request_id=item.change_request_id,
            )
            buttons.extend(
                [
                    [
                        {
                            "text": f"View #{item.change_request_id}",
                            "callback_data": f"ll:{view_token}",
                        },
                        *(
                            [
                                {
                                    "text": "Accept",
                                    "callback_data": f"ll:{accept_token}",
                                }
                            ]
                            if accept_token is not None
                            else []
                        ),
                        {
                            "text": "Reject",
                            "callback_data": f"ll:{reject_token}",
                        },
                    ],
                    [
                        {
                            "text": "Change target",
                            "callback_data": f"ll:{change_target_token}",
                        }
                    ],
                ]
            )
        return {"inline_keyboard": buttons}

    def _build_review_markup_for_change_request(
        self,
        *,
        change_request_id: int,
        session_id: Optional[str],
        chat_id: str,
        user_id: str,
        allow_accept: bool = True,
    ) -> dict[str, Any]:
        accept_token = (
            self._create_callback_token(
                session_id=session_id or f"proposal-{change_request_id}",
                chat_id=chat_id,
                user_id=user_id,
                action="accept",
                callback_kind=TELEGRAM_CALLBACK_KIND_REVIEW,
                change_request_id=change_request_id,
            )
            if allow_accept
            else None
        )
        reject_token = self._create_callback_token(
            session_id=session_id or f"proposal-{change_request_id}",
            chat_id=chat_id,
            user_id=user_id,
            action="reject",
            callback_kind=TELEGRAM_CALLBACK_KIND_REVIEW,
            change_request_id=change_request_id,
        )
        change_target_token = self._create_callback_token(
            session_id=session_id or f"proposal-{change_request_id}",
            chat_id=chat_id,
            user_id=user_id,
            action="change_target",
            callback_kind=TELEGRAM_CALLBACK_KIND_REVIEW,
            change_request_id=change_request_id,
        )
        review_buttons: list[dict[str, str]] = []
        if accept_token is not None:
            review_buttons.append(
                {"text": "Accept", "callback_data": f"ll:{accept_token}"}
            )
        review_buttons.append(
            {"text": "Reject", "callback_data": f"ll:{reject_token}"}
        )
        return {
            "inline_keyboard": [
                review_buttons,
                [{"text": "Change target", "callback_data": f"ll:{change_target_token}"}],
            ]
        }

    def _build_review_target_picker(
        self,
        *,
        change_request_id: int,
        chat_id: str,
        user_id: str,
    ) -> tuple[str, dict[str, Any]]:
        if self._telegram_page_orchestrator is None or not self._telegram_page_orchestrator.list_pages().pages:
            raise TelegramGatewayError(
                error_code="NOTION_PAGES_EMPTY",
                message="No indexed Notion pages are available.",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="NOTION_PAGE_NOT_FOUND",
            )
        session_id = f"proposal-{change_request_id}"
        return self._build_hierarchy_picker(
            mode="change_target",
            session_id=session_id,
            change_request_id=change_request_id,
            chat_id=chat_id,
            user_id=user_id,
            current_page_id=None,
            page_number=1,
        )

    def _schedule_upload_settle(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        request_workflow_id: str,
        settle_version: Optional[int] = None,
    ) -> None:
        if self._queue_client is None:
            raise TelegramGatewayError(
                error_code="TELEGRAM_QUEUE_UNAVAILABLE",
                message="Media-group settling requires the Redis/RQ queue.",
                http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                failure_reason="TELEGRAM_QUEUE_UNAVAILABLE",
            )
        from src.worker.telegram import process_telegram_upload_settle_job

        self._queue_client.enqueue_in(
            queue_name="telegram",
            function=process_telegram_upload_settle_job,
            seconds=1,
            args=(
                session_id,
                chat_id,
                user_id,
                request_workflow_id,
                settle_version,
            ),
            description="Settle one Telegram media group upload session",
            retry_policy=QueueRetryPolicy(max_retries=2, retry_intervals=(1, 3)),
            timeout_seconds=self._telegram_job_timeout_seconds,
        )

    def _build_reply_for_command(self, command: str) -> str:
        if command == "health":
            return "LearnLoop Agent status: ok"
        if command == "help":
            return (
                "LearnLoop Agent commands:\n"
                "/start or /help — show this guide\n"
                "/pages — list indexed Notion pages with full hierarchy paths\n"
                "/sync — discover accessible Notion pages and select pages to re-index\n"
                "/index-full — review a warning, then rebuild the full derived index\n"
                "/index-status [workflow_id] — show persisted index workflow status\n"
                "/cost [today|7d|month|workflow <workflow_id>] — show recorded cost and budget status\n"
                "/pending — review pending proposals with View/Accept/Reject/Change target\n"
                "/workflow [workflow_id] — show recent or redacted workflow status\n"
                "/status — show liveness and dependency readiness\n"
                "/stats — show safe knowledge-base aggregate counts\n"
                "/ingest — upload a PDF or image, then choose a target page button\n"
                "/ingest --page <external_page_id> — text fallback for automation\n"
                "/retry-proposal — retry proposal validation using the existing source\n"
                "/ask <question> — ask about indexed notes; optional --page/--section scopes\n"
                "/accept <proposal_id> — explicitly accept one pending proposal\n"
                "/reject <proposal_id> <reason> — reject without a Notion write\n"
                "/health — check bot status\n\n"
                "You do not need to type a Notion UUID for ingestion. "
                "After upload, choose the parent or child page from the buttons. "
                "Sync is read-only for Notion and requires explicit confirmation. "
                "Full index also requires explicit confirmation; status only reads "
                "persisted workflow state. Cost and workflow commands are read-only "
                "and do not rerun or reconcile work. Pending is read-only until an "
                "explicit review action; only Accept can append and re-index. "
                "Status distinguishes liveness from dependency readiness; stats "
                "show only aggregate counts and safe timestamps. "
                "Accept is always an explicit human action; proposals without a "
                "target cannot be accepted."
            )
        return "Unsupported command. Use /help."

    def _parse_ingest_target(self, command_text: str) -> Optional[str]:
        try:
            tokens = shlex.split(command_text)
        except ValueError as exc:
            raise TelegramGatewayError(
                error_code="INVALID_ARGUMENT",
                message=f"Invalid /ingest command: {exc}",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            ) from exc

        target_notion_page_id: Optional[str] = None
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "--page":
                if index + 1 >= len(tokens):
                    raise TelegramGatewayError(
                        error_code="INVALID_ARGUMENT",
                        message="Usage: /ingest [--page <page_id>] with a PDF or screenshot",
                        http_status_code=HTTPStatus.BAD_REQUEST,
                        failure_reason="UNKNOWN_ERROR",
                    )
                target_notion_page_id = tokens[index + 1].strip()
                index += 2
                continue
            if token.startswith("--page="):
                target_notion_page_id = token.split("=", 1)[1].strip()
                if not target_notion_page_id:
                    raise TelegramGatewayError(
                        error_code="INVALID_ARGUMENT",
                        message="Usage: /ingest [--page <page_id>] with a PDF or screenshot",
                        http_status_code=HTTPStatus.BAD_REQUEST,
                        failure_reason="UNKNOWN_ERROR",
                    )
                index += 1
                continue
            raise TelegramGatewayError(
                error_code="INVALID_ARGUMENT",
                message="Usage: /ingest [--page <page_id>] with a PDF or screenshot",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            )
        return target_notion_page_id

    def _merge_stage_latency(
        self,
        latency: LatencyEvidence,
        incoming: dict[str, float],
    ) -> None:
        latency.update(
            {
                key: float(incoming.get(key, 0.0))
                for key in (
                    "download_ms",
                    "ocr_ms",
                    "llm_ms",
                    "persist_ms",
                    "preview_delivery_ms",
                )
            }
        )

    def _normalize_failure_reason(self, failure_reason: str) -> str:
        normalized = failure_reason.strip().upper()
        if normalized in STANDARD_FAILURE_REASONS:
            return normalized
        return "UNKNOWN_ERROR"

    def _http_status_for_tool_error(self, error_code: str) -> int:
        normalized = error_code.strip().upper()
        if normalized == "INVALID_ARGUMENT":
            return HTTPStatus.BAD_REQUEST
        if normalized == "TELEGRAM_NOT_CONFIGURED":
            return HTTPStatus.SERVICE_UNAVAILABLE
        if normalized in {
            "TELEGRAM_SEND_FAILED",
            "TELEGRAM_CALLBACK_ACK_FAILED",
            "TELEGRAM_PREVIEW_DELIVERY_FAILED",
        }:
            return HTTPStatus.BAD_GATEWAY
        return HTTPStatus.INTERNAL_SERVER_ERROR

    def _mark_failed_workflow(
        self,
        *,
        workflow_run_id: int,
        failure_reason: str,
        error_code: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        safe_metadata = metadata or {}
        self._workflow_run_service.mark_workflow_failed(
            workflow_run_id,
            failure_reason=self._normalize_failure_reason(failure_reason),
            metadata_json=json.dumps(
                {
                    "operation": "telegram_webhook",
                    "error_code": error_code,
                    **safe_metadata,
                },
                sort_keys=True,
            ),
        )
