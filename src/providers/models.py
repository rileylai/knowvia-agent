from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class LLMRequest(BaseModel):
    messages: List[LLMMessage]
    model: str
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class LLMResponse(BaseModel):
    provider: str
    model: str
    output_text: str
    finish_reason: Optional[str] = None
    token_input: Optional[int] = None
    token_output: Optional[int] = None
    raw_response: Optional[Dict[str, Any]] = None
