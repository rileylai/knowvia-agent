from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.services import (
    CostTracker,
    EmbeddingBatchService,
    KnowledgeIndexingError,
    KnowledgeIndexingService,
    STANDARD_FAILURE_REASONS,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
)
from src.tools import ToolContext, ToolRegistry, URLArticleParserClientError, canonicalize_url

URL_ARTICLE_PARSER_TOOL_NAME = "url_article_parser"

TOOL_ERROR_TO_HTTP_STATUS: Dict[str, int] = {
    "INVALID_ARGUMENT": HTTPStatus.BAD_REQUEST,
    "URL_SSRF_BLOCKED": HTTPStatus.BAD_REQUEST,
    "URL_DNS_RESOLUTION_FAILED": HTTPStatus.UNPROCESSABLE_ENTITY,
    "URL_REDIRECT_LIMIT_EXCEEDED": HTTPStatus.UNPROCESSABLE_ENTITY,
    "URL_RESPONSE_TYPE_UNSUPPORTED": HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
    "URL_RESPONSE_TOO_LARGE": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "URL_FETCH_FAILED": HTTPStatus.UNPROCESSABLE_ENTITY,
}


@dataclass
class URLIngestionResult:
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    content_hash: str
    requested_url: str
    final_url: str
    index_status: str = "indexed"
    indexed_chunk_count: int = 0
    embedded_chunk_count: int = 0


class URLIngestionError(Exception):
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


