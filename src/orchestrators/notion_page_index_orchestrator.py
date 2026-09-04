from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from src.db.unit_of_work import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from src.providers import EmbeddingClient
from src.rag import (
    BlockPathNode,
    BlockPathSnapshot,
    ChunkerBlock,
    ChunkerPage,
    build_block_paths,
    chunk_notion_page,
)
from src.repositories import (
    ChunkRepositoryError,
    NotionBlockSnapshot,
    NotionChunkUpsert,
    StaleNotionPageSnapshotError,
)
from src.services import (
    CostTracker,
    EmbeddingBatchError,
    EmbeddingBatchService,
    InfrastructureExecutionTimeout,
    STANDARD_FAILURE_REASONS,
    WorkflowRunService,
    is_known_synthetic_notion_page_id,
)
from src.tools import ToolContext, ToolRegistry

NOTION_READER_TOOL_NAME = "notion_reader"
SYNC_MODE_MANUAL = "manual"
SYNC_MODE_AUTO_AFTER_ACCEPT = "auto_after_accept"
ALLOWED_SYNC_MODES = {SYNC_MODE_MANUAL, SYNC_MODE_AUTO_AFTER_ACCEPT}
EMBEDDING_DIMENSIONS = 1536

TOOL_ERROR_TO_HTTP_STATUS: Dict[str, int] = {
    "INVALID_ARGUMENT": HTTPStatus.BAD_REQUEST,
    "NOTION_PAGE_NOT_FOUND": HTTPStatus.NOT_FOUND,
    "NOTION_AUTH_FAILED": HTTPStatus.BAD_GATEWAY,
    "NOTION_BLOCK_FETCH_FAILED": HTTPStatus.BAD_GATEWAY,
}


@dataclass
class NotionPageIndexResult:
    workflow_run_id: int
    status: str
    notion_page_id: str
    page_title: str
    notion_path: str
    indexed_block_count: int


@dataclass
class NotionIndexedPageSnapshot:
    notion_page_db_id: int
    notion_page_id: str
    page_title: str
    notion_path: str
    indexed_block_count: int
    indexed_chunk_count: int
    last_edited_time: Optional[datetime] = None
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimensions: Optional[int] = None
    embedding_token_input: Optional[int] = None
    embedding_estimated_cost: Optional[float] = None
    embedding_batch_count: Optional[int] = None
    embedding_retry_count: Optional[int] = None


@dataclass
class PreparedNotionPageSnapshot:
    page_payload: _ToolPagePayload
    block_paths: List[BlockPathSnapshot]
    chunk_upserts: List[NotionChunkUpsert]
    embedding_metadata: Dict[str, Optional[object]]


class NotionPageIndexError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
        workflow_run_id: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason
        self.workflow_run_id = workflow_run_id


class _ToolPagePayload(BaseModel):
    page_id: str
    title: str
    notion_path: str
    parent_notion_page_id: Optional[str] = None
    last_edited_time: Optional[datetime] = None


class _ToolBlockPayload(BaseModel):
    block_id: str
    block_type: str
    content_text: str = ""
    children: List["_ToolBlockPayload"] = Field(default_factory=list)


