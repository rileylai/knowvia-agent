from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, List, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.services import (
    MAX_PDF_BYTES,
    STANDARD_FAILURE_REASONS,
    CostTracker,
    EmbeddingBatchError,
    EmbeddingBatchService,
    UploadValidationError,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
    upload_error_http_status,
    validate_extracted_text,
    validate_file_bytes,
    validate_pdf_metadata,
    validate_pdf_page_count,
)
from src.rag import chunk_text_document
from src.repositories import KnowledgeChunkUpsert
from src.tools import ToolContext, ToolRegistry

PDF_PARSER_TOOL_NAME = "pdf_parser"

TOOL_ERROR_TO_HTTP_STATUS: Dict[str, int] = {
    "INVALID_ARGUMENT": HTTPStatus.BAD_REQUEST,
    "INVALID_UPLOAD_TYPE": HTTPStatus.BAD_REQUEST,
    "INVALID_UPLOAD_MIME": HTTPStatus.BAD_REQUEST,
    "EMPTY_UPLOAD": HTTPStatus.BAD_REQUEST,
    "UPLOAD_LIMIT_EXCEEDED": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "UPLOAD_TOO_LARGE": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "PDF_PARSE_FAILED": HTTPStatus.UNPROCESSABLE_ENTITY,
    "PDF_PAGE_LIMIT_EXCEEDED": HTTPStatus.UNPROCESSABLE_ENTITY,
    "EXTRACTED_TEXT_LIMIT_EXCEEDED": HTTPStatus.UNPROCESSABLE_ENTITY,
    "EMBEDDING_PROVIDER_NOT_CONFIGURED": HTTPStatus.SERVICE_UNAVAILABLE,
    "EMBEDDING_PROVIDER_ERROR": HTTPStatus.BAD_GATEWAY,
    "VECTOR_DIMENSION_MISMATCH": HTTPStatus.BAD_GATEWAY,
}


@dataclass
class DocumentIngestionResult:
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    content_hash: str
    index_status: str = "parsed"
    indexed_chunk_count: int = 0
    embedded_chunk_count: int = 0


class DocumentIngestionError(Exception):
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


