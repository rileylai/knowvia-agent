from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.agent.models import AgentCitation
from src.memory import (
    ALLOWED_MEMORY_TYPES,
    is_broad_memory_recall_query,
    memory_recall_type_filter,
)
from src.observability.redaction import sanitize_sensitive_text
from src.providers import EmbeddingClient, EmbeddingRequest
from src.rag import ProductionChunkRetriever, RetrievedChunk
from src.repositories.memory_repository import LongTermMemorySnapshot
from src.services.memory import MemoryService, MemoryServiceError
from src.tools import Tool, ToolContext, ToolResult, ToolSpec


AGENT_TOOL_NAMES = frozenset({"search_knowledge", "search_memory", "save_memory"})
MAX_KNOWLEDGE_EVIDENCE_CHARS = 1200
MAX_TOOL_SAFE_TEXT_CHARS = 4000
MAX_BROAD_MEMORY_RESULTS = 3
KNOWLEDGE_SOURCE_KINDS = frozenset({"notion", "pdf", "image", "url"})


class AgentToolRegistryError(Exception):
    pass


class AgentToolNotAllowedError(AgentToolRegistryError):
    pass


class AgentToolAdapter(Tool, ABC):
    arguments_model: Type[BaseModel]

    def validate_arguments(self, arguments: Dict[str, Any]) -> BaseModel:
        return self.arguments_model.model_validate(arguments)

    @abstractmethod
    async def run_validated(
        self,
        context: ToolContext,
        arguments: BaseModel,
    ) -> ToolResult:
        raise NotImplementedError

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        try:
            parsed = self.validate_arguments(arguments)
        except ValidationError:
            return ToolResult.failure(
                code="invalid_arguments",
                message="Tool arguments are invalid.",
            )
        return await self.run_validated(context, parsed)


class SearchKnowledgeArguments(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)
    source_kinds: Optional[List[str]] = Field(default=None, max_length=4)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @field_validator("source_kinds")
    @classmethod
    def source_kinds_must_be_supported(
        cls,
        values: Optional[List[str]],
    ) -> Optional[List[str]]:
        if values is None:
            return None
        normalized = [value.strip().lower() for value in values]
        if any(value not in KNOWLEDGE_SOURCE_KINDS for value in normalized):
            raise ValueError("source_kinds contains an unsupported value")
        return normalized


