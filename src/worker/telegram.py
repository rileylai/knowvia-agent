from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from src.app.telegram_runtime import build_telegram_gateway_orchestrator
from src.app.dependencies import (
    get_cost_tracker,
    get_embedding_client,
    get_prompt_template_loader,
    get_provider_router,
    get_tool_registry,
    get_trust_boundary,
    get_queue_client,
    get_telegram_session_store,
    get_telegram_sync_session_store,
    get_telegram_index_session_store,
)
from src.db.session import (
    get_db_session_factory,
    get_unit_of_work_factory,
)
from src.orchestrators import (
    TelegramCallbackAttachment,
    TelegramDocumentAttachment,
    TelegramGatewayError,
    TelegramPhotoAttachment,
    NotionPageIndexError,
)

TELEGRAM_WEBHOOK_JOB_PATH = f"{__name__}.process_telegram_webhook_job"
TELEGRAM_FULL_INDEX_JOB_PATH = f"{__name__}.process_telegram_full_index_job"


def process_telegram_webhook_job(
    update_id: Optional[int],
    chat_id: Optional[str],
    text: Optional[str],
    caption: Optional[str],
    document: Optional[Dict[str, Any]],
    photos: List[Dict[str, Any]],
    request_workflow_id: str,
    user_id: Optional[str] = None,
    message_id: Optional[int] = None,
    media_group_id: Optional[str] = None,
    callback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one claimed Telegram update inside an RQ worker process.

    Expected Telegram/domain failures are persisted by the gateway and returned
    as a completed job so RQ does not retry a user-visible failed update. RQ
    retries unexpected worker failures, such as a process crash before the
    ledger reaches a terminal state.
    """

    db_session_factory = get_db_session_factory()
    db_session = db_session_factory()
    try:
        gateway = build_telegram_gateway_orchestrator(
            db_session=db_session,
            db_session_factory=db_session_factory,
            unit_of_work_factory=get_unit_of_work_factory(),
            tool_registry=get_tool_registry(),
            provider_router=get_provider_router(),
            embedding_client=get_embedding_client(),
            cost_tracker=get_cost_tracker(),
            prompt_template_loader=get_prompt_template_loader(),
            trust_boundary=get_trust_boundary(),
            telegram_session_store=get_telegram_session_store(),
            telegram_sync_session_store=get_telegram_sync_session_store(),
            telegram_index_session_store=get_telegram_index_session_store(),
            queue_client=get_queue_client(),
        )
        result = asyncio.run(
            gateway.handle_claimed_webhook(
                update_id=update_id,
                chat_id=chat_id,
                text=text,
                caption=caption,
                document=(
                    TelegramDocumentAttachment(**document)
                    if document is not None
                    else None
                ),
                photos=[TelegramPhotoAttachment(**photo) for photo in photos],
                request_workflow_id=request_workflow_id,
                user_id=user_id,
                message_id=message_id,
                media_group_id=media_group_id,
                callback=(
                    TelegramCallbackAttachment(**callback)
                    if callback is not None
                    else None
                ),
            )
        )
        return asdict(result)
    except TelegramGatewayError as exc:
        return {
            "status": "failed",
            "error_code": exc.error_code,
            "failure_reason": exc.failure_reason,
            "workflow_run_id": exc.workflow_run_id,
        }
    finally:
        db_session.close()


def process_telegram_upload_settle_job(
    session_id: str,
    chat_id: str,
    user_id: str,
    request_workflow_id: str,
    settle_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Settle a media group once; Redis state prevents duplicate pickers."""

    db_session_factory = get_db_session_factory()
    db_session = db_session_factory()
    try:
        gateway = build_telegram_gateway_orchestrator(
            db_session=db_session,
            db_session_factory=db_session_factory,
            unit_of_work_factory=get_unit_of_work_factory(),
            tool_registry=get_tool_registry(),
            provider_router=get_provider_router(),
            embedding_client=get_embedding_client(),
            cost_tracker=get_cost_tracker(),
            prompt_template_loader=get_prompt_template_loader(),
            trust_boundary=get_trust_boundary(),
            telegram_session_store=get_telegram_session_store(),
            telegram_sync_session_store=get_telegram_sync_session_store(),
            telegram_index_session_store=get_telegram_index_session_store(),
            queue_client=get_queue_client(),
        )
        settle_status = asyncio.run(
            gateway.settle_upload_session(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                request_workflow_id=request_workflow_id,
                settle_version=settle_version,
            )
        )
        if settle_status in {"stale", "duplicate"}:
            return {
                "status": "skipped",
                "skipped_reason": (
                    "STALE_SETTLE_JOB"
                    if settle_status == "stale"
                    else "DUPLICATE_SETTLE_JOB"
                ),
                "session_id": session_id,
            }
        return {"status": "succeeded", "session_id": session_id}
    except TelegramGatewayError as exc:
        return {
            "status": "failed",
            "error_code": exc.error_code,
            "failure_reason": exc.failure_reason,
        }
    finally:
        db_session.close()


def process_telegram_full_index_job(
    workflow_run_id: int,
    request_workflow_id: str,
) -> Dict[str, Any]:
    """Run one already-created full-index workflow without RQ retries."""

    db_session_factory = get_db_session_factory()
    db_session = db_session_factory()
    try:
        gateway = build_telegram_gateway_orchestrator(
            db_session=db_session,
            db_session_factory=db_session_factory,
            unit_of_work_factory=get_unit_of_work_factory(),
            tool_registry=get_tool_registry(),
            provider_router=get_provider_router(),
            embedding_client=get_embedding_client(),
            cost_tracker=get_cost_tracker(),
            prompt_template_loader=get_prompt_template_loader(),
            trust_boundary=get_trust_boundary(),
            telegram_session_store=get_telegram_session_store(),
            telegram_sync_session_store=get_telegram_sync_session_store(),
            telegram_index_session_store=get_telegram_index_session_store(),
            queue_client=get_queue_client(),
        )
        result = asyncio.run(
            gateway.run_full_index_job(
                workflow_run_id=workflow_run_id,
                request_workflow_id=request_workflow_id,
            )
        )
        return {
            "status": result.status,
            "workflow_run_id": result.workflow_run_id,
            "discovered_page_count": result.discovered_page_count,
            "processed_page_count": result.processed_page_count,
        }
    except NotionPageIndexError as exc:
        return {
            "status": "failed",
            "workflow_run_id": exc.workflow_run_id or workflow_run_id,
            "failure_reason": exc.failure_reason,
        }
    finally:
        db_session.close()
