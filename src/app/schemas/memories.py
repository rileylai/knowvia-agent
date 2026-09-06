from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


MemoryType = Literal["decision", "preference", "project_context"]


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    memory_type: Optional[MemoryType] = None
    source_session_id: Optional[int] = None
    source_message_id: Optional[int] = None


class MemoryResponse(BaseModel):
    id: int
    memory_type: MemoryType
    content: str
    status: str
    created_at: datetime


class MemorySaveResponse(BaseModel):
    status: Literal["saved", "already_saved"]
    memory: MemoryResponse
