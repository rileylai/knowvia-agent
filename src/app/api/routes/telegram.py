from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from src.app.dependencies import (
    get_cost_tracker,
    get_embedding_client,
    get_prompt_template_loader,
    get_provider_router,
    get_queue_client,
    get_readiness_service,
    get_trust_boundary,
    get_tool_registry,
    get_telegram_session_store,
    get_telegram_sync_session_store,
    get_telegram_index_session_store,
    get_workflow_observability_service,
)
from src.app.telegram_runtime import build_telegram_gateway_orchestrator
from src.app.schemas import TelegramWebhookRequest, TelegramWebhookResponse
from src.db.session import (
    SessionFactory,
    UnitOfWorkFactory,
    get_db_session,
    get_db_session_factory,
    get_unit_of_work_factory,
)
from src.orchestrators import (
    TelegramCallbackAttachment,
    TelegramDocumentAttachment,
    TelegramGatewayError,
    TelegramPhotoAttachment,
)
from src.providers import EmbeddingClient, ProviderRouter
from src.services import (
    CostTracker,
    PromptTemplateLoader,
    TrustBoundaryError,
    TrustBoundaryService,
    TelegramSessionStore,
    TelegramSyncSessionStore,
    TelegramIndexSessionStore,
    WorkflowObservabilityService,
    ReadinessService,
)
from src.queue import QueueClient
from src.tools import ToolRegistry

router = APIRouter()


