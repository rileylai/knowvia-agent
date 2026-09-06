from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.memory import (
    ALLOWED_MEMORY_TYPES,
    MAX_MEMORY_CONTENT_CHARS,
    classify_memory_type,
    is_broad_memory_recall_query,
    normalize_memory_content,
    normalize_memory_duplicate_key,
)
from src.providers import EmbeddingClient, EmbeddingClientError, EmbeddingRequest
from src.repositories.memory_repository import LongTermMemorySnapshot


MEMORY_EMBEDDING_MODEL = "text-embedding-3-small"
MEMORY_EMBEDDING_DIMENSIONS = 1536
MEMORY_SEARCH_TOP_K = 5
# MemoryRepository exposes normalized cosine similarity in [0, 1]. These
# floors separate the observed direct-match range (~0.46) from the observed
# unrelated range (~0.35), while allowing generic broad recall (~0.25).
MEMORY_DIRECT_RELEVANCE_FLOOR = 0.40
MEMORY_BROAD_RELEVANCE_FLOOR = 0.20


class MemoryServiceError(Exception):
    pass


class MemoryValidationError(MemoryServiceError):
    pass


class MemoryEmbeddingError(MemoryServiceError):
    pass


@dataclass(frozen=True)
class MemorySaveResult:
    status: str
    memory: LongTermMemorySnapshot


class MemoryService:
    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        embedding_client: Optional[EmbeddingClient],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._embedding_client = embedding_client

    async def save_memory(
        self,
        *,
        owner_id: str,
        content: str,
        memory_type: Optional[str] = None,
        source_session_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
    ) -> MemorySaveResult:
        normalized_owner_id = self._normalize_owner_id(owner_id)
        normalized_content = self._normalize_content(content)
        selected_type = (memory_type or classify_memory_type(normalized_content)).strip().lower()
        if selected_type not in ALLOWED_MEMORY_TYPES:
            raise MemoryValidationError("memory_type is not supported")
        duplicate_key = normalize_memory_duplicate_key(normalized_content)

        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.memories.get_exact(
                owner_id=normalized_owner_id,
                memory_type=selected_type,
                content_normalized=duplicate_key,
            )
            if existing is not None:
                return MemorySaveResult(status="already_saved", memory=existing)

        embedding, embedding_model = await self._embed(normalized_content)

        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.memories.get_exact(
                owner_id=normalized_owner_id,
                memory_type=selected_type,
                content_normalized=duplicate_key,
            )
            if existing is not None:
                return MemorySaveResult(status="already_saved", memory=existing)
            memory = unit_of_work.memories.create(
                owner_id=normalized_owner_id,
                memory_type=selected_type,
                content=normalized_content,
                content_normalized=duplicate_key,
                embedding=embedding,
                embedding_model=embedding_model,
                embedding_dimensions=MEMORY_EMBEDDING_DIMENSIONS,
                source_session_id=source_session_id,
                source_message_id=source_message_id,
            )
            return MemorySaveResult(status="saved", memory=memory)

    def list_memories(self, *, owner_id: str) -> List[LongTermMemorySnapshot]:
        normalized_owner_id = self._normalize_owner_id(owner_id)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.memories.list_active(owner_id=normalized_owner_id)

    def delete_memory(self, *, owner_id: str, memory_id: int) -> bool:
        normalized_owner_id = self._normalize_owner_id(owner_id)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.memories.delete(
                owner_id=normalized_owner_id,
                memory_id=memory_id,
            )

    async def search_memories(
        self,
        *,
        owner_id: str,
        query: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = MEMORY_SEARCH_TOP_K,
        memory_type: Optional[str] = None,
    ) -> List[LongTermMemorySnapshot]:
        normalized_owner_id = self._normalize_owner_id(owner_id)
        normalized_query = self._normalize_content(query)
        normalized_memory_type = None
        if memory_type is not None:
            normalized_memory_type = memory_type.strip().lower()
            if normalized_memory_type not in ALLOWED_MEMORY_TYPES:
                raise MemoryValidationError("memory_type is not supported")
        embedding = query_embedding
        if embedding is None:
            embedding, _ = await self._embed(normalized_query)
        with self._unit_of_work_factory() as unit_of_work:
            matches = unit_of_work.memories.search_by_vector(
                owner_id=normalized_owner_id,
                query_embedding=embedding,
                top_k=top_k,
                memory_type=normalized_memory_type,
            )
        return self._select_relevant_memories(
            matches=matches,
            query=normalized_query,
        )

    def _select_relevant_memories(
        self,
        *,
        matches: List[LongTermMemorySnapshot],
        query: str,
    ) -> List[LongTermMemorySnapshot]:
        if not matches:
            return []
        is_broad_recall = is_broad_memory_recall_query(query)
        relevance_floor = (
            MEMORY_BROAD_RELEVANCE_FLOOR
            if is_broad_recall
            else MEMORY_DIRECT_RELEVANCE_FLOOR
        )
        relevant = [
            memory
            for memory in matches
            if memory.score is not None and memory.score >= relevance_floor
        ]
        if is_broad_recall:
            return relevant
        return relevant[:1]

    async def _embed(self, content: str) -> tuple[List[float], str]:
        if self._embedding_client is None:
            raise MemoryEmbeddingError("Memory embedding provider is not configured")
        try:
            response = await self._embedding_client.embed(
                EmbeddingRequest(
                    inputs=[content],
                    model=MEMORY_EMBEDDING_MODEL,
                    dimensions=MEMORY_EMBEDDING_DIMENSIONS,
                    metadata={"operation": "memory"},
                )
            )
        except EmbeddingClientError as exc:
            raise MemoryEmbeddingError("Memory embedding failed") from exc
        if len(response.embeddings) != 1:
            raise MemoryEmbeddingError("Memory embedding returned an invalid result")
        try:
            embedding = [float(value) for value in response.embeddings[0]]
        except (TypeError, ValueError) as exc:
            raise MemoryEmbeddingError("Memory embedding returned non-numeric values") from exc
        if len(embedding) != MEMORY_EMBEDDING_DIMENSIONS or not all(
            math.isfinite(value) for value in embedding
        ):
            raise MemoryEmbeddingError("Memory embedding dimensions are invalid")
        return embedding, response.model

    def _normalize_owner_id(self, owner_id: str) -> str:
        normalized = owner_id.strip()
        if not normalized:
            raise MemoryValidationError("owner_id must not be empty")
        return normalized

    def _normalize_content(self, content: str) -> str:
        try:
            return normalize_memory_content(content)
        except ValueError as exc:
            raise MemoryValidationError(str(exc)) from exc
