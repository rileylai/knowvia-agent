from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from src.app.schemas.qa import QACitation, QARequest, QAResponse


class ConversationMessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    sequence_number: int
    created_at: datetime
    citations: List[QACitation]


class ConversationSessionSummaryResponse(BaseModel):
    id: int
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class ConversationSessionResponse(ConversationSessionSummaryResponse):
    messages: List[ConversationMessageResponse]


class ConversationMessageRequest(QARequest):
    query: str = Field(min_length=1, description="User question for this conversation.")


class ConversationTurnResponse(QAResponse):
    session_id: int
    title: str
    updated_at: datetime
    messages: List[ConversationMessageResponse]
