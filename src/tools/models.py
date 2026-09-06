from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)


class ToolContext(BaseModel):
    workflow_id: str
    actor: str = "system"
    owner_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    code: str
    message: str


class ToolResult(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    safe_text: Optional[str] = None
    structured_content: Optional[Dict[str, Any]] = None
    is_error: bool = False
    error: Optional[ToolError] = None
    error_code: Optional[str] = None

    @classmethod
    def success(
        cls,
        content: Optional[str] = None,
        structured_content: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        return cls(
            content=content,
            safe_text=content,
            structured_content=structured_content,
            is_error=False,
            error=None,
        )

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        content: Optional[str] = None,
        structured_content: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        return cls(
            content=content,
            safe_text=content or message,
            structured_content=structured_content,
            is_error=True,
            error=ToolError(code=code, message=message),
            error_code=code,
        )
