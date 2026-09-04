from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from http import HTTPStatus
from time import perf_counter
from typing import Any, Dict, List, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.services import (
    MAX_OCR_IMAGE_BYTES,
    MAX_OCR_IMAGE_COUNT,
    STANDARD_FAILURE_REASONS,
    UploadValidationError,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
    validate_extracted_text,
    validate_file_bytes,
    validate_image_metadata,
    validate_ocr_batch,
    upload_error_http_status,
)
from src.services.latency_evidence import LatencyEvidence, elapsed_ms
from src.services.screenshot_quality import preprocess_screenshot_ocr_text
from src.tools import ToolContext, ToolRegistry

IMAGE_OCR_TOOL_NAME = "image_ocr_parser"

TOOL_ERROR_TO_HTTP_STATUS: Dict[str, int] = {
    "INVALID_ARGUMENT": HTTPStatus.BAD_REQUEST,
    "INVALID_UPLOAD_MIME": HTTPStatus.BAD_REQUEST,
    "EMPTY_UPLOAD": HTTPStatus.BAD_REQUEST,
    "UPLOAD_LIMIT_EXCEEDED": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "UPLOAD_TOO_LARGE": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    "IMAGE_PIXEL_LIMIT_EXCEEDED": HTTPStatus.UNPROCESSABLE_ENTITY,
    "INVALID_IMAGE": HTTPStatus.UNPROCESSABLE_ENTITY,
    "EXTRACTED_TEXT_LIMIT_EXCEEDED": HTTPStatus.UNPROCESSABLE_ENTITY,
    "OCR_FAILED": HTTPStatus.UNPROCESSABLE_ENTITY,
}


@dataclass
class ImageUploadInput:
    file_name: str
    file_bytes: bytes
    mime_type: Optional[str] = None


@dataclass
class ImageOCRIngestionResult:
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    content_hash: str
    latency_metadata: Dict[str, float]


class ImageOCRIngestionError(Exception):
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