@router.post("/api/telegram/webhook", response_model=TelegramWebhookResponse)
async def handle_telegram_webhook(
    payload: TelegramWebhookRequest,
    request: Request,
    db_session: Session = Depends(get_db_session),
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_unit_of_work_factory),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    provider_router: ProviderRouter = Depends(get_provider_router),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
    cost_tracker: CostTracker = Depends(get_cost_tracker),
    prompt_template_loader: PromptTemplateLoader = Depends(get_prompt_template_loader),
    queue_client: Optional[QueueClient] = Depends(get_queue_client),
    telegram_webhook_secret: Optional[str] = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
    trust_boundary: TrustBoundaryService = Depends(get_trust_boundary),
    telegram_session_store: TelegramSessionStore = Depends(get_telegram_session_store),
    telegram_sync_session_store: TelegramSyncSessionStore = Depends(
        get_telegram_sync_session_store
    ),
    telegram_index_session_store: TelegramIndexSessionStore = Depends(
        get_telegram_index_session_store
    ),
    workflow_observability_service: WorkflowObservabilityService = Depends(
        get_workflow_observability_service
    ),
    readiness_service: ReadinessService = Depends(get_readiness_service),
) -> TelegramWebhookResponse:
    try:
        trust_boundary.require_telegram_webhook_secret(telegram_webhook_secret)
    except TrustBoundaryError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": None,
            },
        ) from exc

    orchestrator = build_telegram_gateway_orchestrator(
        db_session=db_session,
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        tool_registry=tool_registry,
        provider_router=provider_router,
        embedding_client=embedding_client,
        cost_tracker=cost_tracker,
        prompt_template_loader=prompt_template_loader,
        trust_boundary=trust_boundary,
        telegram_session_store=telegram_session_store,
        telegram_sync_session_store=telegram_sync_session_store,
        telegram_index_session_store=telegram_index_session_store,
        workflow_observability_service=workflow_observability_service,
        readiness_service=readiness_service,
        queue_client=queue_client,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))
    message = payload.message
    callback_query = payload.callback_query
    callback_message = callback_query.message if callback_query is not None else None
    active_message = message or callback_message
    chat_id = str(active_message.chat.id) if active_message is not None else None
    user = (
        message.from_user
        if message is not None
        else callback_query.from_user if callback_query is not None else None
    )
    user_id = str(user.id) if user is not None else chat_id
    text = message.text if message is not None else None
    caption = message.caption if message is not None else None

    document = None
    photos: list[TelegramPhotoAttachment] = []
    if active_message is not None and active_message.document is not None:
        document = TelegramDocumentAttachment(
            file_id=active_message.document.file_id,
            file_name=active_message.document.file_name,
            mime_type=active_message.document.mime_type,
            file_size=active_message.document.file_size,
        )
    if active_message is not None and active_message.photo:
        largest_photo = max(
            active_message.photo,
            key=lambda photo: (
                photo.width or 0,
                photo.height or 0,
                photo.file_size or 0,
            ),
        )
        photos = [
            TelegramPhotoAttachment(
                file_id=largest_photo.file_id,
                file_unique_id=largest_photo.file_unique_id,
                file_size=largest_photo.file_size,
            )
        ]

    try:
        result = await orchestrator.enqueue_webhook(
            update_id=payload.update_id,
            chat_id=chat_id,
            text=text,
            caption=caption,
            document=document,
            photos=photos,
            user_id=user_id,
            message_id=active_message.message_id if active_message is not None else None,
            media_group_id=(
                active_message.media_group_id if active_message is not None else None
            ),
            callback=(
                TelegramCallbackAttachment(
                    callback_query_id=callback_query.id,
                    callback_data=callback_query.data or "",
                )
                if callback_query is not None
                else None
            ),
            request_workflow_id=request_workflow_id,
        )
    except TelegramGatewayError as exc:
        detail = {
            "error_code": exc.error_code,
            "message": exc.message,
            "failure_reason": exc.failure_reason,
            "workflow_run_id": exc.workflow_run_id,
        }
        for key in (
            "business_status",
            "callback_ack_status",
            "preview_delivery_status",
            "index_status",
            "index_discovered_page_count",
            "index_processed_page_count",
            "index_failed_page_count",
            "index_remaining_page_count",
            "cost_scope",
            "cost_workflow_run_id",
            "cost_total_usd",
            "cost_llm_usd",
            "cost_embedding_usd",
            "cost_unknown_workflow_count",
            "cost_budget_status",
            "cost_budget_usd",
            "cost_workflow_budget_exceeded_count",
            "cost_workflow_budget_usd",
            "workflow_detail_run_id",
            "workflow_detail_type",
            "workflow_detail_status",
            "workflow_detail_failure_reason",
            "workflow_detail_age_seconds",
            "workflow_detail_stale",
            "workflow_detail_estimated_cost_usd",
            "workflow_recent_count",
            "pending_count",
            "status_liveness",
            "status_readiness",
            "status_checks",
            "stats_page_count",
            "stats_block_count",
            "stats_chunk_count",
            "stats_vector_count",
            "stats_proposal_count",
            "stats_pending_proposal_count",
            "stats_accepted_proposal_count",
            "stats_rejected_proposal_count",
            "stats_latest_full_index_at",
            "stats_latest_incremental_sync_at",
        ):
            if key in exc.metadata:
                detail[key] = exc.metadata[key]
        raise HTTPException(
            status_code=exc.http_status_code,
            detail=detail,
        ) from exc

    response = TelegramWebhookResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        handled=result.handled,
        command=result.command,
        reply_text=result.reply_text,
        telegram_message_id=result.telegram_message_id,
        skipped_reason=result.skipped_reason,
        source_document_id=result.source_document_id,
        change_request_id=result.change_request_id,
        source_type=result.source_type,
        target_notion_page_id=result.target_notion_page_id,
        qa_workflow_run_id=result.qa_workflow_run_id,
        insufficient_info=result.insufficient_info,
        citations=result.citations,
        review_workflow_run_id=result.review_workflow_run_id,
        review_action=result.review_action,
        change_request_status=result.change_request_status,
        target_set=result.target_set,
        business_status=result.business_status,
        callback_ack_status=result.callback_ack_status,
        preview_delivery_status=result.preview_delivery_status,
        sync_workflow_run_id=result.sync_workflow_run_id,
        sync_status=result.sync_status,
        sync_discovered_page_count=result.sync_discovered_page_count,
        sync_selected_page_count=result.sync_selected_page_count,
        sync_succeeded_page_count=result.sync_succeeded_page_count,
        sync_failed_page_count=result.sync_failed_page_count,
        index_workflow_run_id=result.index_workflow_run_id,
        index_status=result.index_status,
        index_discovered_page_count=result.index_discovered_page_count,
        index_processed_page_count=result.index_processed_page_count,
        index_failed_page_count=result.index_failed_page_count,
        index_remaining_page_count=result.index_remaining_page_count,
        index_failure_reason=result.index_failure_reason,
        index_estimated_cost_usd=result.index_estimated_cost_usd,
        index_stale=result.index_stale,
        cost_scope=result.cost_scope,
        cost_workflow_run_id=result.cost_workflow_run_id,
        cost_total_usd=result.cost_total_usd,
        cost_llm_usd=result.cost_llm_usd,
        cost_embedding_usd=result.cost_embedding_usd,
        cost_unknown_workflow_count=result.cost_unknown_workflow_count,
        cost_budget_status=result.cost_budget_status,
        cost_budget_usd=result.cost_budget_usd,
        cost_workflow_budget_exceeded_count=result.cost_workflow_budget_exceeded_count,
        cost_workflow_budget_usd=result.cost_workflow_budget_usd,
        workflow_detail_run_id=result.workflow_detail_run_id,
        workflow_detail_type=result.workflow_detail_type,
        workflow_detail_status=result.workflow_detail_status,
        workflow_detail_failure_reason=result.workflow_detail_failure_reason,
        workflow_detail_age_seconds=result.workflow_detail_age_seconds,
        workflow_detail_stale=result.workflow_detail_stale,
        workflow_detail_estimated_cost_usd=result.workflow_detail_estimated_cost_usd,
        workflow_recent_count=result.workflow_recent_count,
        pending_count=result.pending_count,
        status_liveness=result.status_liveness,
        status_readiness=result.status_readiness,
        status_checks=result.status_checks or {},
        stats_page_count=result.stats_page_count,
        stats_block_count=result.stats_block_count,
        stats_chunk_count=result.stats_chunk_count,
        stats_vector_count=result.stats_vector_count,
        stats_proposal_count=result.stats_proposal_count,
        stats_pending_proposal_count=result.stats_pending_proposal_count,
        stats_accepted_proposal_count=result.stats_accepted_proposal_count,
        stats_rejected_proposal_count=result.stats_rejected_proposal_count,
        stats_latest_full_index_at=result.stats_latest_full_index_at,
        stats_latest_incremental_sync_at=result.stats_latest_incremental_sync_at,
    )
    if result.status == "running":
        content = (
            response.model_dump()
            if hasattr(response, "model_dump")
            else response.dict()
        )
        return JSONResponse(status_code=202, content=content)
    return response