class DocumentIngestionOrchestrator:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        unit_of_work_factory: UnitOfWorkFactory,
        workflow_run_service: WorkflowRunService,
        embedding_batch_service: Optional[EmbeddingBatchService] = None,
        cost_tracker: Optional[CostTracker] = None,
        index_knowledge: bool = True,
    ) -> None:
        self._tool_registry = tool_registry
        self._unit_of_work_factory = unit_of_work_factory
        self._workflow_run_service = workflow_run_service
        self._embedding_batch_service = embedding_batch_service
        self._cost_tracker = cost_tracker
        self._index_knowledge = index_knowledge

    async def ingest_document(
        self,
        *,
        file_name: str,
        file_bytes: bytes,
        mime_type: Optional[str] = None,
        request_workflow_id: str,
    ) -> DocumentIngestionResult:
        normalized_file_name = file_name.strip()
        try:
            if not normalized_file_name:
                raise UploadValidationError(
                    error_code="INVALID_ARGUMENT",
                    message="file_name must not be empty",
                    failure_reason="INVALID_ARGUMENT",
                )
            validate_pdf_metadata(
                file_name=normalized_file_name,
                mime_type=mime_type,
            )
            validate_file_bytes(
                file_bytes=file_bytes,
                maximum_bytes=MAX_PDF_BYTES,
                label="Uploaded document",
            )
        except UploadValidationError as exc:
            raise DocumentIngestionError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=upload_error_http_status(exc.error_code),
                failure_reason=exc.failure_reason,
            ) from exc

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="ingestion",
            metadata_json=json.dumps(
                {
                    "operation": "ingest_document",
                    "source_type": "pdf",
                    "source_display_name": normalized_file_name,
                    "file_size_bytes": len(file_bytes),
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )

        source_document_id: Optional[int] = None
        indexed_chunk_count = 0
        embedded_chunk_count = 0
        embedding_metadata: Dict[str, Any] = {}
        try:
            parsed = await self._parse_pdf(
                file_name=normalized_file_name,
                file_bytes=file_bytes,
                request_workflow_id=request_workflow_id,
            )
            page_count = parsed.get("page_count")
            if isinstance(page_count, int):
                try:
                    validate_pdf_page_count(page_count)
                except UploadValidationError as exc:
                    raise DocumentIngestionError(
                        error_code=exc.error_code,
                        message=exc.message,
                        http_status_code=upload_error_http_status(exc.error_code),
                        failure_reason=exc.failure_reason,
                    ) from exc
            raw_text = self._extract_raw_text(parsed)
            content_hash = self._build_content_hash(raw_text)
            with self._unit_of_work_factory() as unit_of_work:
                source_document = unit_of_work.source_documents.create_source_document(
                    source_type="pdf",
                    source_display_name=normalized_file_name,
                    raw_text=raw_text,
                    content_hash=content_hash,
                    owner_scope="local",
                    status="indexing" if self._index_knowledge else "parsed",
                )
                source_document_id = int(source_document.id)
                persisted_source_type = source_document.source_type
                persisted_display_name = source_document.source_display_name
                persisted_content_hash = source_document.content_hash

            if not self._index_knowledge:
                self._workflow_run_service.mark_workflow_succeeded(
                    workflow_run.id,
                    metadata_json=json.dumps(
                        {
                            "operation": "ingest_document",
                            "source_document_id": source_document_id,
                            "source_type": "pdf",
                            "source_display_name": normalized_file_name,
                            "content_hash": content_hash,
                            "page_count": parsed.get("page_count"),
                            "char_count": len(raw_text),
                            "index_status": "parsed",
                        },
                        sort_keys=True,
                    ),
                )
                return DocumentIngestionResult(
                    workflow_run_id=workflow_run.id,
                    status="succeeded",
                    source_document_id=source_document_id,
                    source_type=persisted_source_type,
                    source_display_name=persisted_display_name,
                    content_hash=persisted_content_hash,
                    index_status="parsed",
                )

            chunk_drafts = chunk_text_document(
                raw_text,
                source_kind="pdf",
                source_display_name=normalized_file_name,
                pages=self._extract_pages(parsed),
            )
            if not chunk_drafts:
                raise DocumentIngestionError(
                    error_code="PDF_PARSE_FAILED",
                    message="No searchable text chunks were produced from PDF",
                    http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    failure_reason="PDF_PARSE_FAILED",
                )
            if self._embedding_batch_service is None:
                raise DocumentIngestionError(
                    error_code="EMBEDDING_PROVIDER_NOT_CONFIGURED",
                    message="Embedding provider is not configured for PDF indexing",
                    http_status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                    failure_reason="EMBEDDING_PROVIDER_NOT_CONFIGURED",
                )

            try:
                embedding_result = await self._embedding_batch_service.embed(
                    [draft.chunk_text for draft in chunk_drafts],
                    metadata={
                        "workflow_id": request_workflow_id,
                        "operation": "index_pdf",
                        "source_kind": "pdf",
                    },
                )
            except EmbeddingBatchError as exc:
                failure_reason = self._normalize_failure_reason(exc.failure_reason)
                raise DocumentIngestionError(
                    error_code=failure_reason,
                    message="Failed to generate PDF chunk embeddings",
                    http_status_code=TOOL_ERROR_TO_HTTP_STATUS.get(
                        failure_reason, HTTPStatus.BAD_GATEWAY
                    ),
                    failure_reason=failure_reason,
                ) from None

            if len(embedding_result.embeddings) != len(chunk_drafts):
                raise DocumentIngestionError(
                    error_code="VECTOR_DIMENSION_MISMATCH",
                    message="Embedding response does not match PDF chunk count",
                    http_status_code=HTTPStatus.BAD_GATEWAY,
                    failure_reason="VECTOR_DIMENSION_MISMATCH",
                )
            indexed_chunks = [
                KnowledgeChunkUpsert(
                    source_document_id=source_document_id,
                    source_kind=draft.source_kind,
                    chunk_index=draft.chunk_index,
                    chunk_text=draft.chunk_text,
                    source_display_name=normalized_file_name,
                    locator=draft.locator,
                    embedding=embedding,
                    embedding_model=embedding_result.model,
                    embedding_dimensions=embedding_result.dimensions,
                    citation_metadata=draft.citation_meta,
                    owner_scope="local",
                    eligibility_status="eligible",
                )
                for draft, embedding in zip(
                    chunk_drafts, embedding_result.embeddings
                )
            ]
            with self._unit_of_work_factory() as unit_of_work:
                persisted_chunks = unit_of_work.chunks.upsert_source_document_chunks(
                    source_document_id=source_document_id,
                    chunks=indexed_chunks,
                )
                unit_of_work.source_documents.update_status(
                    source_document_id=source_document_id,
                    status="indexed",
                )
            indexed_chunk_count = len(persisted_chunks)
            embedded_chunk_count = len(embedding_result.embeddings)
            embedding_metadata = {
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

            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    {
                        "operation": "ingest_document",
                        "source_document_id": source_document_id,
                        "source_type": "pdf",
                        "source_display_name": normalized_file_name,
                        "content_hash": content_hash,
                        "page_count": parsed.get("page_count"),
                        "char_count": len(raw_text),
                        "index_status": "indexed",
                        "indexed_chunk_count": indexed_chunk_count,
                        "embedded_chunk_count": embedded_chunk_count,
                        **embedding_metadata,
                    },
                    sort_keys=True,
                ),
            )
        except WorkflowRunAuditUpdateError:
            raise
        except DocumentIngestionError as exc:
            self._mark_source_document_failed(source_document_id)
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
            )
            raise DocumentIngestionError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=workflow_run.id,
            ) from exc
        except Exception as exc:
            self._mark_source_document_failed(source_document_id)
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
            )
            raise DocumentIngestionError(
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
                message=f"Failed to ingest PDF document: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

        return DocumentIngestionResult(
            workflow_run_id=workflow_run.id,
            status="succeeded",
            source_document_id=source_document_id,
            source_type=persisted_source_type,
            source_display_name=persisted_display_name,
            content_hash=persisted_content_hash,
            index_status="indexed",
            indexed_chunk_count=indexed_chunk_count,
            embedded_chunk_count=embedded_chunk_count,
        )

    async def _parse_pdf(
        self,
        *,
        file_name: str,
        file_bytes: bytes,
        request_workflow_id: str,
    ) -> Dict[str, Any]:
        tool_result = await self._tool_registry.call_tool(
            PDF_PARSER_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "ingest_document",
                    "source_type": "pdf",
                    "source_display_name": file_name,
                },
            ),
            arguments={
                "file_name": file_name,
                "file_bytes_base64": base64.b64encode(file_bytes).decode("ascii"),
            },
        )

        if tool_result.is_error:
            error_code = "UNKNOWN_ERROR"
            message = "PDF parser failed"
            if tool_result.error is not None:
                error_code = tool_result.error.code
                message = tool_result.error.message
            raise DocumentIngestionError(
                error_code=error_code,
                message=message,
                http_status_code=TOOL_ERROR_TO_HTTP_STATUS.get(
                    error_code, HTTPStatus.INTERNAL_SERVER_ERROR
                ),
                failure_reason=self._normalize_failure_reason(error_code),
            )

        structured_content = tool_result.structured_content
        if structured_content is None:
            raise DocumentIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="PDF parser structured_content is missing",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        return structured_content

    def _extract_raw_text(self, parser_output: Dict[str, Any]) -> str:
        raw_text = parser_output.get("raw_text")
        if not isinstance(raw_text, str):
            raise DocumentIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="PDF parser raw_text is invalid",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        normalized_raw_text = raw_text.strip()
        if not normalized_raw_text:
            raise DocumentIngestionError(
                error_code="PDF_PARSE_FAILED",
                message="No extractable text found in PDF",
                http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                failure_reason="PDF_PARSE_FAILED",
            )
        try:
            validate_extracted_text(normalized_raw_text)
        except UploadValidationError as exc:
            raise DocumentIngestionError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=upload_error_http_status(exc.error_code),
                failure_reason=exc.failure_reason,
            ) from exc
        return normalized_raw_text

    def _extract_pages(self, parser_output: Dict[str, Any]) -> List[str]:
        pages = parser_output.get("pages")
        if not isinstance(pages, list):
            return []
        return [page for page in pages if isinstance(page, str)]

    def _mark_source_document_failed(self, source_document_id: Optional[int]) -> None:
        if source_document_id is None:
            return
        try:
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.source_documents.update_status(
                    source_document_id=source_document_id,
                    status="failed",
                )
        except Exception:
            return

    def _build_content_hash(self, raw_text: str) -> str:
        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    def _normalize_failure_reason(self, error_code: str) -> str:
        normalized = error_code.strip().upper()
        if normalized in STANDARD_FAILURE_REASONS:
            return normalized
        return "UNKNOWN_ERROR"

    def _mark_failed_workflow(
        self,
        *,
        workflow_run_id: int,
        failure_reason: str,
        error_code: str,
    ) -> None:
        self._workflow_run_service.mark_workflow_failed(
            workflow_run_id,
            failure_reason=self._normalize_failure_reason(failure_reason),
            metadata_json=json.dumps(
                {
                    "operation": "ingest_document",
                    "source_type": "pdf",
                    "error_code": error_code,
                },
                sort_keys=True,
            ),
        )