class URLIngestionOrchestrator:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        unit_of_work_factory: UnitOfWorkFactory,
        workflow_run_service: WorkflowRunService,
        embedding_batch_service: Optional[EmbeddingBatchService] = None,
        cost_tracker: Optional[CostTracker] = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._unit_of_work_factory = unit_of_work_factory
        self._workflow_run_service = workflow_run_service
        self._knowledge_indexing_service = KnowledgeIndexingService(
            unit_of_work_factory=unit_of_work_factory,
            embedding_batch_service=embedding_batch_service,
            cost_tracker=cost_tracker,
        )

    async def ingest_url(
        self,
        *,
        url: str,
        request_workflow_id: str,
    ) -> URLIngestionResult:
        normalized_url = url.strip()
        if not normalized_url:
            raise URLIngestionError(
                error_code="INVALID_ARGUMENT",
                message="url must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="ingestion",
            metadata_json=json.dumps(
                {
                    "operation": "ingest_url",
                    "source_type": "url",
                    "url_length": len(normalized_url),
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )

        source_document_id: Optional[int] = None
        try:
            parsed = await self._parse_url_article(
                url=normalized_url,
                request_workflow_id=request_workflow_id,
            )
            raw_text = self._extract_raw_text(parsed)
            content_hash = self._build_content_hash(raw_text)
            requested_url = self._canonicalize_url(normalized_url)
            final_url = self._extract_final_url(
                parser_output=parsed,
                fallback_url=requested_url,
            )
            source_display_name = self._extract_source_display_name(
                parser_output=parsed,
                fallback_url=final_url,
            )

            with self._unit_of_work_factory() as unit_of_work:
                existing_source = unit_of_work.source_documents.find_indexed_url_source(
                    final_url=final_url,
                    content_hash=content_hash,
                    owner_scope="local",
                )
            if existing_source is not None:
                self._workflow_run_service.mark_workflow_succeeded(
                    workflow_run.id,
                    metadata_json=json.dumps(
                        {
                            "operation": "ingest_url_duplicate_guard",
                            "source_document_id": existing_source.id,
                            "source_type": "url",
                            "content_hash": content_hash,
                            "index_status": "indexed",
                            "result_status": "already_indexed",
                            "indexed_chunk_count": existing_source.chunk_count,
                            "embedded_chunk_count": existing_source.chunk_count,
                        },
                        sort_keys=True,
                    ),
                )
                return URLIngestionResult(
                    workflow_run_id=workflow_run.id,
                    status="already_indexed",
                    source_document_id=existing_source.id,
                    source_type=existing_source.source_kind,
                    source_display_name=existing_source.display_name,
                    content_hash=existing_source.content_hash,
                    requested_url=requested_url,
                    final_url=existing_source.source_url or final_url,
                    index_status=existing_source.status,
                    indexed_chunk_count=existing_source.chunk_count,
                    embedded_chunk_count=existing_source.chunk_count,
                )

            with self._unit_of_work_factory() as unit_of_work:
                source_document = unit_of_work.source_documents.create_source_document(
                    source_type="url",
                    source_display_name=source_display_name,
                    raw_text=raw_text,
                    content_hash=content_hash,
                    requested_url=requested_url,
                    final_url=final_url,
                    owner_scope="local",
                    status="indexing",
                )
                source_document_id = int(source_document.id)
                persisted_source_type = source_document.source_type
                persisted_display_name = source_document.source_display_name
                persisted_content_hash = source_document.content_hash

            try:
                indexing_result = await self._knowledge_indexing_service.index_source_document(
                    source_document_id=source_document_id,
                    source_kind="url",
                    source_display_name=source_display_name,
                    raw_text=raw_text,
                    request_workflow_id=request_workflow_id,
                    citation_metadata={
                        "requested_url": requested_url,
                        "final_url": final_url,
                    },
                    owner_scope="local",
                    empty_chunks_error_code="URL_FETCH_FAILED",
                )
            except KnowledgeIndexingError as exc:
                raise URLIngestionError(
                    error_code=exc.error_code,
                    message=exc.message,
                    http_status_code=exc.http_status_code,
                    failure_reason=exc.failure_reason,
                ) from None

            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    {
                        "operation": "ingest_url",
                        "source_document_id": source_document_id,
                        "source_type": "url",
                        "index_status": "indexed",
                        "content_hash": content_hash,
                        "char_count": len(raw_text),
                        "indexed_chunk_count": indexing_result.indexed_chunk_count,
                        "embedded_chunk_count": indexing_result.embedded_chunk_count,
                        **indexing_result.embedding_metadata,
                    },
                    sort_keys=True,
                ),
            )
        except WorkflowRunAuditUpdateError:
            raise
        except URLIngestionError as exc:
            self._mark_source_document_failed(source_document_id)
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
            )
            raise URLIngestionError(
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
            raise URLIngestionError(
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
                message=f"Failed to ingest URL article: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

        return URLIngestionResult(
            workflow_run_id=workflow_run.id,
            status="succeeded",
            source_document_id=source_document_id,
            source_type=persisted_source_type,
            source_display_name=persisted_display_name,
            content_hash=persisted_content_hash,
            requested_url=requested_url,
            final_url=final_url,
            index_status="indexed",
            indexed_chunk_count=indexing_result.indexed_chunk_count,
            embedded_chunk_count=indexing_result.embedded_chunk_count,
        )

    def _extract_final_url(
        self,
        *,
        parser_output: Dict[str, Any],
        fallback_url: str,
    ) -> str:
        final_url = parser_output.get("final_url") or parser_output.get("url")
        if final_url is None:
            return fallback_url
        if not isinstance(final_url, str) or not final_url.strip():
            raise URLIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="URL parser final_url is invalid",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        return self._canonicalize_url(final_url)

    def _extract_source_display_name(
        self,
        *,
        parser_output: Dict[str, Any],
        fallback_url: str,
    ) -> str:
        title = parser_output.get("title")
        if not isinstance(title, str):
            return fallback_url
        normalized_title = " ".join(title.split()).strip()
        return normalized_title[:512] or fallback_url

    def _canonicalize_url(self, url: str) -> str:
        try:
            return canonicalize_url(url)
        except URLArticleParserClientError as exc:
            raise URLIngestionError(
                error_code=exc.code,
                message=str(exc),
                http_status_code=TOOL_ERROR_TO_HTTP_STATUS.get(
                    exc.code,
                    HTTPStatus.BAD_REQUEST,
                ),
                failure_reason=self._normalize_failure_reason(exc.code),
            ) from exc

    async def _parse_url_article(
        self,
        *,
        url: str,
        request_workflow_id: str,
    ) -> Dict[str, Any]:
        tool_result = await self._tool_registry.call_tool(
            URL_ARTICLE_PARSER_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "ingest_url",
                    "source_type": "url",
                },
            ),
            arguments={"url": url},
        )

        if tool_result.is_error:
            error_code = "UNKNOWN_ERROR"
            message = "URL parser failed"
            if tool_result.error is not None:
                error_code = tool_result.error.code
                message = tool_result.error.message
            raise URLIngestionError(
                error_code=error_code,
                message=message,
                http_status_code=TOOL_ERROR_TO_HTTP_STATUS.get(
                    error_code, HTTPStatus.INTERNAL_SERVER_ERROR
                ),
                failure_reason=self._normalize_failure_reason(error_code),
            )

        structured_content = tool_result.structured_content
        if structured_content is None:
            raise URLIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="URL parser structured_content is missing",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        return structured_content

    def _extract_raw_text(self, parser_output: Dict[str, Any]) -> str:
        raw_text = parser_output.get("raw_text")
        if not isinstance(raw_text, str):
            raise URLIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="URL parser raw_text is invalid",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        normalized_raw_text = raw_text.strip()
        if not normalized_raw_text:
            raise URLIngestionError(
                error_code="URL_FETCH_FAILED",
                message="No extractable text found in URL article",
                http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                failure_reason="URL_FETCH_FAILED",
            )
        return normalized_raw_text

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
                    "operation": "ingest_url",
                    "source_type": "url",
                    "error_code": error_code,
                },
                sort_keys=True,
            ),
        )
