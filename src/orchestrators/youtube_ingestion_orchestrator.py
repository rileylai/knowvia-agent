from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.services import (
    STANDARD_FAILURE_REASONS,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
)
from src.tools import ToolContext, ToolRegistry

YOUTUBE_TRANSCRIPT_TOOL_NAME = "youtube_transcript_parser"

TOOL_ERROR_TO_HTTP_STATUS: Dict[str, int] = {
    "INVALID_ARGUMENT": HTTPStatus.BAD_REQUEST,
    "YOUTUBE_TRANSCRIPT_NOT_FOUND": HTTPStatus.UNPROCESSABLE_ENTITY,
}


@dataclass
class YouTubeIngestionResult:
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    content_hash: str


class YouTubeIngestionError(Exception):
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


class YouTubeIngestionOrchestrator:
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

    async def ingest_youtube(
        self,
        *,
        url: str,
        request_workflow_id: str,
    ) -> YouTubeIngestionResult:
        normalized_url = url.strip()
        if not normalized_url:
            raise YouTubeIngestionError(
                error_code="INVALID_ARGUMENT",
                message="url must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="ingestion",
            metadata_json=json.dumps(
                {
                    "operation": "ingest_youtube",
                    "source_type": "youtube",
                    "source_url": normalized_url,
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )

        try:
            parsed = await self._parse_youtube_transcript(
                url=normalized_url,
                request_workflow_id=request_workflow_id,
            )
            raw_text = self._extract_raw_text(parsed)
            source_display_name = self._extract_source_display_name(parsed)
            content_hash = self._build_content_hash(raw_text)
            with self._unit_of_work_factory() as unit_of_work:
                source_document = unit_of_work.source_documents.create_source_document(
                    source_type="youtube",
                    source_display_name=source_display_name,
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
                        "operation": "ingest_youtube",
                        "source_document_id": source_document_id,
                        "source_type": "youtube",
                        "source_display_name": source_display_name,
                        "source_url": normalized_url,
                        "video_id": parsed.get("video_id"),
                        "content_hash": content_hash,
                        "char_count": len(raw_text),
                    },
                    sort_keys=True,
                ),
            )
        except WorkflowRunAuditUpdateError:
            raise
        except YouTubeIngestionError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
            )
            raise YouTubeIngestionError(
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
            raise YouTubeIngestionError(
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
                message=f"Failed to ingest YouTube transcript: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

        return YouTubeIngestionResult(
            workflow_run_id=workflow_run.id,
            status="succeeded",
            source_document_id=source_document_id,
            source_type=persisted_source_type,
            source_display_name=persisted_display_name,
            content_hash=persisted_content_hash,
        )

    async def _parse_youtube_transcript(
        self,
        *,
        url: str,
        request_workflow_id: str,
    ) -> Dict[str, Any]:
        tool_result = await self._tool_registry.call_tool(
            YOUTUBE_TRANSCRIPT_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "ingest_youtube",
                    "source_type": "youtube",
                    "source_url": url,
                },
            ),
            arguments={"url": url},
        )

        if tool_result.is_error:
            error_code = "UNKNOWN_ERROR"
            message = "YouTube transcript parser failed"
            if tool_result.error is not None:
                error_code = tool_result.error.code
                message = tool_result.error.message
            raise YouTubeIngestionError(
                error_code=error_code,
                message=message,
                http_status_code=TOOL_ERROR_TO_HTTP_STATUS.get(
                    error_code, HTTPStatus.INTERNAL_SERVER_ERROR
                ),
                failure_reason=self._normalize_failure_reason(error_code),
            )

        structured_content = tool_result.structured_content
        if structured_content is None:
            raise YouTubeIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="YouTube transcript parser structured_content is missing",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        return structured_content

    def _extract_raw_text(self, parser_output: Dict[str, Any]) -> str:
        raw_text = parser_output.get("raw_text")
        if not isinstance(raw_text, str):
            raise YouTubeIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="YouTube transcript parser raw_text is invalid",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        normalized_raw_text = raw_text.strip()
        if not normalized_raw_text:
            raise YouTubeIngestionError(
                error_code="YOUTUBE_TRANSCRIPT_NOT_FOUND",
                message="No transcript found for this YouTube video",
                http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                failure_reason="YOUTUBE_TRANSCRIPT_NOT_FOUND",
            )
        return normalized_raw_text

    def _extract_source_display_name(self, parser_output: Dict[str, Any]) -> str:
        source_display_name = parser_output.get("source_display_name")
        if isinstance(source_display_name, str):
            normalized_name = source_display_name.strip()
            if normalized_name:
                return normalized_name

        video_id = parser_output.get("video_id")
        if isinstance(video_id, str):
            normalized_video_id = video_id.strip()
            if normalized_video_id:
                return f"YouTube transcript ({normalized_video_id})"

        raise YouTubeIngestionError(
            error_code="TOOL_OUTPUT_INVALID",
            message="YouTube transcript parser source_display_name is invalid",
            http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            failure_reason="UNKNOWN_ERROR",
        )

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
                    "operation": "ingest_youtube",
                    "source_type": "youtube",
                    "error_code": error_code,
                },
                sort_keys=True,
            ),
        )
