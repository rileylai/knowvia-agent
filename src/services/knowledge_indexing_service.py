from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, List, Mapping, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.rag import chunk_text_document
from src.repositories import KnowledgeChunkUpsert
from src.services.cost_tracker import CostTracker
from src.services.embedding_batch_service import (
    EmbeddingBatchError,
    EmbeddingBatchService,
)


INDEXING_ERROR_HTTP_STATUS = {
    "EMBEDDING_PROVIDER_NOT_CONFIGURED": HTTPStatus.SERVICE_UNAVAILABLE,
    "EMBEDDING_PROVIDER_ERROR": HTTPStatus.BAD_GATEWAY,
    "VECTOR_DIMENSION_MISMATCH": HTTPStatus.BAD_GATEWAY,
}


@dataclass(frozen=True)
class KnowledgeIndexingResult:
    indexed_chunk_count: int
    embedded_chunk_count: int
    embedding_metadata: Dict[str, Any]


class KnowledgeIndexingError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        failure_reason: str,
        http_status_code: int,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.failure_reason = failure_reason
        self.http_status_code = http_status_code


class KnowledgeIndexingService:
    """Index normalized text sources through the shared Knowledge contract."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        embedding_batch_service: Optional[EmbeddingBatchService],
        cost_tracker: Optional[CostTracker] = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._embedding_batch_service = embedding_batch_service
        self._cost_tracker = cost_tracker

    async def index_source_document(
        self,
        *,
        source_document_id: int,
        source_kind: str,
        source_display_name: str,
        raw_text: str,
        request_workflow_id: str,
        pages: Optional[List[str]] = None,
        citation_metadata: Optional[Mapping[str, object]] = None,
        owner_scope: str = "local",
        empty_chunks_error_code: str = "SOURCE_DOCUMENT_INDEXING_FAILED",
    ) -> KnowledgeIndexingResult:
        chunk_drafts = chunk_text_document(
            raw_text,
            source_kind=source_kind,
            source_display_name=source_display_name,
            pages=pages,
        )
        if not chunk_drafts:
            raise KnowledgeIndexingError(
                error_code=empty_chunks_error_code,
                message="No searchable text chunks were produced",
                failure_reason=empty_chunks_error_code,
                http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        if self._embedding_batch_service is None:
            raise KnowledgeIndexingError(
                error_code="EMBEDDING_PROVIDER_NOT_CONFIGURED",
                message="Embedding provider is not configured for Knowledge indexing",
                failure_reason="EMBEDDING_PROVIDER_NOT_CONFIGURED",
                http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            )

        try:
            embedding_result = await self._embedding_batch_service.embed(
                [draft.chunk_text for draft in chunk_drafts],
                metadata={
                    "workflow_id": request_workflow_id,
                    "operation": "index_knowledge_source",
                    "source_kind": source_kind,
                },
            )
        except EmbeddingBatchError as exc:
            failure_reason = self._normalize_failure_reason(exc.failure_reason)
            raise KnowledgeIndexingError(
                error_code=failure_reason,
                message="Failed to generate Knowledge chunk embeddings",
                failure_reason=failure_reason,
                http_status_code=INDEXING_ERROR_HTTP_STATUS.get(
                    failure_reason,
                    HTTPStatus.BAD_GATEWAY,
                ),
            ) from None

        if len(embedding_result.embeddings) != len(chunk_drafts):
            raise KnowledgeIndexingError(
                error_code="VECTOR_DIMENSION_MISMATCH",
                message="Embedding response does not match Knowledge chunk count",
                failure_reason="VECTOR_DIMENSION_MISMATCH",
                http_status_code=HTTPStatus.BAD_GATEWAY,
            )

        base_citation_metadata = dict(citation_metadata or {})
        indexed_chunks = [
            KnowledgeChunkUpsert(
                source_document_id=source_document_id,
                source_kind=draft.source_kind,
                chunk_index=draft.chunk_index,
                chunk_text=draft.chunk_text,
                source_display_name=source_display_name,
                locator=draft.locator,
                embedding=embedding,
                embedding_model=embedding_result.model,
                embedding_dimensions=embedding_result.dimensions,
                citation_metadata={
                    **draft.citation_meta,
                    **base_citation_metadata,
                },
                owner_scope=owner_scope,
                eligibility_status="eligible",
            )
            for draft, embedding in zip(
                chunk_drafts,
                embedding_result.embeddings,
            )
        ]
        embedding_metadata: Dict[str, Any] = {
            "embedding_provider": embedding_result.provider,
            "embedding_model": embedding_result.model,
            "embedding_dimensions": embedding_result.dimensions,
            "embedding_token_input": embedding_result.token_input,
            "embedding_batch_count": embedding_result.batch_count,
            "embedding_retry_count": embedding_result.retry_count,
        }
        if self._cost_tracker is not None:
            embedding_metadata["embedding_estimated_cost"] = (
                self._cost_tracker.estimate_embedding_cost(
                    provider_name=embedding_result.provider,
                    model=embedding_result.model,
                    token_input=embedding_result.token_input,
                )
            )

        with self._unit_of_work_factory() as unit_of_work:
            persisted_chunks = unit_of_work.chunks.upsert_source_document_chunks(
                source_document_id=source_document_id,
                chunks=indexed_chunks,
            )
            unit_of_work.source_documents.update_status(
                source_document_id=source_document_id,
                status="indexed",
            )

        return KnowledgeIndexingResult(
            indexed_chunk_count=len(persisted_chunks),
            embedded_chunk_count=len(embedding_result.embeddings),
            embedding_metadata=embedding_metadata,
        )

    def _normalize_failure_reason(self, failure_reason: str) -> str:
        normalized = failure_reason.strip().upper()
        if normalized in {
            "EMBEDDING_PROVIDER_ERROR",
            "EMBEDDING_PROVIDER_NOT_CONFIGURED",
            "VECTOR_DIMENSION_MISMATCH",
        }:
            return normalized
        return "EMBEDDING_PROVIDER_ERROR"
