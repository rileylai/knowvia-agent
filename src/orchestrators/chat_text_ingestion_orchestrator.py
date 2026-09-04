from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.services import WorkflowRunAuditUpdateError, WorkflowRunService

MVP_CHAT_TEXT_MAX_CHARS = 10_000
DEFAULT_CHAT_TEXT_SOURCE_DISPLAY_NAME = "Chat text"


@dataclass
class ChatTextIngestionResult:
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    content_hash: str


class ChatTextIngestionError(Exception):
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


class ChatTextIngestionOrchestrator:
    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        workflow_run_service: WorkflowRunService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._workflow_run_service = workflow_run_service

    async def ingest_chat_text(
        self,
        *,
        chat_text: str,
        source_display_name: str,
        request_workflow_id: str,
    ) -> ChatTextIngestionResult:
        normalized_chat_text = chat_text.strip()
        normalized_display_name = source_display_name.strip()

        if not normalized_chat_text:
            raise ChatTextIngestionError(
                error_code="INVALID_ARGUMENT",
                message="chat_text must not be empty",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        if len(normalized_chat_text) > MVP_CHAT_TEXT_MAX_CHARS:
            raise ChatTextIngestionError(
                error_code="INVALID_ARGUMENT",
                message=(
                    "chat_text exceeds MVP length limit "
                    f"({MVP_CHAT_TEXT_MAX_CHARS} chars)"
                ),
                http_status_code=HTTPStatus.BAD_REQUEST,
            )

        if not normalized_display_name:
            normalized_display_name = DEFAULT_CHAT_TEXT_SOURCE_DISPLAY_NAME

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="ingestion",
            metadata_json=json.dumps(
                {
                    "operation": "ingest_chat_text",
                    "source_type": "chat_text",
                    "source_display_name": normalized_display_name,
                    "chat_text_length": len(normalized_chat_text),
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )

        try:
            content_hash = self._build_content_hash(normalized_chat_text)
            with self._unit_of_work_factory() as unit_of_work:
                source_document = unit_of_work.source_documents.create_source_document(
                    source_type="chat_text",
                    source_display_name=normalized_display_name,
                    raw_text=normalized_chat_text,
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
                        "operation": "ingest_chat_text",
                        "source_document_id": source_document_id,
                        "source_type": "chat_text",
                        "source_display_name": normalized_display_name,
                        "chat_text_length": len(normalized_chat_text),
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
                        "operation": "ingest_chat_text",
                        "source_type": "chat_text",
                        "error_code": "SOURCE_DOCUMENT_CREATE_FAILED",
                    },
                    sort_keys=True,
                ),
            )
            raise ChatTextIngestionError(
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
                message=f"Failed to ingest chat text: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

        return ChatTextIngestionResult(
            workflow_run_id=workflow_run.id,
            status="succeeded",
            source_document_id=source_document_id,
            source_type=persisted_source_type,
            source_display_name=persisted_display_name,
            content_hash=persisted_content_hash,
        )

    def _build_content_hash(self, raw_text: str) -> str:
        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
