from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)


CONTEXT_REQUIREMENT_DECISION_SCHEMA_NAME = "context_requirement_decision"
REFERENCE_BINDING_DECISION_SCHEMA_NAME = "reference_binding_decision"
EVIDENCE_READINESS_DECISION_SCHEMA_NAME = "evidence_readiness_decision"
MAX_CONTEXT_MEMORY_QUERY_CHARS = 500
MAX_CONTEXTUAL_FACETS = 2
MAX_CONTEXTUAL_FACET_ID_CHARS = 32
MAX_CONTEXTUAL_FACET_TEXT_CHARS = 120
MAX_REFERENCE_BINDINGS = 2
MAX_REFERENCE_CURRENT_SPAN_CHARS = 200
MAX_REFERENCE_SOURCE_SPAN_CHARS = 500
CONTEXTUAL_FACET_RETRIEVAL_MARKERS = frozenset(
    {"corpus", "knowledge", "memory", "retrieval", "saved", "search", "source", "tool"}
)


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


class ContextualFacet(BaseModel):
    """One bounded contextual dependency for a mixed Knowledge task."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: StrictStr = Field(min_length=1, max_length=MAX_CONTEXTUAL_FACET_ID_CHARS)
    text: StrictStr = Field(min_length=1, max_length=MAX_CONTEXTUAL_FACET_TEXT_CHARS)

    @field_validator("id", "text")
    @classmethod
    def facet_text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("contextual facet values must not be blank")
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("contextual facet values must be single-line")
        tokens = set(re.findall(r"[a-z0-9_]+", normalized.casefold()))
        if tokens & CONTEXTUAL_FACET_RETRIEVAL_MARKERS:
            raise ValueError("contextual facet values must describe context, not retrieval")
        if re.search(r"(?:^|\s)(?:and|or)(?:\s|$)", normalized.casefold()) or any(
            marker in normalized for marker in ("與", "及", "或")
        ):
            raise ValueError("each contextual facet must describe one atomic dependency")
        return normalized


class ContextRequirementDecision(BaseModel):
    """The bounded context authorities required for one agent task."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    needs_knowledge: StrictBool
    needs_memory: StrictBool
    contextual_facets: List[ContextualFacet] = Field(
        default_factory=list,
        max_length=MAX_CONTEXTUAL_FACETS,
    )
    # Direct memory recall remains compatible with the existing structured
    # selector path. Mixed Knowledge tasks must use contextual_facets instead.
    memory_query: Optional[StrictStr] = Field(
        default=None, max_length=MAX_CONTEXT_MEMORY_QUERY_CHARS
    )

    @field_validator("memory_query")
    @classmethod
    def memory_query_must_not_be_blank(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_memory_requirement(self) -> "ContextRequirementDecision":
        if self.needs_knowledge and self.needs_memory:
            if self.memory_query is not None:
                raise ValueError(
                    "mixed Knowledge tasks must use contextual_facets, not memory_query"
                )
            if not self.contextual_facets:
                raise ValueError(
                    "contextual_facets are required when Knowledge and Memory are both needed"
                )
        elif self.needs_memory:
            if self.memory_query is None:
                raise ValueError("memory_query is required for direct memory recall")
            if self.contextual_facets:
                raise ValueError(
                    "contextual_facets are only valid for mixed Knowledge tasks"
                )
        elif self.contextual_facets or self.memory_query is not None:
            raise ValueError(
                "memory requirements must be false when no Memory context is requested"
            )
        else:
            self.memory_query = None
        return self

    @classmethod
    def response_format(cls) -> Dict[str, Any]:
        schema = cls.model_json_schema()
        schema["required"] = [
            "needs_knowledge",
            "needs_memory",
            "contextual_facets",
            "memory_query",
        ]
        schema["properties"]["memory_query"].pop("default", None)
        return {
            "type": "json_schema",
            "json_schema": {
                "name": CONTEXT_REQUIREMENT_DECISION_SCHEMA_NAME,
                "strict": True,
                "schema": schema,
            },
        }


class EvidenceReadinessDecision(BaseModel):
    """The minimal pre-final decision for accepted Knowledge evidence."""

    model_config = ConfigDict(extra="forbid")

    ready: StrictBool

    @classmethod
    def response_format(cls) -> Dict[str, Any]:
        schema = cls.model_json_schema()
        schema["required"] = ["ready"]
        return {
            "type": "json_schema",
            "json_schema": {
                "name": EVIDENCE_READINESS_DECISION_SCHEMA_NAME,
                "strict": True,
                "schema": schema,
            },
        }


class ReferenceBinding(BaseModel):
    """A bounded textual reference proposed by the resolver."""

    model_config = ConfigDict(extra="forbid")

    current_span: StrictStr = Field(
        min_length=1,
        max_length=MAX_REFERENCE_CURRENT_SPAN_CHARS,
    )
    source_message_id: StrictInt = Field(gt=0)
    source_span: StrictStr = Field(
        min_length=1,
        max_length=MAX_REFERENCE_SOURCE_SPAN_CHARS,
    )

    @field_validator("current_span", "source_span")
    @classmethod
    def span_must_be_single_line(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reference spans must not be blank")
        if "\n" in value or "\r" in value:
            raise ValueError("reference spans must be single-line")
        return value


class ReferenceBindingDecision(BaseModel):
    """The resolver's only semantic output for a substantive request."""

    model_config = ConfigDict(extra="forbid")

    reference_bindings: List[ReferenceBinding] = Field(max_length=MAX_REFERENCE_BINDINGS)

    @classmethod
    def response_format(cls) -> Dict[str, Any]:
        schema = cls.model_json_schema()
        schema["required"] = ["reference_bindings"]
        return {
            "type": "json_schema",
            "json_schema": {
                "name": REFERENCE_BINDING_DECISION_SCHEMA_NAME,
                "strict": True,
                "schema": schema,
            },
        }


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
    PROVIDER_CONTRACT_ERROR = "provider_contract_error"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"


@dataclass
class AgentState:
    session_id: int
    owner_id: str
    history_message_count: int = 0
    history_roles: List[str] = field(default_factory=list)
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
    reference_bindings: List[ReferenceBinding] = field(default_factory=list)
    deterministic_memory_fallback_used: bool = False
    memory_retrieval_mode: Optional[str] = None
    memory_type_filter: Optional[str] = None
    memory_effective_top_k: Optional[int] = None
    memory_retrieval_hit_count: Optional[int] = None
    memory_best_similarity: Optional[float] = None
    memory_required_dependency_count: int = 0
    memory_resolved_dependency_count: int = 0
    context_requirement_decision: Optional[ContextRequirementDecision] = None
    insufficient_info_source: Optional[str] = None
    knowledge_candidate_count: int = 0
    knowledge_accepted_evidence_count: int = 0
    knowledge_context_count: int = 0
    knowledge_best_score: Optional[float] = None
    knowledge_relevance_floor: Optional[float] = None


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
