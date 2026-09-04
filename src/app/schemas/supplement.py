from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from src.orchestrators import DEFAULT_SUPPLEMENT_MODEL, DEFAULT_SUPPLEMENT_PROVIDER_NAME


class SupplementProposeRequest(BaseModel):
    source_document_id: int = Field(
        ge=1,
        description="Source document id used to generate a supplement proposal.",
    )
    provider_name: str = Field(
        default=DEFAULT_SUPPLEMENT_PROVIDER_NAME,
        min_length=1,
        description="LLM provider name routed by ProviderRouter.",
    )
    model: str = Field(
        default=DEFAULT_SUPPLEMENT_MODEL,
        min_length=1,
        description="LLM model name for proposal generation.",
    )
    target_notion_page_id: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Optional external Notion page id for the pending change request.",
    )


class SupplementProposeResponse(BaseModel):
    workflow_run_id: int
    status: str
    change_request_id: int
    change_request_status: str
    source_document_id: int
    duplicate_detected: bool
    duplicate_notion_path: Optional[str] = None
    target_notion_page_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    token_input: Optional[int] = None
    token_output: Optional[int] = None


class SupplementAcceptRequest(BaseModel):
    change_request_id: int = Field(
        ge=1,
        description="Change request id to accept.",
    )
    reviewer: Optional[str] = Field(
        default=None,
        description="Optional reviewer id or name.",
    )


class SupplementRejectRequest(BaseModel):
    change_request_id: int = Field(
        ge=1,
        description="Change request id to reject.",
    )
    reviewer: Optional[str] = Field(
        default=None,
        description="Optional reviewer id or name.",
    )
    reason: str = Field(
        min_length=1,
        description="Reject reason recorded for audit and workflow metadata.",
    )


class SupplementEditLaterRequest(BaseModel):
    change_request_id: int = Field(
        ge=1,
        description="Change request id to keep pending for later editing/review.",
    )
    reviewer: Optional[str] = Field(
        default=None,
        description="Optional reviewer id or name.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Optional note describing why this request is deferred.",
    )


class SupplementReviewResponse(BaseModel):
    workflow_run_id: int
    status: str
    change_request_id: int
    change_request_status: str
    review_action: str
    reviewer: Optional[str] = None
    reason: Optional[str] = None


class SupplementCitation(BaseModel):
    source_type: Optional[str] = None
    source_display_name: Optional[str] = None
    notion_path: Optional[str] = None
    page_id: Optional[str] = None
    quote: Optional[str] = None


class SupplementProposalContent(BaseModel):
    title: str
    target_path: str
    source_type: str
    source_display_name: str
    summary: str
    concepts: List[str]
    notes: List[str]


class SupplementTargetPage(BaseModel):
    page_id: str
    title: str
    notion_path: str


class SupplementPendingItem(BaseModel):
    change_request_id: int
    status: str
    source_document_id: Optional[int] = None
    target_notion_page_id: Optional[str] = None
    target_page: Optional[SupplementTargetPage] = None
    proposal: SupplementProposalContent
    citations: List[SupplementCitation]
    created_at: Optional[datetime] = None


class SupplementPendingListResponse(BaseModel):
    status: str
    count: int
    items: List[SupplementPendingItem]
