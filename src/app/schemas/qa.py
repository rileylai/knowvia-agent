from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class QARequest(BaseModel):
    query: str = Field(min_length=1, description="User question for RAG QA.")
    top_k: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum number of retrieved production chunks.",
    )
    page_ids: Optional[List[str]] = Field(
        default=None,
        description="Optional scope filter for Notion page ids.",
    )
    section_paths: Optional[List[str]] = Field(
        default=None,
        description="Optional scope filter for Notion path prefixes.",
    )
    source_kinds: Optional[List[str]] = Field(
        default=None,
        description="Optional source kind filter. Supported production kinds are notion, pdf, and url.",
    )
    provider_name: str = Field(
        default="openai",
        min_length=1,
        description="LLM provider name routed by ProviderRouter.",
    )
    model: str = Field(
        default="gpt-4o-mini",
        min_length=1,
        description="LLM model name for the provider request.",
    )


class QACitation(BaseModel):
    notion_path: Optional[str] = None
    page_id: Optional[str] = None
    score: float
    source_kind: str = "notion"
    source_display_name: Optional[str] = None
    locator: Optional[str] = None
    source_url: Optional[str] = None


class QAResponse(BaseModel):
    workflow_run_id: int
    status: str
    answer: str
    insufficient_info: bool
    retrieved_chunk_count: int
    citations: List[QACitation]
    provider: Optional[str] = None
    model: Optional[str] = None
    token_input: Optional[int] = None
    token_output: Optional[int] = None
