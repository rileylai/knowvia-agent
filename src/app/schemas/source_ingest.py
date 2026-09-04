from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SourceDocumentCreateRequest(BaseModel):
    source_type: str = Field(
        min_length=1,
        description="Source type: pdf, url, youtube, screenshot, or chat_text.",
    )
    source_display_name: str = Field(
        min_length=1,
        description="Display name shown in source metadata.",
    )
    raw_text: str = Field(
        min_length=1,
        description="Normalized source text used for proposal generation.",
    )


class SourceDocumentCreateResponse(BaseModel):
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    content_hash: str
    index_status: Optional[str] = None
    indexed_chunk_count: int = 0
    embedded_chunk_count: int = 0


class KnowledgeSourceResponse(BaseModel):
    id: int
    display_name: str
    source_kind: str
    status: str
    chunk_count: int
    updated_at: Optional[datetime] = None


class URLIngestionRequest(BaseModel):
    url: str = Field(
        min_length=1,
        description="Absolute article URL to ingest (http/https).",
    )


class YouTubeIngestionRequest(BaseModel):
    url: str = Field(
        min_length=1,
        description="Absolute YouTube video URL to ingest transcript from.",
    )


class ChatTextIngestionRequest(BaseModel):
    chat_text: str = Field(
        min_length=1,
        description="Pasted chat text to ingest.",
    )
    source_display_name: str = Field(
        default="Chat text",
        min_length=1,
        description="Display name shown in source metadata.",
    )
