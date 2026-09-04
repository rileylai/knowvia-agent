from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.services import (
    MAX_PDF_BYTES,
    STANDARD_FAILURE_REASONS,
    UploadValidationError,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
    upload_error_http_status,
    validate_extracted_text,
    validate_file_bytes,
    validate_pdf_metadata,
    validate_pdf_page_count,
)
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
}


@dataclass
class DocumentIngestionResult:
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    content_hash: str


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
    ) -> None:
        self._tool_registry = tool_registry
        self._unit_of_work_factory = unit_of_work_factory
        self._workflow_run_service = workflow_run_service

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
                )
                source_document_id = int(source_document.id)
                persisted_source_type = source_document.source_type
                persisted_display_name = source_document.source_display_name
                persisted_content_hash = source_document.content_hash

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
                    },
                    sort_keys=True,
                ),
            )
        except WorkflowRunAuditUpdateError:
            raise
        except DocumentIngestionError as exc:
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