class NotionPageIndexOrchestrator:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        unit_of_work_factory: UnitOfWorkFactory,
        workflow_run_service: WorkflowRunService,
        embedding_client: Optional[EmbeddingClient] = None,
        embedding_batch_service: Optional[EmbeddingBatchService] = None,
        cost_tracker: Optional[CostTracker] = None,
        allow_synthetic_postgres_persistence: bool = False,
        source_is_synthetic: bool = False,
    ) -> None:
        self._tool_registry = tool_registry
        self._unit_of_work_factory = unit_of_work_factory
        self._workflow_run_service = workflow_run_service
        if embedding_client is not None and embedding_batch_service is not None:
            raise ValueError(
                "Provide embedding_batch_service instead of embedding_client"
            )
        self._embedding_batch_service = embedding_batch_service
        if self._embedding_batch_service is None and embedding_client is not None:
            self._embedding_batch_service = EmbeddingBatchService(
                embedding_client=embedding_client,
                model="text-embedding-3-small",
                dimensions=EMBEDDING_DIMENSIONS,
            )
        self._cost_tracker = cost_tracker
        self._allow_synthetic_postgres_persistence = (
            allow_synthetic_postgres_persistence
        )
        self._source_is_synthetic = source_is_synthetic

    def start_indexing_workflow(
        self,
        *,
        page_id: str,
        request_workflow_id: str,
        sync_mode: str,
    ) -> int:
        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="indexing",
            metadata_json=json.dumps(
                {
                    "sync_mode": sync_mode,
                    "operation": "index_page",
                    "page_id": page_id,
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )
        return int(workflow_run.id)

    def mark_indexing_workflow_succeeded(
        self,
        *,
        workflow_run_id: int,
        page_id: str,
        sync_mode: str,
        snapshot: NotionIndexedPageSnapshot,
    ) -> None:
        self._workflow_run_service.mark_workflow_succeeded(
            workflow_run_id,
            metadata_json=json.dumps(
                self._build_success_metadata(
                    operation="index_page",
                    sync_mode=sync_mode,
                    page_id=page_id,
                    snapshot=snapshot,
                ),
                sort_keys=True,
            ),
        )

    def mark_indexing_workflow_failed(
        self,
        *,
        workflow_run_id: int,
        page_id: str,
        sync_mode: str,
        error_code: str,
        failure_reason: str,
    ) -> None:
        self._workflow_run_service.mark_workflow_failed(
            workflow_run_id,
            failure_reason=failure_reason,
            metadata_json=json.dumps(
                {
                    "operation": "index_page",
                    "sync_mode": sync_mode,
                    "page_id": page_id,
                    "error_code": error_code,
                },
                sort_keys=True,
            ),
        )

    async def index_page(
        self,
        *,
        page_id: str,
        request_workflow_id: str,
        sync_mode: str = SYNC_MODE_MANUAL,
    ) -> NotionPageIndexResult:
        normalized_page_id = page_id.strip()
        if not normalized_page_id:
            raise NotionPageIndexError(
                error_code="INVALID_ARGUMENT",
                message="page_id must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            )
        normalized_sync_mode = sync_mode.strip().lower()
        if normalized_sync_mode not in ALLOWED_SYNC_MODES:
            raise NotionPageIndexError(
                error_code="INVALID_ARGUMENT",
                message=(
                    "sync_mode must be one of: "
                    f"{', '.join(sorted(ALLOWED_SYNC_MODES))}"
                ),
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            )

        workflow_run_id = self.start_indexing_workflow(
            page_id=normalized_page_id,
            request_workflow_id=request_workflow_id,
            sync_mode=normalized_sync_mode,
        )

        try:
            snapshot = await self.index_page_snapshot(
                page_id=normalized_page_id,
                request_workflow_id=request_workflow_id,
            )
            self.mark_indexing_workflow_succeeded(
                workflow_run_id=workflow_run_id,
                page_id=normalized_page_id,
                sync_mode=normalized_sync_mode,
                snapshot=snapshot,
            )
        except NotionPageIndexError as exc:
            self.mark_indexing_workflow_failed(
                workflow_run_id=workflow_run_id,
                page_id=normalized_page_id,
                sync_mode=normalized_sync_mode,
                error_code=exc.error_code,
                failure_reason=exc.failure_reason,
            )
            raise NotionPageIndexError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=workflow_run_id,
            ) from exc

        return NotionPageIndexResult(
            workflow_run_id=workflow_run_id,
            status="succeeded",
            notion_page_id=snapshot.notion_page_id,
            page_title=snapshot.page_title,
            notion_path=snapshot.notion_path,
            indexed_block_count=snapshot.indexed_block_count,
        )

    async def index_page_snapshot(
        self,
        *,
        page_id: str,
        request_workflow_id: str,
    ) -> NotionIndexedPageSnapshot:
        prepared_snapshot = await self.prepare_page_snapshot(
            page_id=page_id,
            request_workflow_id=request_workflow_id,
        )
        return self.persist_prepared_page_snapshot(
            prepared_snapshot=prepared_snapshot,
        )

    async def prepare_page_snapshot(
        self,
        *,
        page_id: str,
        request_workflow_id: str,
    ) -> PreparedNotionPageSnapshot:
        normalized_page_id = page_id.strip()
        if not normalized_page_id:
            raise NotionPageIndexError(
                error_code="INVALID_ARGUMENT",
                message="page_id must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            )

        tool_result = await self._tool_registry.call_tool(
            NOTION_READER_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "index_page_snapshot",
                    "page_id": normalized_page_id,
                },
            ),
            arguments={"page_id": normalized_page_id},
        )

        if tool_result.is_error:
            error_code = "UNKNOWN_ERROR"
            message = "Notion reader failed"
            if tool_result.error is not None:
                error_code = tool_result.error.code
                message = tool_result.error.message
            raise NotionPageIndexError(
                error_code=error_code,
                message=message,
                http_status_code=self._http_status_for_tool_error(error_code),
                failure_reason=self._normalize_failure_reason(error_code),
            )

        try:
            page_payload, block_payloads = self._parse_tool_payload(
                tool_result.structured_content
            )
            block_paths = build_block_paths(
                page_path=page_payload.notion_path,
                blocks=[
                    self._to_block_path_node(block_payload)
                    for block_payload in block_payloads
                ],
            )
            chunk_upserts = self._build_chunk_upserts(
                page_payload=page_payload,
                block_paths=block_paths,
            )
            embedded_chunk_upserts = chunk_upserts
            embedding_metadata = self._empty_embedding_metadata()
            if chunk_upserts:
                embedded_chunk_upserts, embedding_metadata = await self._embed_chunk_upserts(
                    page_id=page_payload.page_id,
                    request_workflow_id=request_workflow_id,
                    chunk_upserts=chunk_upserts,
                )

            return PreparedNotionPageSnapshot(
                page_payload=page_payload,
                block_paths=block_paths,
                chunk_upserts=embedded_chunk_upserts,
                embedding_metadata=embedding_metadata,
            )
        except NotionPageIndexError:
            raise
        except InfrastructureExecutionTimeout:
            raise
        except Exception as exc:
            raise NotionPageIndexError(
                error_code="INDEX_PAGE_PREPARATION_FAILED",
                message=f"Failed to prepare indexed page: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            ) from exc

    def persist_prepared_page_snapshot(
        self,
        *,
        prepared_snapshot: PreparedNotionPageSnapshot,
        unit_of_work: Optional[SqlAlchemyUnitOfWork] = None,
    ) -> NotionIndexedPageSnapshot:
        try:
            if unit_of_work is not None:
                return self._persist_indexed_page_in_unit_of_work(
                    prepared_snapshot=prepared_snapshot,
                    unit_of_work=unit_of_work,
                )
            with self._unit_of_work_factory() as owned_unit_of_work:
                return self._persist_indexed_page_in_unit_of_work(
                    prepared_snapshot=prepared_snapshot,
                    unit_of_work=owned_unit_of_work,
                )
        except NotionPageIndexError:
            raise
        except InfrastructureExecutionTimeout:
            raise
        except StaleNotionPageSnapshotError as exc:
            raise NotionPageIndexError(
                error_code="STALE_PAGE_SNAPSHOT",
                message=str(exc),
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="STALE_PAGE_SNAPSHOT",
            ) from exc
        except ChunkRepositoryError as exc:
            raise NotionPageIndexError(
                error_code="VECTOR_UPSERT_FAILED",
                message=f"Failed to upsert notion chunks: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="VECTOR_UPSERT_FAILED",
            ) from exc
        except Exception as exc:
            raise NotionPageIndexError(
                error_code="INDEX_PAGE_PERSIST_FAILED",
                message=f"Failed to persist indexed page: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            ) from exc

    def _persist_indexed_page_in_unit_of_work(
        self,
        *,
        prepared_snapshot: PreparedNotionPageSnapshot,
        unit_of_work: SqlAlchemyUnitOfWork,
    ) -> NotionIndexedPageSnapshot:
        page_payload = prepared_snapshot.page_payload
        if (
            getattr(unit_of_work, "database_dialect", None) == "postgresql"
            and not self._allow_synthetic_postgres_persistence
            and (
                self._source_is_synthetic
                or is_known_synthetic_notion_page_id(page_payload.page_id)
            )
        ):
            raise NotionPageIndexError(
                error_code="SYNTHETIC_DATA_NOT_ALLOWED",
                message="Synthetic Notion data cannot be persisted to PostgreSQL",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="SYNTHETIC_DATA_NOT_ALLOWED",
            )
        notion_page = unit_of_work.notion_pages.upsert_page_snapshot(
            notion_page_id=page_payload.page_id,
            title=page_payload.title,
            notion_path=page_payload.notion_path,
            parent_notion_page_id=page_payload.parent_notion_page_id,
            last_edited_time=page_payload.last_edited_time,
        )
        unit_of_work.chunks.delete_page_chunks(
            notion_page_db_id=notion_page.id,
        )
        inserted_blocks = unit_of_work.notion_blocks.replace_page_blocks(
            notion_page_db_id=notion_page.id,
            root_blocks=[
                self._to_block_snapshot(block_snapshot)
                for block_snapshot in prepared_snapshot.block_paths
            ],
        )
        if prepared_snapshot.chunk_upserts:
            unit_of_work.chunks.upsert_chunks(
                notion_page_db_id=notion_page.id,
                chunks=prepared_snapshot.chunk_upserts,
            )

        return NotionIndexedPageSnapshot(
            notion_page_db_id=notion_page.id,
            notion_page_id=notion_page.notion_page_id,
            page_title=notion_page.title,
            notion_path=notion_page.notion_path,
            last_edited_time=notion_page.last_edited_time,
            indexed_block_count=len(inserted_blocks),
            indexed_chunk_count=len(prepared_snapshot.chunk_upserts),
            embedding_provider=prepared_snapshot.embedding_metadata[
                "embedding_provider"
            ],
            embedding_model=prepared_snapshot.embedding_metadata["embedding_model"],
            embedding_dimensions=prepared_snapshot.embedding_metadata[
                "embedding_dimensions"
            ],
            embedding_token_input=prepared_snapshot.embedding_metadata[
                "embedding_token_input"
            ],
            embedding_estimated_cost=prepared_snapshot.embedding_metadata[
                "embedding_estimated_cost"
            ],
            embedding_batch_count=prepared_snapshot.embedding_metadata[
                "embedding_batch_count"
            ],
            embedding_retry_count=prepared_snapshot.embedding_metadata[
                "embedding_retry_count"
            ],
        )

    def _persist_indexed_page(
        self,
        *,
        page_payload: _ToolPagePayload,
        block_paths: List[BlockPathSnapshot],
        chunk_upserts: List[NotionChunkUpsert],
        embedding_metadata: Dict[str, Optional[object]],
    ) -> NotionIndexedPageSnapshot:
        prepared_snapshot = PreparedNotionPageSnapshot(
            page_payload=page_payload,
            block_paths=block_paths,
            chunk_upserts=chunk_upserts,
            embedding_metadata=embedding_metadata,
        )
        return self.persist_prepared_page_snapshot(
            prepared_snapshot=prepared_snapshot,
        )

    def _http_status_for_tool_error(self, error_code: str) -> int:
        return TOOL_ERROR_TO_HTTP_STATUS.get(
            error_code, HTTPStatus.INTERNAL_SERVER_ERROR
        )

    def _normalize_failure_reason(self, error_code: str) -> str:
        normalized = error_code.strip().upper()
        if normalized in STANDARD_FAILURE_REASONS:
            return normalized
        return "UNKNOWN_ERROR"

    def _parse_tool_payload(
        self,
        structured_content: Optional[Dict[str, Any]],
    ) -> Tuple[_ToolPagePayload, List[_ToolBlockPayload]]:
        if structured_content is None:
            raise NotionPageIndexError(
                error_code="TOOL_OUTPUT_INVALID",
                message="Tool output structured_content is missing",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )

        try:
            page_payload = _ToolPagePayload(**structured_content["page"])
            block_payloads = [
                _ToolBlockPayload(**block) for block in structured_content["blocks"]
            ]
            return page_payload, block_payloads
        except (KeyError, TypeError, ValidationError) as exc:
            raise NotionPageIndexError(
                error_code="TOOL_OUTPUT_INVALID",
                message=f"Tool output schema is invalid: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            ) from exc

    def _build_chunk_upserts(
        self,
        *,
        page_payload: _ToolPagePayload,
        block_paths: List[BlockPathSnapshot],
    ) -> List[NotionChunkUpsert]:
        chunk_drafts = chunk_notion_page(
            ChunkerPage(
                notion_page_id=page_payload.page_id,
                title=page_payload.title,
                notion_path=page_payload.notion_path,
                blocks=[
                    self._to_chunker_block(block_snapshot)
                    for block_snapshot in block_paths
                ],
            )
        )
        return [
            NotionChunkUpsert(
                chunk_index=draft.chunk_index,
                chunk_text=draft.chunk_text,
                notion_path=draft.notion_path,
                notion_block_ids=self._extract_chunk_block_ids(draft.citation_meta),
                source_kind=draft.source_kind,
            )
            for draft in chunk_drafts
        ]

    async def _embed_chunk_upserts(
        self,
        *,
        page_id: str,
        request_workflow_id: str,
        chunk_upserts: List[NotionChunkUpsert],
    ) -> Tuple[List[NotionChunkUpsert], Dict[str, Optional[object]]]:
        if self._embedding_batch_service is None:
            raise NotionPageIndexError(
                error_code="EMBEDDING_PROVIDER_NOT_CONFIGURED",
                message="Embedding provider is not configured for indexing",
                http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                failure_reason="EMBEDDING_PROVIDER_NOT_CONFIGURED",
            )

        try:
            response = await self._embedding_batch_service.embed(
                [chunk.chunk_text for chunk in chunk_upserts],
                metadata={
                    "workflow_id": request_workflow_id,
                    "operation": "index_page_snapshot",
                },
            )
        except EmbeddingBatchError as exc:
            failure_reason = exc.failure_reason
            raise NotionPageIndexError(
                error_code=failure_reason,
                message="Failed to generate chunk embeddings",
                http_status_code=HTTPStatus.BAD_GATEWAY,
                failure_reason=failure_reason,
            ) from None

        embedded_chunks: List[NotionChunkUpsert] = []
        for chunk, embedding in zip(chunk_upserts, response.embeddings):
            embedded_chunks.append(
                NotionChunkUpsert(
                    chunk_index=chunk.chunk_index,
                    chunk_text=chunk.chunk_text,
                    notion_path=chunk.notion_path,
                    notion_block_ids=list(chunk.notion_block_ids),
                    source_kind=chunk.source_kind,
                    embedding=embedding,
                )
            )

        estimated_cost = None
        if self._cost_tracker is not None:
            estimated_cost = self._cost_tracker.estimate_embedding_cost(
                provider_name=response.provider,
                model=response.model,
                token_input=response.token_input,
            )

        return embedded_chunks, {
            "embedding_provider": response.provider,
            "embedding_model": response.model,
            "embedding_dimensions": EMBEDDING_DIMENSIONS,
            "embedding_token_input": response.token_input,
            "embedding_estimated_cost": estimated_cost,
            "embedding_batch_count": response.batch_count,
            "embedding_retry_count": response.retry_count,
        }

    def _build_success_metadata(
        self,
        *,
        operation: str,
        sync_mode: str,
        page_id: str,
        snapshot: NotionIndexedPageSnapshot,
    ) -> Dict[str, object]:
        metadata: Dict[str, object] = {
            "operation": operation,
            "sync_mode": sync_mode,
            "page_id": page_id,
            "indexed_block_count": snapshot.indexed_block_count,
            "indexed_chunk_count": snapshot.indexed_chunk_count,
        }
        if snapshot.embedding_provider is not None:
            metadata["embedding_provider"] = snapshot.embedding_provider
        if snapshot.embedding_model is not None:
            metadata["embedding_model"] = snapshot.embedding_model
        if snapshot.embedding_dimensions is not None:
            metadata["embedding_dimensions"] = snapshot.embedding_dimensions
        if snapshot.embedding_token_input is not None:
            metadata["embedding_token_input"] = snapshot.embedding_token_input
        if snapshot.embedding_estimated_cost is not None:
            metadata["embedding_estimated_cost"] = snapshot.embedding_estimated_cost
        if snapshot.embedding_batch_count is not None:
            metadata["embedding_batch_count"] = snapshot.embedding_batch_count
        if snapshot.embedding_retry_count is not None:
            metadata["embedding_retry_count"] = snapshot.embedding_retry_count
        return metadata

    def _empty_embedding_metadata(self) -> Dict[str, Optional[object]]:
        return {
            "embedding_provider": None,
            "embedding_model": None,
            "embedding_dimensions": None,
            "embedding_token_input": None,
            "embedding_estimated_cost": None,
            "embedding_batch_count": None,
            "embedding_retry_count": None,
        }

    def _extract_chunk_block_ids(self, citation_meta: Dict[str, Any]) -> List[str]:
        value = citation_meta.get("notion_block_ids")
        if not isinstance(value, list):
            return []
        return [str(block_id).strip() for block_id in value if str(block_id).strip()]

    def _to_block_path_node(self, block: _ToolBlockPayload) -> BlockPathNode:
        return BlockPathNode(
            block_id=block.block_id,
            block_type=block.block_type,
            content_text=block.content_text,
            children=[self._to_block_path_node(child) for child in block.children],
        )

    def _to_block_snapshot(self, block: BlockPathSnapshot) -> NotionBlockSnapshot:
        return NotionBlockSnapshot(
            notion_block_id=block.block_id,
            block_type=block.block_type,
            content_text=block.content_text,
            block_path=block.block_path,
            children=[self._to_block_snapshot(child) for child in block.children],
        )

    def _to_chunker_block(self, block: BlockPathSnapshot) -> ChunkerBlock:
        return ChunkerBlock(
            notion_block_id=block.block_id,
            block_type=block.block_type,
            content_text=block.content_text,
            block_path=block.block_path,
            children=[self._to_chunker_block(child) for child in block.children],
        )
