from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AgentCitation:
    notion_path: str
    page_id: Optional[str]
    score: float
    source_kind: str = "notion"
    source_display_name: Optional[str] = None
    locator: Optional[str] = None
    source_url: Optional[str] = None
    image_index: Optional[int] = None
    sequence_index: Optional[int] = None
    original_filename: Optional[str] = None


class AgentTerminationReason(str, Enum):
    COMPLETED = "completed"
    INSUFFICIENT_INFO = "insufficient_info"
    MAX_TOOL_CALLS = "max_tool_calls"
    MAX_ITERATIONS = "max_iterations"
    INVALID_TOOL = "invalid_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    PERMISSION_DENIED = "permission_denied"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_ERROR = "tool_error"
    PROVIDER_ERROR = "provider_error"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"


@dataclass
class AgentState:
    session_id: int
    owner_id: str
    messages_used: int = 0
    knowledge_context: List[Dict[str, Any]] = field(default_factory=list)
    memory_context: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls_used: int = 0
    max_tool_calls: int = 3
    max_iterations: int = 6
    citations: List[AgentCitation] = field(default_factory=list)
    termination_reason: Optional[AgentTerminationReason] = None
    workflow_run_id: int = 0
    used_saved_memory: bool = False
    memory_status: Optional[str] = None
    explicit_save_allowed: bool = False
    conversation_transform: bool = False
    response_language: str = "zh-Hant"
    conversation_authority_available: bool = False
    available_tool_count: int = 0
    available_tool_names: List[str] = field(default_factory=list)
    provider_termination_type: Optional[str] = None
    provider_termination_types: List[str] = field(default_factory=list)
    tool_names_used: List[str] = field(default_factory=list)
    deterministic_memory_fallback_used: bool = False
    memory_retrieval_mode: Optional[str] = None
    memory_type_filter: Optional[str] = None
    memory_effective_top_k: Optional[int] = None
    memory_retrieval_hit_count: Optional[int] = None
    memory_best_similarity: Optional[float] = None


@dataclass(frozen=True)
class AgentRunResult:
    workflow_run_id: int
    status: str
    answer: str
    insufficient_info: bool
    retrieved_chunk_count: int
    citations: List[AgentCitation]
    provider: Optional[str]
    model: Optional[str]
    token_input: Optional[int]
    token_output: Optional[int]
    used_saved_memory: bool = False
    memory_status: Optional[str] = None
    termination_reason: AgentTerminationReason = AgentTerminationReason.COMPLETED
    tool_calls_used: int = 0


class AgentRuntimeError(Exception):
    def __init__(self, *, error_code: str, message: str, http_status_code: int = 502) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
