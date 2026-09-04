from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.services import WorkflowRunAuditUpdateError, WorkflowRunService

SUPPORTED_SOURCE_TYPES = {"pdf", "url", "youtube", "screenshot", "chat_text"}


@dataclass
class SourceDocumentCreateResult:
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    content_hash: str


class SourceDocumentWorkflowError(Exception):
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


class SourceDocumentOrchestrator:
    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        workflow_run_service: WorkflowRunService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._workflow_run_service = workflow_run_service

    async def create_source_document(
        self,
        *,
        source_type: str,
        source_display_name: str,
        raw_text: str,
        request_workflow_id: str,
    ) -> SourceDocumentCreateResult:
        normalized_source_type = source_type.strip().lower()
        normalized_display_name = source_display_name.strip()
        normalized_raw_text = raw_text.strip()

        if normalized_source_type not in SUPPORTED_SOURCE_TYPES:
            raise SourceDocumentWorkflowError(
                error_code="INVALID_ARGUMENT",
                message=(
                    "source_type must be one of: "
                    "pdf, url, youtube, screenshot, chat_text"
                ),
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        if not normalized_display_name:
            raise SourceDocumentWorkflowError(
                error_code="INVALID_ARGUMENT",
                message="source_display_name must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        if not normalized_raw_text:
            raise SourceDocumentWorkflowError(
                error_code="INVALID_ARGUMENT",
                message="raw_text must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="ingestion",
            metadata_json=json.dumps(
                {
                    "operation": "create_source_document",
                    "source_type": normalized_source_type,
                    "source_display_name": normalized_display_name,
                    "raw_text_length": len(normalized_raw_text),
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )

        try:
            content_hash = self._build_content_hash(normalized_raw_text)
            with self._unit_of_work_factory() as unit_of_work:
                source_document = unit_of_work.source_documents.create_source_document(
                    source_type=normalized_source_type,
                    source_display_name=normalized_display_name,
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
                        "operation": "create_source_document",
                        "source_document_id": source_document_id,
                        "source_type": normalized_source_type,
                        "content_hash": content_hash,
                    },
                    sort_keys=True,
                ),
            )
        except WorkflowRunAuditUpdateError:
            raise
        except Exception as exc:
            self._workflow_run_service.mark_workflow_failed(
                workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                metadata_json=json.dumps(
                    {
                        "operation": "create_source_document",
                        "source_type": normalized_source_type,
                        "error_code": "SOURCE_DOCUMENT_CREATE_FAILED",
                    },
                    sort_keys=True,
                ),
            )
            raise SourceDocumentWorkflowError(
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
                message=f"Failed to create source document: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

        return SourceDocumentCreateResult(
            workflow_run_id=workflow_run.id,
            status="succeeded",
            source_document_id=source_document_id,
            source_type=persisted_source_type,
            source_display_name=persisted_display_name,
            content_hash=persisted_content_hash,
        )

    def _build_content_hash(self, raw_text: str) -> str:
        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
