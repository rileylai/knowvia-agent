from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class NotionPageIndexRequest(BaseModel):
    page_id: str = Field(min_length=1, description="Notion page id to index.")


class NotionPageIndexResponse(BaseModel):
    workflow_run_id: int
    status: str
    page_id: str
    page_title: str
    notion_path: str
    indexed_block_count: int


class NotionIncrementalIndexRequest(BaseModel):
    page_ids: List[str] = Field(
        min_length=1,
        description="Changed Notion page ids to reconcile using manual incremental sync.",
    )


class NotionIncrementalIndexedPage(BaseModel):
    page_id: str
    page_title: str
    notion_path: str
    indexed_block_count: int


class NotionIncrementalIndexResponse(BaseModel):
    workflow_run_id: int
    status: str
    sync_mode: str
    processed_page_count: int
    indexed_pages: List[NotionIncrementalIndexedPage]


class NotionFullIndexResponse(BaseModel):
    workflow_run_id: int
    status: str
    discovered_page_count: int
    processed_page_count: int
    indexed_pages: List[NotionIncrementalIndexedPage]


class NotionIndexStatusResponse(BaseModel):
    workflow_run_id: int
    workflow_type: str
    status: str
    failure_reason: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    metadata: Dict[str, Any]
