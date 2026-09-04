from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from http import HTTPStatus
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

from src.orchestrators.document_ingestion_orchestrator import (
    DocumentIngestionError,
    DocumentIngestionOrchestrator,
)
from src.orchestrators.image_ocr_ingestion_orchestrator import (
    ImageOCRIngestionError,
    ImageOCRIngestionOrchestrator,
    ImageUploadInput,
)
from src.orchestrators.supplement_propose_orchestrator import (
    DEFAULT_SUPPLEMENT_MODEL,
    DEFAULT_SUPPLEMENT_PROVIDER_NAME,
    SupplementProposeError,
    SupplementProposeOrchestrator,
)
from src.orchestrators.supplement_query_orchestrator import (
    SupplementQueryError,
    SupplementQueryOrchestrator,
    SupplementReviewItemResult,
)
from src.services import (
    InMemoryTelegramSessionStore,
    STANDARD_FAILURE_REASONS,
    TelegramSessionStore,
    TelegramUploadAttachment,
    TelegramUploadSession,
)
from src.services.latency_evidence import LatencyEvidence, elapsed_ms
from src.services.telegram_proposal_preview import (
    format_bounded_note_lines,
    truncate_telegram_preview,
)
from src.tools import ToolContext, ToolRegistry

TELEGRAM_BOT_TOOL_NAME = "telegram_bot"

TOOL_ERROR_TO_HTTP_STATUS: Dict[str, int] = {
    "INVALID_ARGUMENT": HTTPStatus.BAD_REQUEST,
    "TELEGRAM_NOT_CONFIGURED": HTTPStatus.SERVICE_UNAVAILABLE,
    "TELEGRAM_FILE_DOWNLOAD_FAILED": HTTPStatus.BAD_GATEWAY,
}


@dataclass
class TelegramDocumentAttachment:
    file_id: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


@dataclass
class TelegramPhotoAttachment:
    file_id: str
    file_unique_id: Optional[str] = None
    file_size: Optional[int] = None


@dataclass
class TelegramIngestionCommandResult:
    reply_text: str
    source_document_id: Optional[int]
    change_request_id: Optional[int]
    source_type: Optional[str]
    target_notion_page_id: Optional[str]
    target_notion_path: Optional[str] = None
    session_id: Optional[str] = None
    already_processed: bool = False
    latency_metadata: Dict[str, float] = field(default_factory=dict)


class TelegramIngestionError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
        workflow_run_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason
        self.workflow_run_id = workflow_run_id
        self.metadata = dict(metadata or {})