class SearchMemoryArguments(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=5)
    memory_type: Optional[str] = None

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @field_validator("memory_type")
    @classmethod
    def memory_type_must_be_allowed(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in ALLOWED_MEMORY_TYPES:
            raise ValueError("memory_type is not supported")
        return normalized


class SaveMemoryArguments(BaseModel):
    memory_type: str
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("memory_type")
    @classmethod
    def memory_type_must_be_allowed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_MEMORY_TYPES:
            raise ValueError("memory_type is not supported")
        return normalized

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must not be blank")
        return normalized


class KnowledgeSearchTool(AgentToolAdapter):
    arguments_model = SearchKnowledgeArguments

    def __init__(
        self,
        *,
        retriever: ProductionChunkRetriever,
        embedding_client: Optional[EmbeddingClient],
    ) -> None:
        self._retriever = retriever
        self._embedding_client = embedding_client
        self._spec = ToolSpec(
            name="search_knowledge",
            description="Search indexed enterprise knowledge for grounded evidence.",
            input_schema=SearchKnowledgeArguments.model_json_schema(),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run_validated(
        self,
        context: ToolContext,
        arguments: BaseModel,
    ) -> ToolResult:
        parsed = arguments
        assert isinstance(parsed, SearchKnowledgeArguments)
        owner_id = _context_owner_id(context)
        if owner_id is None:
            return ToolResult.failure("permission_denied", "Knowledge scope is unavailable.")

        query_embedding = await _build_query_embedding(
            embedding_client=self._embedding_client,
            query=parsed.query,
            workflow_id=context.workflow_id,
        )
        source_kinds = _bounded_source_kinds(
            requested=parsed.source_kinds,
            backend_scope=context.metadata.get("source_kinds"),
        )
        retrieval = self._retriever.retrieve_with_metadata(
            query_text=parsed.query,
            top_k=parsed.top_k,
            page_ids=_bounded_scope_list(context.metadata.get("page_ids")),
            section_paths=_bounded_scope_list(context.metadata.get("section_paths")),
            source_kinds=source_kinds,
            query_embedding=query_embedding,
            allow_legacy_embedding_scoring=False,
            owner_scope=owner_id,
        )
        citations = _build_citations(retrieval.chunks)
        evidence = [
            {
                "id": f"K{index}",
                "text": _bounded_text(chunk.chunk_text, MAX_KNOWLEDGE_EVIDENCE_CHARS),
                "source_kind": chunk.source_kind,
                "source_display_name": chunk.source_display_name or chunk.notion_path,
                "locator": chunk.locator or chunk.notion_path,
                "score": round(chunk.score, 6),
            }
            for index, chunk in enumerate(retrieval.chunks, start=1)
        ]
        structured = {
            "authority": "knowledge_evidence",
            "retrieved_chunk_count": len(retrieval.chunks),
            "insufficient_info": not bool(evidence),
            "evidence": evidence,
            "citations": [citation.__dict__ for citation in citations],
            "retrieval_mode": retrieval.retrieval_mode,
        }
        safe_text = _bounded_text(
            "\n\n".join(
                f"[{item['id']}] {item['source_display_name']} · {item['locator']}\n{item['text']}"
                for item in evidence
            )
            or "No indexed knowledge evidence matched the query.",
            MAX_TOOL_SAFE_TEXT_CHARS,
        )
        return ToolResult.success(content=safe_text, structured_content=structured)


class MemorySearchTool(AgentToolAdapter):
    arguments_model = SearchMemoryArguments

    def __init__(self, *, memory_service: MemoryService) -> None:
        self._memory_service = memory_service
        self._spec = ToolSpec(
            name="search_memory",
            description="Search the current owner's explicitly saved conversational memory.",
            input_schema=SearchMemoryArguments.model_json_schema(),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run_validated(
        self,
        context: ToolContext,
        arguments: BaseModel,
    ) -> ToolResult:
        parsed = arguments
        assert isinstance(parsed, SearchMemoryArguments)
        owner_id = _context_owner_id(context)
        if owner_id is None:
            return ToolResult.failure("permission_denied", "Memory owner scope is unavailable.")
        backend_recall_query = context.metadata.get("memory_recall_query")
        has_backend_recall_query = (
            isinstance(backend_recall_query, str) and bool(backend_recall_query.strip())
        )
        effective_query = (
            backend_recall_query.strip()
            if has_backend_recall_query
            else parsed.query
        )
        retrieval_mode = (
            "broad" if is_broad_memory_recall_query(effective_query) else "direct"
        )
        backend_memory_type = memory_recall_type_filter(effective_query)
        selected_memory_type = (
            backend_memory_type
            if has_backend_recall_query
            else backend_memory_type or parsed.memory_type
        )
        effective_top_k = (
            min(parsed.top_k, MAX_BROAD_MEMORY_RESULTS)
            if retrieval_mode == "broad"
            else 1
        )
        try:
            memories = await self._memory_service.search_memories(
                owner_id=owner_id,
                query=effective_query,
                top_k=effective_top_k,
                memory_type=selected_memory_type,
            )
        except MemoryServiceError:
            return ToolResult.failure("tool_error", "Saved memory search failed.")

        saved_memories = [
            {
                "id": memory.id,
                "memory_type": memory.memory_type,
                "content": _bounded_text(memory.content, MAX_KNOWLEDGE_EVIDENCE_CHARS),
                "score": memory.score,
            }
            for memory in memories
        ]
        structured = {
            "authority": "saved_memory",
            "saved_memories": saved_memories,
            "used_saved_memory": bool(saved_memories),
            "citations": [],
            "retrieval_mode": retrieval_mode,
            "requested_top_k": parsed.top_k,
            "effective_top_k": effective_top_k,
            "retrieval_hit_count": len(saved_memories),
            "best_similarity": memories[0].score if memories else None,
            "memory_type": selected_memory_type,
        }
        memory_text = "\n".join(f"- {item['content']}" for item in saved_memories)
        if not memory_text:
            memory_text = "No relevant saved memory matched the query."
        best_similarity = (
            f"{memories[0].score:.6f}"
            if memories and memories[0].score is not None
            else "none"
        )
        metadata_text = (
            "[saved_memory "
            f"authority=saved_memory mode={retrieval_mode} "
            f"requested_top_k={parsed.top_k} effective_top_k={effective_top_k} "
            f"memory_type={selected_memory_type or 'any'} "
            f"hits={len(saved_memories)} best_similarity={best_similarity}]"
        )
        safe_text = _bounded_text(
            metadata_text + "\n" + memory_text,
            MAX_TOOL_SAFE_TEXT_CHARS,
        )
        return ToolResult.success(content=safe_text, structured_content=structured)


class MemorySaveTool(AgentToolAdapter):
    arguments_model = SaveMemoryArguments

    def __init__(self, *, memory_service: MemoryService) -> None:
        self._memory_service = memory_service
        self._spec = ToolSpec(
            name="save_memory",
            description="Save a memory only when the original user message explicitly asks to remember it.",
            input_schema=SaveMemoryArguments.model_json_schema(),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run_validated(
        self,
        context: ToolContext,
        arguments: BaseModel,
    ) -> ToolResult:
        parsed = arguments
        assert isinstance(parsed, SaveMemoryArguments)
        owner_id = _context_owner_id(context)
        explicit_content = context.metadata.get("explicit_save_content")
        explicit_type = context.metadata.get("explicit_save_memory_type")
        if (
            owner_id is None
            or context.metadata.get("explicit_save_allowed") is not True
            or not isinstance(explicit_content, str)
            or not isinstance(explicit_type, str)
        ):
            return ToolResult.failure(
                "permission_denied",
                "Memory save requires an explicit user request.",
            )
        # The orchestrator's explicit-save intent is trusted. The provider's
        # valid tool arguments select the tool shape, but cannot override the
        # backend-classified type or original user content.
        try:
            result = await self._memory_service.save_memory(
                owner_id=owner_id,
                content=explicit_content,
                memory_type=explicit_type,
                source_session_id=context.metadata.get("session_id"),
                source_message_id=context.metadata.get("user_message_id"),
            )
        except MemoryServiceError:
            return ToolResult.failure("tool_error", "Memory save failed.")

        return ToolResult.success(
            content="Memory saved" if result.status == "saved" else "Already saved",
            structured_content={
                "authority": "saved_memory",
                "status": result.status,
                "memory_id": result.memory.id,
                "saved_memories": [
                    {
                        "id": result.memory.id,
                        "memory_type": result.memory.memory_type,
                        "content": _bounded_text(
                            result.memory.content,
                            MAX_KNOWLEDGE_EVIDENCE_CHARS,
                        ),
                        "status": result.status,
                    }
                ],
            },
        )


class AgentToolRegistry:
    def __init__(self, tools: Optional[List[AgentToolAdapter]] = None) -> None:
        self._tools: Dict[str, AgentToolAdapter] = {}
        for tool in tools or []:
            self.register_tool(tool)

    def register_tool(self, tool: AgentToolAdapter) -> None:
        name = tool.spec.name.strip().lower()
        if name not in AGENT_TOOL_NAMES:
            raise AgentToolNotAllowedError(f"Tool is not allowed: '{name}'")
        if name in self._tools:
            raise AgentToolRegistryError(f"Tool is already registered: '{name}'")
        self._tools[name] = tool

    def list_tool_names(self) -> List[str]:
        return sorted(self._tools)

    def get_tool(self, name: str) -> AgentToolAdapter:
        normalized = name.strip().lower()
        if normalized not in AGENT_TOOL_NAMES or normalized not in self._tools:
            raise AgentToolNotAllowedError(f"Tool is not allowed: '{normalized}'")
        return self._tools[normalized]

    def tool_specs(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.spec.name,
                    "description": tool.spec.description or "",
                    "parameters": tool.spec.input_schema,
                },
            }
            for tool in (self._tools[name] for name in self.list_tool_names())
        ]

    async def call_tool(
        self,
        name: str,
        *,
        context: ToolContext,
        arguments: Dict[str, Any],
    ) -> ToolResult:
        tool = self.get_tool(name)
        result = await tool.run(context=context, arguments=arguments)
        result.name = name.strip().lower()
        if result.safe_text is None:
            result.safe_text = result.content
        return result


def build_agent_tool_registry(
    *,
    retriever: ProductionChunkRetriever,
    embedding_client: Optional[EmbeddingClient],
    memory_service: MemoryService,
) -> AgentToolRegistry:
    return AgentToolRegistry(
        tools=[
            KnowledgeSearchTool(
                retriever=retriever,
                embedding_client=embedding_client,
            ),
            MemorySearchTool(memory_service=memory_service),
            MemorySaveTool(memory_service=memory_service),
        ]
    )


def _context_owner_id(context: ToolContext) -> Optional[str]:
    value = context.owner_id or context.metadata.get("owner_id")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _bounded_scope_list(value: object) -> Optional[List[str]]:
    if not isinstance(value, list):
        return None
    normalized = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return normalized[:20] or None


def _bounded_source_kinds(
    *,
    requested: Optional[List[str]],
    backend_scope: object,
) -> Optional[List[str]]:
    if not isinstance(backend_scope, list):
        return requested
    allowed = {
        value.strip().lower()
        for value in backend_scope
        if isinstance(value, str) and value.strip().lower() in KNOWLEDGE_SOURCE_KINDS
    }
    if requested is None:
        return sorted(allowed) or None
    return [value for value in requested if value in allowed]


async def _build_query_embedding(
    *,
    embedding_client: Optional[EmbeddingClient],
    query: str,
    workflow_id: str,
) -> Optional[List[float]]:
    if embedding_client is None:
        return None
    try:
        response = await embedding_client.embed(
            EmbeddingRequest(
                inputs=[query],
                dimensions=1536,
                metadata={"workflow_id": workflow_id, "operation": "agent_search_knowledge"},
            )
        )
    except Exception:
        return None
    if len(response.embeddings) != 1:
        return None
    try:
        values = [float(value) for value in response.embeddings[0]]
    except (TypeError, ValueError):
        return None
    return values if len(values) == 1536 else None


def _build_citations(chunks: List[RetrievedChunk]):
    citations: List[AgentCitation] = []
    seen_citations = set()
    for chunk in chunks:
        legacy_path = chunk.notion_path.strip()
        source_kind = chunk.source_kind.strip().lower() or "notion"
        source_display_name = (
            chunk.source_display_name or legacy_path or "unknown source"
        ).strip()
        locator = (chunk.locator or legacy_path or f"chunk {chunk.chunk_index + 1}").strip()
        citation_metadata = _parse_citation_metadata(chunk.citation_metadata)
        citation_key = (source_kind, source_display_name, locator)
        if not source_display_name or not locator or citation_key in seen_citations:
            continue
        seen_citations.add(citation_key)
        citations.append(
            AgentCitation(
                notion_path=legacy_path,
                page_id=chunk.notion_page_id,
                score=round(chunk.score, 6),
                source_kind=source_kind,
                source_display_name=source_display_name,
                locator=locator,
                source_url=chunk.source_url,
                image_index=_optional_int(citation_metadata.get("image_index")),
                sequence_index=_optional_int(citation_metadata.get("sequence_index")),
                original_filename=_optional_string(
                    citation_metadata.get("original_filename")
                ),
            )
        )
    return citations


def _parse_citation_metadata(value: Optional[str]) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _optional_int(value: object) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_string(value: object) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _bounded_text(value: str, limit: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= limit:
        return sanitize_sensitive_text(normalized)
    return sanitize_sensitive_text(normalized[: max(0, limit - 1)].rstrip() + "…")