class ImageOCRIngestionOrchestrator:
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

    async def ingest_image_ocr(
        self,
        *,
        images: List[ImageUploadInput],
        request_workflow_id: str,
    ) -> ImageOCRIngestionResult:
        business_started = perf_counter()
        normalized_images = self._validate_images(images)
        source_display_name = self._build_source_display_name(len(normalized_images))

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="ingestion",
            metadata_json=json.dumps(
                {
                    "operation": "ingest_image_ocr",
                    "source_type": "screenshot",
                    "source_display_name": source_display_name,
                    "image_count": len(normalized_images),
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )

        try:
            ocr_started = perf_counter()
            parsed = await self._parse_images(
                images=normalized_images,
                request_workflow_id=request_workflow_id,
            )
            raw_text = self._extract_raw_text(parsed)
            ocr_ms = elapsed_ms(ocr_started)
            content_hash = self._build_content_hash(raw_text)
            persist_started = perf_counter()
            with self._unit_of_work_factory() as unit_of_work:
                source_document = unit_of_work.source_documents.create_source_document(
                    source_type="screenshot",
                    source_display_name=source_display_name,
                    raw_text=raw_text,
                    content_hash=content_hash,
                )
                source_document_id = int(source_document.id)
                persisted_source_type = source_document.source_type
                persisted_display_name = source_document.source_display_name
                persisted_content_hash = source_document.content_hash
            persist_ms = elapsed_ms(persist_started)
            latency = LatencyEvidence()
            latency.add(
                ocr_ms=ocr_ms,
                persist_ms=persist_ms,
                total_business_ms=elapsed_ms(business_started),
            )

            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    {
                        "operation": "ingest_image_ocr",
                        "source_document_id": source_document_id,
                        "source_type": "screenshot",
                        "source_display_name": source_display_name,
                        "image_count": len(normalized_images),
                        "content_hash": content_hash,
                        "char_count": len(raw_text),
                        **latency.as_dict(),
                    },
                    sort_keys=True,
                ),
            )
        except WorkflowRunAuditUpdateError:
            raise
        except ImageOCRIngestionError as exc:
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason=exc.failure_reason,
                error_code=exc.error_code,
            )
            raise ImageOCRIngestionError(
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
            raise ImageOCRIngestionError(
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
                message=f"Failed to ingest screenshot OCR source: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

        return ImageOCRIngestionResult(
            workflow_run_id=workflow_run.id,
            status="succeeded",
            source_document_id=source_document_id,
            source_type=persisted_source_type,
            source_display_name=persisted_display_name,
            content_hash=persisted_content_hash,
            latency_metadata=latency.as_dict(),
        )

    def _validate_images(self, images: List[ImageUploadInput]) -> List[ImageUploadInput]:
        if not images:
            raise ImageOCRIngestionError(
                error_code="INVALID_ARGUMENT",
                message="images must contain at least one image",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        if len(images) > MAX_OCR_IMAGE_COUNT:
            raise ImageOCRIngestionError(
                error_code="UPLOAD_LIMIT_EXCEEDED",
                message=(
                    f"OCR image count exceeds the {MAX_OCR_IMAGE_COUNT} image limit"
                ),
                http_status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                failure_reason="UPLOAD_LIMIT_EXCEEDED",
            )

        normalized_images: List[ImageUploadInput] = []
        total_bytes = 0
        for index, image in enumerate(images, start=1):
            file_name = image.file_name.strip()
            if not file_name:
                file_name = f"image-{index}"
            if not image.file_bytes:
                raise ImageOCRIngestionError(
                    error_code="INVALID_ARGUMENT",
                    message=f"images[{index}] is empty",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                )
            try:
                validate_image_metadata(mime_type=image.mime_type)
                validate_file_bytes(
                    file_bytes=image.file_bytes,
                    maximum_bytes=MAX_OCR_IMAGE_BYTES,
                    label=f"images[{index}]",
                )
                total_bytes += len(image.file_bytes)
                validate_ocr_batch(
                    image_count=index,
                    total_bytes=total_bytes,
                )
            except UploadValidationError as exc:
                raise ImageOCRIngestionError(
                    error_code=exc.error_code,
                    message=exc.message,
                    http_status_code=upload_error_http_status(exc.error_code),
                    failure_reason=exc.failure_reason,
                ) from exc
            normalized_images.append(
                ImageUploadInput(
                    file_name=file_name,
                    file_bytes=image.file_bytes,
                    mime_type=image.mime_type,
                )
            )
        return normalized_images

    async def _parse_images(
        self,
        *,
        images: List[ImageUploadInput],
        request_workflow_id: str,
    ) -> Dict[str, Any]:
        tool_result = await self._tool_registry.call_tool(
            IMAGE_OCR_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "ingest_image_ocr",
                    "source_type": "screenshot",
                    "image_count": len(images),
                },
            ),
            arguments={
                "images": [
                    {
                        "file_name": image.file_name,
                        "file_bytes_base64": base64.b64encode(image.file_bytes).decode(
                            "ascii"
                        ),
                    }
                    for image in images
                ]
            },
        )

        if tool_result.is_error:
            error_code = "UNKNOWN_ERROR"
            message = "Image OCR parser failed"
            if tool_result.error is not None:
                error_code = tool_result.error.code
                message = tool_result.error.message
            raise ImageOCRIngestionError(
                error_code=error_code,
                message=message,
                http_status_code=TOOL_ERROR_TO_HTTP_STATUS.get(
                    error_code, HTTPStatus.INTERNAL_SERVER_ERROR
                ),
                failure_reason=self._normalize_failure_reason(error_code),
            )

        structured_content = tool_result.structured_content
        if structured_content is None:
            raise ImageOCRIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="Image OCR parser structured_content is missing",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        return structured_content

    def _extract_raw_text(self, parser_output: Dict[str, Any]) -> str:
        raw_text = parser_output.get("raw_text")
        if not isinstance(raw_text, str):
            raise ImageOCRIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="Image OCR parser raw_text is invalid",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        normalized_raw_text = preprocess_screenshot_ocr_text(raw_text)
        normalized_raw_text = normalized_raw_text.strip()
        if not normalized_raw_text:
            raise ImageOCRIngestionError(
                error_code="OCR_FAILED",
                message="No extractable text found in images",
                http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                failure_reason="OCR_FAILED",
            )
        try:
            validate_extracted_text(normalized_raw_text)
        except UploadValidationError as exc:
            raise ImageOCRIngestionError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=upload_error_http_status(exc.error_code),
                failure_reason=exc.failure_reason,
            ) from exc
        return normalized_raw_text

    def _build_source_display_name(self, image_count: int) -> str:
        return f"Screenshot batch ({image_count} images)"

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
                    "operation": "ingest_image_ocr",
                    "source_type": "screenshot",
                    "error_code": error_code,
                },
                sort_keys=True,
            ),
        )