class TelegramIngestionOrchestrator:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        document_ingestion_orchestrator: DocumentIngestionOrchestrator,
        image_ocr_ingestion_orchestrator: ImageOCRIngestionOrchestrator,
        supplement_propose_orchestrator: SupplementProposeOrchestrator,
        supplement_query_orchestrator: Optional[SupplementQueryOrchestrator] = None,
        session_store: Optional[TelegramSessionStore] = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._document_ingestion_orchestrator = document_ingestion_orchestrator
        self._image_ocr_ingestion_orchestrator = image_ocr_ingestion_orchestrator
        self._supplement_propose_orchestrator = supplement_propose_orchestrator
        self._supplement_query_orchestrator = supplement_query_orchestrator
        self._session_store = session_store or InMemoryTelegramSessionStore()

    def store_upload(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        media_group_id: Optional[str],
        message_id: Optional[int],
        document: Optional[TelegramDocumentAttachment],
        photos: List[TelegramPhotoAttachment],
        command_text: Optional[str],
    ) -> TelegramUploadSession:
        attachments: List[TelegramUploadAttachment] = []
        if document is not None:
            attachments.append(
                TelegramUploadAttachment(
                    kind="pdf",
                    file_id=document.file_id,
                    message_id=message_id,
                    file_name=document.file_name,
                    mime_type=document.mime_type,
                    file_size=document.file_size,
                )
            )
        attachments.extend(
            TelegramUploadAttachment(
                kind="photo",
                file_id=photo.file_id,
                message_id=message_id,
                file_unique_id=photo.file_unique_id,
                file_size=photo.file_size,
            )
            for photo in self._deduplicate_photos(photos)
        )
        if not attachments:
            raise TelegramIngestionError(
                error_code="UPLOAD_MEDIA_MISSING",
                message="No PDF or image was found. Please upload the file again.",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            )
        return self._session_store.upsert_upload(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            media_group_id=media_group_id,
            attachments=attachments,
            command_text=command_text,
        )

    def get_latest_upload(self, *, chat_id: str, user_id: str) -> Optional[TelegramUploadSession]:
        return self._session_store.find_latest_upload(chat_id=chat_id, user_id=user_id)

    def mark_upload_awaiting_target(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramUploadSession]:
        return self._session_store.mark_awaiting_target(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    def claim_upload_settle(self, *, session_id: str, chat_id: str, user_id: str) -> bool:
        return self._session_store.claim_settle(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    def claim_upload_settled(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        settle_version: Optional[int] = None,
    ) -> bool:
        return self._session_store.claim_settled(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            settle_version=settle_version,
        )

    def claim_upload_picker(self, *, session_id: str, chat_id: str, user_id: str) -> bool:
        return self._session_store.claim_picker(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    def claim_upload_receipt(self, *, session_id: str, chat_id: str, user_id: str) -> bool:
        return self._session_store.claim_receipt(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    def claim_upload_preview(self, *, session_id: str, chat_id: str, user_id: str) -> bool:
        return self._session_store.claim_preview(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    def complete_upload_preview(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        success: bool,
        failure_reason: Optional[str] = None,
    ) -> Optional[TelegramUploadSession]:
        return self._session_store.complete_preview(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            success=success,
            failure_reason=failure_reason,
        )

    def fail_upload(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        failure_reason: str,
    ) -> None:
        self._session_store.fail_upload(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            failure_reason=failure_reason,
        )

    def get_upload(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> Optional[TelegramUploadSession]:
        return self._session_store.get_upload(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )

    def create_callback(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        action: str,
        callback_kind: Optional[str] = None,
        change_request_id: Optional[int] = None,
        target_notion_page_id: Optional[str] = None,
        target_notion_path: Optional[str] = None,
        picker_mode: str = "upload",
        navigation_page_id: Optional[str] = None,
        navigation_page_number: int = 1,
    ) -> str:
        return self._session_store.create_callback(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            action=action,
            callback_kind=callback_kind,
            change_request_id=change_request_id,
            target_notion_page_id=target_notion_page_id,
            target_notion_path=target_notion_path,
            picker_mode=picker_mode,
            navigation_page_id=navigation_page_id,
            navigation_page_number=navigation_page_number,
        )

    def resolve_callback(self, *, token: str, chat_id: str, user_id: str):
        return self._session_store.resolve_callback(
            token=token,
            chat_id=chat_id,
            user_id=user_id,
        )

    async def handle_target_selection(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        target_notion_page_id: str,
        target_notion_path: str,
        request_workflow_id: str,
    ) -> TelegramIngestionCommandResult:
        claim_status, session = self._session_store.claim_target(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            target_notion_page_id=target_notion_page_id,
            target_notion_path=target_notion_path,
        )
        if session is None:
            raise TelegramIngestionError(
                error_code="UPLOAD_SESSION_EXPIRED",
                message="This upload session expired. Please upload the file again.",
                http_status_code=HTTPStatus.GONE,
                failure_reason="UPLOAD_SESSION_EXPIRED",
            )
        if claim_status == "in_progress":
            return TelegramIngestionCommandResult(
                reply_text="This upload is already being processed. Please wait for its preview.",
                source_document_id=session.source_document_id,
                change_request_id=session.change_request_id,
                source_type=session.source_type,
                target_notion_page_id=session.target_notion_page_id,
                target_notion_path=session.target_notion_path,
                session_id=session.session_id,
                already_processed=True,
            )
        if claim_status == "already":
            preview = self._build_proposal_preview(
                change_request_id=int(session.change_request_id or 0),
                target_notion_page_id=session.target_notion_page_id,
                target_notion_path=session.target_notion_path,
                source_type=session.source_type or "unknown",
                source_document_id=int(session.source_document_id or 0),
                source_count=len(session.attachments),
            )
            return TelegramIngestionCommandResult(
                reply_text=preview or "Proposal is already ready for review.",
                source_document_id=session.source_document_id,
                change_request_id=session.change_request_id,
                source_type=session.source_type,
                target_notion_page_id=session.target_notion_page_id,
                target_notion_path=session.target_notion_path,
                session_id=session.session_id,
                already_processed=True,
            )
        if claim_status == "retry":
            return await self._retry_claimed_source_proposal(
                session=session,
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                request_workflow_id=request_workflow_id,
            )
        if claim_status != "new":
            raise TelegramIngestionError(
                error_code="UPLOAD_SESSION_INVALID",
                message="This upload session is no longer selectable. Please upload the file again.",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="UPLOAD_SESSION_INVALID",
            )

        document: Optional[TelegramDocumentAttachment] = None
        photos: List[TelegramPhotoAttachment] = []
        for attachment in session.attachments:
            if attachment.kind == "pdf":
                document = TelegramDocumentAttachment(
                    file_id=attachment.file_id,
                    file_name=attachment.file_name,
                    mime_type=attachment.mime_type,
                    file_size=attachment.file_size,
                )
            else:
                photos.append(
                    TelegramPhotoAttachment(
                        file_id=attachment.file_id,
                        file_unique_id=attachment.file_unique_id,
                        file_size=attachment.file_size,
                    )
                )

        try:
            result = await self.handle_ingest_command(
                chat_id=chat_id,
                document=document,
                photos=photos,
                request_workflow_id=request_workflow_id,
                target_notion_page_id=target_notion_page_id,
                on_source_created=lambda source_document_id, source_type: self._session_store.record_source_document(
                    session_id=session_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    source_document_id=source_document_id,
                    source_type=source_type,
                    target_notion_page_id=target_notion_page_id,
                    target_notion_path=target_notion_path,
                ),
            )
        except TelegramIngestionError as exc:
            self._session_store.fail_upload(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                failure_reason=exc.failure_reason,
            )
            raise
        resolved_target_notion_path = result.target_notion_path or target_notion_path
        self._session_store.record_proposal(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            source_document_id=int(result.source_document_id or 0),
            change_request_id=int(result.change_request_id or 0),
            source_type=str(result.source_type or "unknown"),
            target_notion_page_id=target_notion_page_id,
            target_notion_path=resolved_target_notion_path,
        )
        preview = self._build_proposal_preview(
            change_request_id=int(result.change_request_id or 0),
            target_notion_page_id=target_notion_page_id,
            target_notion_path=resolved_target_notion_path,
            source_type=str(result.source_type or "unknown"),
            source_document_id=int(result.source_document_id or 0),
            source_count=len(session.attachments),
        )
        return TelegramIngestionCommandResult(
            reply_text=preview or result.reply_text,
            source_document_id=result.source_document_id,
            change_request_id=result.change_request_id,
            source_type=result.source_type,
            target_notion_page_id=target_notion_page_id,
            target_notion_path=resolved_target_notion_path,
            session_id=session_id,
            latency_metadata=result.latency_metadata,
        )

    async def retry_existing_proposal(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        request_workflow_id: str,
    ) -> TelegramIngestionCommandResult:
        """Retry proposal generation from an existing persisted source only."""

        claim_status, session = self._session_store.claim_retry(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        if session is None:
            raise TelegramIngestionError(
                error_code="UPLOAD_SESSION_EXPIRED",
                message="No failed proposal session is available. Use /ingest to start a new upload.",
                http_status_code=HTTPStatus.GONE,
                failure_reason="UPLOAD_SESSION_EXPIRED",
            )
        if claim_status == "in_progress":
            return TelegramIngestionCommandResult(
                reply_text="This proposal is already being retried. Please wait for its preview.",
                source_document_id=session.source_document_id,
                change_request_id=session.change_request_id,
                source_type=session.source_type,
                target_notion_page_id=session.target_notion_page_id,
                target_notion_path=session.target_notion_path,
                session_id=session.session_id,
                already_processed=True,
            )
        if claim_status == "already":
            preview = self._build_proposal_preview(
                change_request_id=int(session.change_request_id or 0),
                target_notion_page_id=session.target_notion_page_id,
                target_notion_path=session.target_notion_path,
                source_type=session.source_type or "unknown",
                source_document_id=int(session.source_document_id or 0),
                source_count=len(session.attachments),
            )
            return TelegramIngestionCommandResult(
                reply_text=preview or "Proposal is already ready for review.",
                source_document_id=session.source_document_id,
                change_request_id=session.change_request_id,
                source_type=session.source_type,
                target_notion_page_id=session.target_notion_page_id,
                target_notion_path=session.target_notion_path,
                session_id=session.session_id,
                already_processed=True,
            )
        if claim_status != "new":
            raise TelegramIngestionError(
                error_code="UPLOAD_SESSION_INVALID",
                message="Only a failed proposal can be retried. Upload and OCR are not repeated by this command.",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="UPLOAD_SESSION_INVALID",
            )
        if session.source_document_id is None or session.target_notion_page_id is None:
            raise TelegramIngestionError(
                error_code="UPLOAD_SESSION_INVALID",
                message="The existing source is missing a safe proposal retry target.",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="UPLOAD_SESSION_INVALID",
            )

        return await self._retry_claimed_source_proposal(
            session=session,
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            request_workflow_id=request_workflow_id,
        )

    async def _retry_claimed_source_proposal(
        self,
        *,
        session: TelegramUploadSession,
        session_id: str,
        chat_id: str,
        user_id: str,
        request_workflow_id: str,
    ) -> TelegramIngestionCommandResult:
        """Generate a proposal from a claimed source without re-ingestion."""

        if session.source_document_id is None or session.target_notion_page_id is None:
            raise TelegramIngestionError(
                error_code="UPLOAD_SESSION_INVALID",
                message="The existing source is missing a safe proposal retry target.",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="UPLOAD_SESSION_INVALID",
            )

        try:
            result = await self._supplement_propose_orchestrator.propose_change_request(
                source_document_id=int(session.source_document_id),
                provider_name=DEFAULT_SUPPLEMENT_PROVIDER_NAME,
                model=DEFAULT_SUPPLEMENT_MODEL,
                request_workflow_id=request_workflow_id,
                target_notion_page_id=session.target_notion_page_id,
            )
        except SupplementProposeError as exc:
            self._session_store.fail_upload(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                failure_reason=exc.failure_reason,
            )
            raise TelegramIngestionError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
                workflow_run_id=exc.workflow_run_id,
                metadata=exc.metadata,
            ) from exc

        self._session_store.record_proposal(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            source_document_id=int(result.source_document_id),
            change_request_id=int(result.change_request_id),
            source_type=session.source_type or "screenshot",
            target_notion_page_id=session.target_notion_page_id,
            target_notion_path=session.target_notion_path or "",
        )
        preview = self._build_proposal_preview(
            change_request_id=int(result.change_request_id),
            target_notion_page_id=session.target_notion_page_id,
            target_notion_path=session.target_notion_path,
            source_type=session.source_type or "screenshot",
            source_document_id=int(result.source_document_id),
            source_count=len(session.attachments),
        )
        return TelegramIngestionCommandResult(
            reply_text=preview or "Proposal retry succeeded; it is ready for review.",
            source_document_id=result.source_document_id,
            change_request_id=result.change_request_id,
            source_type=session.source_type or "screenshot",
            target_notion_page_id=session.target_notion_page_id,
            target_notion_path=session.target_notion_path,
            session_id=session_id,
            latency_metadata=result.latency_metadata,
        )

    async def handle_ingest_command(
        self,
        *,
        chat_id: str,
        document: Optional[TelegramDocumentAttachment],
        photos: List[TelegramPhotoAttachment],
        request_workflow_id: str,
        target_notion_page_id: Optional[str] = None,
        on_source_created: Optional[Callable[[int, str], None]] = None,
    ) -> TelegramIngestionCommandResult:
        _ = chat_id
        business_started = perf_counter()
        latency = LatencyEvidence()
        normalized_photos = self._deduplicate_photos(photos)
        if document is None and not normalized_photos:
            return TelegramIngestionCommandResult(
                reply_text=(
                    "Use /ingest with one PDF document or one/more screenshots."
                ),
                source_document_id=None,
                change_request_id=None,
                source_type=None,
                target_notion_page_id=target_notion_page_id,
            )

        try:
            if document is not None:
                source_result, download_ms = await self._ingest_pdf_document(
                    document=document,
                    request_workflow_id=request_workflow_id,
                )
                source_count = 1
            else:
                source_result, download_ms = await self._ingest_screenshot_batch(
                    photos=normalized_photos,
                    request_workflow_id=request_workflow_id,
                )
                source_count = len(normalized_photos)
            latency.add(download_ms=download_ms)
            if on_source_created is not None:
                on_source_created(
                    int(source_result.source_document_id),
                    str(source_result.source_type),
                )
            self._merge_stage_latency(
                latency,
                getattr(source_result, "latency_metadata", {}),
            )

            proposal_result = await self._supplement_propose_orchestrator.propose_change_request(
                source_document_id=source_result.source_document_id,
                provider_name=DEFAULT_SUPPLEMENT_PROVIDER_NAME,
                model=DEFAULT_SUPPLEMENT_MODEL,
                request_workflow_id=request_workflow_id,
                target_notion_page_id=target_notion_page_id,
            )
            self._merge_stage_latency(latency, proposal_result.latency_metadata)
            latency.add(total_business_ms=elapsed_ms(business_started))
            proposal_preview = self._build_proposal_preview(
                change_request_id=proposal_result.change_request_id,
                target_notion_page_id=proposal_result.target_notion_page_id,
                target_notion_path=proposal_result.target_notion_path,
                source_type=source_result.source_type,
                source_document_id=source_result.source_document_id,
                source_count=source_count,
            )
        except (DocumentIngestionError, ImageOCRIngestionError, SupplementProposeError) as exc:
            raise TelegramIngestionError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=self._normalize_failure_reason(exc.failure_reason),
                workflow_run_id=exc.workflow_run_id,
                metadata=getattr(exc, "metadata", None),
            ) from exc
        except SupplementQueryError as exc:
            raise TelegramIngestionError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
            ) from exc

        return TelegramIngestionCommandResult(
            reply_text=proposal_preview or self._build_success_reply(
                source_type=source_result.source_type,
                source_document_id=source_result.source_document_id,
                change_request_id=proposal_result.change_request_id,
                source_count=source_count,
            ),
            source_document_id=source_result.source_document_id,
            change_request_id=proposal_result.change_request_id,
            source_type=source_result.source_type,
            target_notion_page_id=proposal_result.target_notion_page_id,
            target_notion_path=proposal_result.target_notion_path,
            latency_metadata=latency.as_dict(),
        )

    def _build_proposal_preview(
        self,
        *,
        change_request_id: int,
        target_notion_page_id: Optional[str],
        target_notion_path: Optional[str],
        source_type: str,
        source_document_id: int,
        source_count: int,
    ) -> Optional[str]:
        if self._supplement_query_orchestrator is None:
            return None
        item = self._supplement_query_orchestrator.get_detail(
            change_request_id=change_request_id
        )
        return self._format_proposal_preview(
            item=item,
            target_notion_page_id=target_notion_page_id,
            target_notion_path=target_notion_path,
            source_type=source_type,
            source_document_id=source_document_id,
            source_count=source_count,
        )

    def _format_proposal_preview(
        self,
        *,
        item: SupplementReviewItemResult,
        target_notion_page_id: Optional[str],
        target_notion_path: Optional[str],
        source_type: str,
        source_document_id: int,
        source_count: int,
    ) -> str:
        target = target_notion_path or target_notion_page_id or "not selected"
        if source_type == "screenshot":
            ingestion_summary = (
                "Ingestion succeeded "
                f"(screenshots={source_count}, source_document_id={source_document_id}, "
                f"change_request_id={item.change_request_id}, status=pending)."
            )
        else:
            ingestion_summary = (
                "Ingestion succeeded "
                f"(source_type={source_type}, source_document_id={source_document_id}, "
                f"change_request_id={item.change_request_id}, status=pending)."
            )
        lines = [
            ingestion_summary,
            f"Proposal ready for review (change_request_id={item.change_request_id})",
            f"Title: {item.proposal.title}",
            f"Source: {item.proposal.source_display_name}",
            f"Target Notion page: {target}",
            f"Summary: {item.proposal.summary}",
            "Key Concepts: " + ", ".join(item.proposal.concepts),
            "Notes:",
        ]
        lines.extend(format_bounded_note_lines(item.proposal.notes))
        lines.append("Citations:")
        for citation in item.citations:
            citation_value = (
                citation.notion_path
                or citation.source_display_name
                or citation.page_id
                or citation.quote
                or "unavailable"
            )
            lines.append(f"- {citation_value}")
        if target_notion_page_id:
            lines.append(
                f"Review with /accept {item.change_request_id} or /reject "
                f"{item.change_request_id} <reason>."
            )
        else:
            lines.append(
                "Target is not selected. Choose a Notion page before accepting "
                "this proposal."
            )
        return truncate_telegram_preview("\n".join(lines))

    async def _ingest_pdf_document(
        self,
        *,
        document: TelegramDocumentAttachment,
        request_workflow_id: str,
    ) -> tuple[Any, float]:
        download_started = perf_counter()
        downloaded = await self._download_telegram_file(
            file_id=document.file_id,
            request_workflow_id=request_workflow_id,
        )

        source_file_name = (document.file_name or "").strip() or downloaded.file_name
        if not source_file_name.lower().endswith(".pdf"):
            raise TelegramIngestionError(
                error_code="INVALID_ARGUMENT",
                message="/ingest document must be a .pdf file",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            )

        download_ms = elapsed_ms(download_started)
        result = await self._document_ingestion_orchestrator.ingest_document(
            file_name=source_file_name,
            file_bytes=downloaded.file_bytes,
            mime_type=document.mime_type,
            request_workflow_id=request_workflow_id,
        )
        return result, download_ms

    async def _ingest_screenshot_batch(
        self,
        *,
        photos: List[TelegramPhotoAttachment],
        request_workflow_id: str,
    ) -> tuple[Any, float]:
        image_inputs: List[ImageUploadInput] = []
        download_ms = 0.0
        for index, photo in enumerate(photos, start=1):
            download_started = perf_counter()
            downloaded = await self._download_telegram_file(
                file_id=photo.file_id,
                request_workflow_id=request_workflow_id,
            )
            download_ms += elapsed_ms(download_started)
            image_file_name = downloaded.file_name.strip() or f"telegram-screenshot-{index}.jpg"
            image_inputs.append(
                ImageUploadInput(
                    file_name=image_file_name,
                    file_bytes=downloaded.file_bytes,
                )
            )

        result = await self._image_ocr_ingestion_orchestrator.ingest_image_ocr(
            images=image_inputs,
            request_workflow_id=request_workflow_id,
        )
        return result, download_ms

    def _merge_stage_latency(
        self,
        latency: LatencyEvidence,
        incoming: Dict[str, float],
    ) -> None:
        latency.update(
            {
                key: float(incoming.get(key, 0.0))
                for key in (
                    "download_ms",
                    "ocr_ms",
                    "llm_ms",
                    "persist_ms",
                    "preview_delivery_ms",
                )
            }
        )

    async def _download_telegram_file(
        self,
        *,
        file_id: str,
        request_workflow_id: str,
    ):
        normalized_file_id = file_id.strip()
        if not normalized_file_id:
            raise TelegramIngestionError(
                error_code="INVALID_ARGUMENT",
                message="file_id is required",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            )

        tool_result = await self._tool_registry.call_tool(
            TELEGRAM_BOT_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "telegram_file_download",
                    "file_id": normalized_file_id,
                },
            ),
            arguments={
                "action": "download_file",
                "file_id": normalized_file_id,
            },
        )
        if tool_result.is_error:
            error_code = "UNKNOWN_ERROR"
            error_message = "Telegram file download failed"
            if tool_result.error is not None:
                error_code = tool_result.error.code
                error_message = tool_result.error.message
            raise TelegramIngestionError(
                error_code=error_code,
                message=error_message,
                http_status_code=TOOL_ERROR_TO_HTTP_STATUS.get(
                    error_code,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ),
                failure_reason=self._normalize_failure_reason(error_code),
            )

        structured_content = tool_result.structured_content or {}
        encoded = str(structured_content.get("file_bytes_base64", "")).strip()
        if not encoded:
            raise TelegramIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="telegram_bot download_file output missing file_bytes_base64",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        try:
            file_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise TelegramIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message=f"telegram_bot download_file output is invalid base64: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            ) from exc
        if not file_bytes:
            raise TelegramIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="telegram_bot download_file output contains empty bytes",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )

        file_name = str(structured_content.get("file_name", "")).strip()
        if not file_name:
            file_name = f"{normalized_file_id}.bin"

        return _DownloadedTelegramFile(
            file_name=file_name,
            file_bytes=file_bytes,
        )

    def _deduplicate_photos(
        self,
        photos: List[TelegramPhotoAttachment],
    ) -> List[TelegramPhotoAttachment]:
        deduplicated: Dict[str, TelegramPhotoAttachment] = {}
        for photo in photos:
            normalized_file_id = photo.file_id.strip()
            if not normalized_file_id:
                continue
            dedupe_key = (photo.file_unique_id or "").strip() or normalized_file_id
            current = deduplicated.get(dedupe_key)
            if current is None:
                deduplicated[dedupe_key] = TelegramPhotoAttachment(
                    file_id=normalized_file_id,
                    file_unique_id=(photo.file_unique_id or "").strip() or None,
                    file_size=photo.file_size,
                )
                continue

            current_size = current.file_size or 0
            incoming_size = photo.file_size or 0
            if incoming_size > current_size:
                deduplicated[dedupe_key] = TelegramPhotoAttachment(
                    file_id=normalized_file_id,
                    file_unique_id=(photo.file_unique_id or "").strip() or None,
                    file_size=photo.file_size,
                )

        return list(deduplicated.values())

    def _build_success_reply(
        self,
        *,
        source_type: str,
        source_document_id: int,
        change_request_id: int,
        source_count: int,
    ) -> str:
        if source_type == "screenshot":
            return (
                "Ingestion succeeded "
                f"(screenshots={source_count}, source_document_id={source_document_id}, "
                f"change_request_id={change_request_id}, status=pending)."
            )
        return (
            "Ingestion succeeded "
            f"(source_type={source_type}, source_document_id={source_document_id}, "
            f"change_request_id={change_request_id}, status=pending)."
        )

    def _normalize_failure_reason(self, failure_reason: str) -> str:
        normalized = failure_reason.strip().upper()
        if normalized in STANDARD_FAILURE_REASONS:
            return normalized
        return "UNKNOWN_ERROR"


@dataclass
class _DownloadedTelegramFile:
    file_name: str
    file_bytes: bytes
