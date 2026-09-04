from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.db.models import ChangeRequest, SourceDocument, WorkflowRun
from src.repositories import (
    ChangeRequestRepository,
    NotionPageRepository,
    SourceDocumentRepository,
    TelegramUpdateLedgerRepository,
    WorkflowRunRepository,
)
from src.services.telegram_update_idempotency import (
    TELEGRAM_UPDATE_SUCCEEDED,
)
from src.services.telegram_proposal_preview import (
    format_bounded_note_lines,
    truncate_telegram_preview,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry


RECOVERABLE_TELEGRAM_FAILURES = {
    "TELEGRAM_SEND_FAILED",
    "TELEGRAM_CALLBACK_ACK_FAILED",
    "TELEGRAM_PREVIEW_DELIVERY_FAILED",
}


@dataclass(frozen=True)
class TelegramRecoveryInspection:
    update_id: int
    workflow_run_id: int
    source_document_id: int
    change_request_id: int
    eligible: bool
    workflow_status: Optional[str]
    workflow_failure_reason: Optional[str]
    ledger_status: Optional[str]
    source_document_exists: bool
    change_request_exists: bool
    change_request_pending: bool
    source_change_request_match: bool
    target_page_exists: bool
    chat_id_available: bool
    reason: Optional[str]
    reconciled: bool = False


class TelegramRecoveryError(RuntimeError):
    def __init__(self, *, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class TelegramRecoveryService:
    """Inspect and reconcile one committed Telegram business outcome.

    This service never invokes OCR, an LLM, ingestion, proposal creation, or
    Notion. Its only external side effect is an explicitly requested preview
    ``send_message``.
    """

    def __init__(self, session_factory, *, tool_registry: Optional["ToolRegistry"] = None) -> None:
        self._session_factory = session_factory
        self._tool_registry = tool_registry

    def inspect(
        self,
        *,
        update_id: int,
        workflow_run_id: int,
        source_document_id: int,
        change_request_id: int,
    ) -> TelegramRecoveryInspection:
        session = self._session_factory()
        try:
            workflow = WorkflowRunRepository(session).get_workflow_run_by_id(
                workflow_run_id
            )
            ledger = TelegramUpdateLedgerRepository(session).get_by_update_id(update_id)
            source_document = SourceDocumentRepository(session).get_source_document_by_id(
                source_document_id
            )
            change_request = ChangeRequestRepository(session).get_change_request_by_id(
                change_request_id
            )
            target_page_exists = False
            if change_request is not None and change_request.target_notion_page_id is not None:
                target_page_exists = (
                    NotionPageRepository(session).get_by_id(
                        int(change_request.target_notion_page_id)
                    )
                    is not None
                )
            metadata = _safe_metadata(workflow.metadata_json if workflow else None)
            ledger_failure_reason = _ledger_failure_reason(ledger.failure_json if ledger else None)
            workflow_failure_reason = workflow.failure_reason if workflow else None
            reconciled = bool(
                workflow is not None
                and workflow.status == "succeeded"
                and metadata.get("reconciled_by") == "telegram_recovery"
            )
            eligible = (
                workflow is not None
                and workflow.workflow_type == "telegram"
                and workflow.status == "failed"
                and (workflow_failure_reason in RECOVERABLE_TELEGRAM_FAILURES
                     or ledger_failure_reason in RECOVERABLE_TELEGRAM_FAILURES)
                and ledger is not None
                and ledger.status == "failed"
                and ledger.workflow_run_id == workflow_run_id
                and source_document is not None
                and change_request is not None
                and change_request.status == "pending"
                and change_request.source_document_id == source_document_id
                and target_page_exists
            )
            reason = None if eligible or reconciled else _inspection_failure_reason(
                workflow=workflow,
                ledger=ledger,
                source_document=source_document,
                change_request=change_request,
                target_page_exists=target_page_exists,
                workflow_failure_reason=workflow_failure_reason,
                ledger_failure_reason=ledger_failure_reason,
            )
            return TelegramRecoveryInspection(
                update_id=update_id,
                workflow_run_id=workflow_run_id,
                source_document_id=source_document_id,
                change_request_id=change_request_id,
                eligible=eligible,
                workflow_status=workflow.status if workflow else None,
                workflow_failure_reason=workflow_failure_reason,
                ledger_status=ledger.status if ledger else None,
                source_document_exists=source_document is not None,
                change_request_exists=change_request is not None,
                change_request_pending=(
                    change_request is not None and change_request.status == "pending"
                ),
                source_change_request_match=(
                    change_request is not None
                    and change_request.source_document_id == source_document_id
                ),
                target_page_exists=target_page_exists,
                chat_id_available=bool(metadata.get("chat_id")),
                reason=reason,
                reconciled=reconciled,
            )
        finally:
            session.close()

    def build_preview(
        self,
        *,
        change_request_id: int,
        source_document_id: int,
    ) -> tuple[str, str]:
        """Read an existing pending proposal and format a fresh preview."""

        session = self._session_factory()
        try:
            change_request = ChangeRequestRepository(session).get_change_request_by_id(
                change_request_id
            )
            source_document = SourceDocumentRepository(session).get_source_document_by_id(
                source_document_id
            )
            if change_request is None or source_document is None:
                raise TelegramRecoveryError(
                    error_code="TELEGRAM_RECOVERY_NOT_ELIGIBLE",
                    message="The verified business rows are no longer available",
                )
            from src.orchestrators.supplement_query_orchestrator import (
                SupplementQueryOrchestrator,
            )

            item = SupplementQueryOrchestrator(
                change_request_repository=ChangeRequestRepository(session),
                notion_page_repository=NotionPageRepository(session),
            ).get_detail(change_request_id=change_request_id)
            if item.status != "pending":
                raise TelegramRecoveryError(
                    error_code="TELEGRAM_RECOVERY_NOT_ELIGIBLE",
                    message="Only a pending proposal can be previewed",
                )
            source_count = _source_count(source_document.source_display_name)
            preview = _format_preview(
                item=item,
                source_type=source_document.source_type,
                source_document_id=source_document_id,
                source_count=source_count,
            )
            return preview, str(item.target_notion_page_id or "")
        except TelegramRecoveryError:
            raise
        except Exception as exc:
            raise TelegramRecoveryError(
                error_code="TELEGRAM_RECOVERY_NOT_ELIGIBLE",
                message="The existing proposal could not be read safely",
            ) from exc
        finally:
            session.close()

    def get_chat_id(self, *, workflow_run_id: int) -> Optional[str]:
        session = self._session_factory()
        try:
            workflow = WorkflowRunRepository(session).get_workflow_run_by_id(
                workflow_run_id
            )
            metadata = _safe_metadata(workflow.metadata_json if workflow else None)
            chat_id = metadata.get("chat_id")
            return str(chat_id).strip() if chat_id is not None else None
        finally:
            session.close()

    def send_preview(
        self,
        *,
        chat_id: str,
        workflow_run_id: int,
        preview_text: str,
    ) -> int:
        if self._tool_registry is None:
            raise TelegramRecoveryError(
                error_code="TELEGRAM_RECOVERY_NOT_CONFIGURED",
                message="Telegram recovery delivery is not configured",
            )
        try:
            from src.tools.models import ToolContext

            result = asyncio.run(
                self._tool_registry.call_tool(
                    "telegram_bot",
                    context=ToolContext(
                        workflow_id=f"telegram-recovery-{workflow_run_id}",
                        metadata={"operation": "telegram_preview_recovery"},
                    ),
                    arguments={
                        "chat_id": chat_id,
                        "text": preview_text,
                    },
                )
            )
        except Exception as exc:
            raise TelegramRecoveryError(
                error_code="TELEGRAM_PREVIEW_DELIVERY_FAILED",
                message="Telegram proposal preview could not be delivered",
            ) from exc
        if result.is_error:
            raise TelegramRecoveryError(
                error_code="TELEGRAM_PREVIEW_DELIVERY_FAILED",
                message="Telegram proposal preview could not be delivered",
            )
        raw_message_id = (result.structured_content or {}).get("message_id")
        try:
            return int(raw_message_id)
        except (TypeError, ValueError) as exc:
            raise TelegramRecoveryError(
                error_code="TELEGRAM_PREVIEW_DELIVERY_FAILED",
                message="Telegram preview delivery returned an invalid result",
            ) from exc

    def reconcile_success(
        self,
        *,
        update_id: int,
        workflow_run_id: int,
        source_document_id: int,
        change_request_id: int,
        preview_delivery_status: str,
        recovery_action: str,
        telegram_message_id: Optional[int] = None,
    ) -> TelegramRecoveryInspection:
        inspection = self.inspect(
            update_id=update_id,
            workflow_run_id=workflow_run_id,
            source_document_id=source_document_id,
            change_request_id=change_request_id,
        )
        if not inspection.eligible:
            raise TelegramRecoveryError(
                error_code="TELEGRAM_RECOVERY_NOT_ELIGIBLE",
                message="The verified Telegram outcome is not eligible for reconciliation",
            )

        session = self._session_factory()
        try:
            workflow_repository = WorkflowRunRepository(session)
            ledger_repository = TelegramUpdateLedgerRepository(session)
            workflow = workflow_repository.get_workflow_run_by_id(workflow_run_id)
            ledger = ledger_repository.get_by_update_id(update_id)
            if workflow is None or ledger is None or ledger.status != "failed":
                raise TelegramRecoveryError(
                    error_code="TELEGRAM_RECOVERY_CONFLICT",
                    message="Telegram workflow or ledger changed during reconciliation",
                )
            metadata = _safe_metadata(workflow.metadata_json)
            metadata.update(
                {
                    "business_status": "succeeded",
                    "callback_ack_status": metadata.get(
                        "callback_ack_status", "unknown"
                    ),
                    "preview_delivery_status": preview_delivery_status,
                    "recovery_action": recovery_action,
                    "reconciled_by": "telegram_recovery",
                }
            )
            workflow_repository.update_workflow_run(
                workflow_run_id,
                status="succeeded",
                failure_reason=None,
                metadata_json=json.dumps(metadata, sort_keys=True),
                finished_at=datetime.now(timezone.utc),
                commit=False,
            )
            ledger_repository.mark_succeeded(
                update_id,
                workflow_run_id=workflow_run_id,
                result_json=json.dumps(
                    {
                        "workflow_run_id": workflow_run_id,
                        "status": TELEGRAM_UPDATE_SUCCEEDED,
                        "handled": True,
                        "command": "callback",
                        "reply_text": "Proposal preview delivery recovered.",
                        "telegram_message_id": telegram_message_id,
                        "skipped_reason": None,
                        "source_document_id": source_document_id,
                        "change_request_id": change_request_id,
                        "source_type": None,
                        "target_notion_page_id": None,
                        "qa_workflow_run_id": None,
                        "insufficient_info": None,
                        "citations": [],
                        "review_workflow_run_id": None,
                        "review_action": None,
                        "change_request_status": "pending",
                        "target_set": True,
                        "business_status": "succeeded",
                        "callback_ack_status": metadata["callback_ack_status"],
                        "preview_delivery_status": preview_delivery_status,
                    },
                    sort_keys=True,
                ),
            )
            session.commit()
        except TelegramRecoveryError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise TelegramRecoveryError(
                error_code="TELEGRAM_RECOVERY_CONFLICT",
                message="Telegram workflow and ledger could not be reconciled",
            ) from exc
        finally:
            session.close()
        return self.inspect(
            update_id=update_id,
            workflow_run_id=workflow_run_id,
            source_document_id=source_document_id,
            change_request_id=change_request_id,
        )


def _safe_metadata(metadata_json: Optional[str]) -> Dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        value = json.loads(metadata_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "operation",
        "update_id",
        "chat_id",
        "request_workflow_id",
        "has_document",
        "photo_count",
        "handled",
        "command",
        "telegram_message_id",
        "source_document_id",
        "change_request_id",
        "source_type",
        "target_set",
        "qa_workflow_run_id",
        "insufficient_info",
        "citation_count",
        "review_workflow_run_id",
        "review_action",
        "change_request_status",
        "business_status",
        "callback_ack_status",
        "callback_ack_failure_reason",
        "preview_delivery_status",
        "preview_delivery_failure_reason",
        "recovery_action",
        "reconciled_by",
    }
    safe: Dict[str, Any] = {}
    for key, item in value.items():
        if key not in allowed_keys:
            continue
        if item is None or isinstance(item, (bool, int, float, str)):
            safe[key] = item
    return safe


def _ledger_failure_reason(failure_json: Optional[str]) -> Optional[str]:
    if not failure_json:
        return None
    try:
        value = json.loads(failure_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    reason = value.get("failure_reason")
    return str(reason).strip().upper() if reason else None


def _inspection_failure_reason(
    *,
    workflow: Optional[WorkflowRun],
    ledger,
    source_document: Optional[SourceDocument],
    change_request: Optional[ChangeRequest],
    target_page_exists: bool,
    workflow_failure_reason: Optional[str],
    ledger_failure_reason: Optional[str],
) -> str:
    if workflow is None:
        return "WORKFLOW_NOT_FOUND"
    if ledger is None:
        return "TELEGRAM_UPDATE_NOT_FOUND"
    if workflow.workflow_type != "telegram":
        return "WORKFLOW_TYPE_MISMATCH"
    if workflow.status != "failed" or ledger.status != "failed":
        return "OUTCOME_NOT_TERMINAL_FAILED"
    if not ({workflow_failure_reason, ledger_failure_reason} & RECOVERABLE_TELEGRAM_FAILURES):
        return "FAILURE_REASON_NOT_RECOVERABLE"
    if source_document is None:
        return "SOURCE_DOCUMENT_NOT_FOUND"
    if change_request is None:
        return "CHANGE_REQUEST_NOT_FOUND"
    if change_request.status != "pending":
        return "CHANGE_REQUEST_NOT_PENDING"
    if change_request.source_document_id != source_document.id:
        return "SOURCE_CHANGE_REQUEST_MISMATCH"
    if not target_page_exists:
        return "TARGET_PAGE_NOT_FOUND"
    return "TELEGRAM_RECOVERY_CONFLICT"


def _source_count(display_name: str) -> int:
    match = re.search(r"Screenshot batch \((\d+) images\)", display_name)
    return int(match.group(1)) if match else 1


def _format_preview(*, item, source_type: str, source_document_id: int, source_count: int) -> str:
    target = item.target_page.notion_path if item.target_page else "not selected"
    if source_type == "screenshot":
        summary = (
            "Ingestion succeeded "
            f"(screenshots={source_count}, source_document_id={source_document_id}, "
            f"change_request_id={item.change_request_id}, status=pending)."
        )
    else:
        summary = (
            "Ingestion succeeded "
            f"(source_type={source_type}, source_document_id={source_document_id}, "
            f"change_request_id={item.change_request_id}, status=pending)."
        )
    lines = [
        summary,
        f"Proposal ready for review (change_request_id={item.change_request_id})",
        f"Title: {item.proposal.title}",
        f"Target Notion page: {target}",
        f"Summary: {item.proposal.summary}",
        "Key Concepts: " + ", ".join(item.proposal.concepts),
        "Notes:",
    ]
    lines.extend(format_bounded_note_lines(item.proposal.notes))
    lines.append("Citations:")
    for citation in item.citations:
        lines.append(
            "- "
            + (
                citation.notion_path
                or citation.source_display_name
                or citation.page_id
                or citation.quote
                or "unavailable"
            )
        )
    lines.append(
        f"Review with /accept {item.change_request_id} or /reject "
        f"{item.change_request_id} <reason>."
    )
    return truncate_telegram_preview("\n".join(lines))
