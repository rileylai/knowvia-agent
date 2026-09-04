from __future__ import annotations

from typing import List
from typing import Optional

from pydantic import BaseModel, Field


class TelegramChatPayload(BaseModel):
    id: int


class TelegramUserPayload(BaseModel):
    id: int


class TelegramDocumentPayload(BaseModel):
    file_id: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


class TelegramPhotoPayload(BaseModel):
    file_id: str
    file_unique_id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    file_size: Optional[int] = None


class TelegramMessagePayload(BaseModel):
    message_id: int
    chat: TelegramChatPayload
    from_user: Optional[TelegramUserPayload] = Field(default=None, alias="from")
    text: Optional[str] = None
    caption: Optional[str] = None
    media_group_id: Optional[str] = None
    document: Optional[TelegramDocumentPayload] = None
    photo: List[TelegramPhotoPayload] = Field(default_factory=list)


class TelegramCallbackQueryPayload(BaseModel):
    id: str
    from_user: TelegramUserPayload = Field(alias="from")
    message: Optional[TelegramMessagePayload] = None
    data: Optional[str] = None


class TelegramWebhookRequest(BaseModel):
    update_id: Optional[int] = None
    message: Optional[TelegramMessagePayload] = None
    callback_query: Optional[TelegramCallbackQueryPayload] = None


class TelegramWebhookResponse(BaseModel):
    workflow_run_id: Optional[int] = None
    status: str
    handled: bool
    command: Optional[str] = None
    reply_text: Optional[str] = None
    telegram_message_id: Optional[int] = None
    skipped_reason: Optional[str] = None
    source_document_id: Optional[int] = None
    change_request_id: Optional[int] = None
    source_type: Optional[str] = None
    target_notion_page_id: Optional[str] = None
    qa_workflow_run_id: Optional[int] = None
    insufficient_info: Optional[bool] = None
    citations: List[str] = Field(default_factory=list)
    review_workflow_run_id: Optional[int] = None
    review_action: Optional[str] = None
    change_request_status: Optional[str] = None
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
    status_checks: dict[str, str] = Field(default_factory=dict)
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
