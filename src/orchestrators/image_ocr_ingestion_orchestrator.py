from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional

from src.db.unit_of_work import UnitOfWorkFactory
from src.services import (
    MAX_OCR_IMAGE_BYTES,
    MAX_OCR_IMAGE_COUNT,
    STANDARD_FAILURE_REASONS,
    CostTracker,
    EmbeddingBatchService,
    KnowledgeIndexingError,
    KnowledgeIndexingService,
    UploadValidationError,
    WorkflowRunAuditUpdateError,
    WorkflowRunService,
    validate_extracted_text,
    validate_file_bytes,
    validate_image_metadata,
    validate_ocr_batch,
    inspect_image_dimensions,
    upload_error_http_status,
)
from src.services.latency_evidence import LatencyEvidence, elapsed_ms
from src.services.screenshot_quality import preprocess_screenshot_ocr_text
from src.tools import ToolContext, ToolRegistry

IMAGE_OCR_TOOL_NAME = "image_ocr_parser"
MAX_IMAGE_SOURCE_TITLE_CHARS = 48
MAX_IMAGE_SOURCE_PREVIEW_CHARS = 180
IMAGE_SECTION_MARKER_PATTERN = re.compile(r"^\[Image\s+\d+:.*\]$")
IMAGE_SECTION_HEADER_PATTERN = re.compile(
    r"^\[Image\s+(?P<sequence_index>\d+):[^\]]*\]\s*$",
    re.MULTILINE,
)

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
    width: Optional[int] = None
    height: Optional[int] = None
    sequence_index: int = 0


@dataclass
class ImageOCRPart:
    sequence_index: int
    original_filename: str
    raw_text: str
    file_hash: str
    width: Optional[int]
    height: Optional[int]


@dataclass
class ImageOCRIngestionResult:
    workflow_run_id: int
    status: str
    source_document_id: int
    source_type: str
    source_display_name: str
    original_filename: str
    content_hash: str
    latency_metadata: Dict[str, float]
    index_status: str = "indexed"
    indexed_chunk_count: int = 0
    embedded_chunk_count: int = 0
    source_preview: Optional[str] = None
    image_count: int = 1


@dataclass
class ImageOCRIngestionItemResult:
    original_filename: str
    status: str
    sequence_index: int = 1
    workflow_run_id: Optional[int] = None
    source_document_id: Optional[int] = None
    source_type: str = "image"
    source_display_name: Optional[str] = None
    content_hash: Optional[str] = None
    index_status: Optional[str] = None
    indexed_chunk_count: int = 0
    embedded_chunk_count: int = 0
    error_code: Optional[str] = None
    message: Optional[str] = None
    failure_reason: Optional[str] = None
    file_hash: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class ImageOCRBatchIngestionResult:
    status: str
    source_type: str
    source_display_name: str
    image_results: List[ImageOCRIngestionItemResult]
    workflow_run_ids: List[int]
    source_preview: Optional[str] = None
    image_count: int = 0
    latency_metadata: Dict[str, float] = field(default_factory=dict)
    source_document_id: Optional[int] = None
    content_hash: Optional[str] = None
    index_status: Optional[str] = None
    indexed_chunk_count: int = 0
    embedded_chunk_count: int = 0


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
        embedding_batch_service: Optional[EmbeddingBatchService] = None,
        cost_tracker: Optional[CostTracker] = None,
        index_knowledge: bool = True,
        source_type: str = "image",
        validate_decoded_images: bool = True,
    ) -> None:
        self._tool_registry = tool_registry
        self._unit_of_work_factory = unit_of_work_factory
        self._workflow_run_service = workflow_run_service
        self._knowledge_indexing_service = KnowledgeIndexingService(
            unit_of_work_factory=unit_of_work_factory,
            embedding_batch_service=embedding_batch_service,
            cost_tracker=cost_tracker,
        )
        self._index_knowledge = index_knowledge
        self._source_type = source_type
        self._validate_decoded_images = validate_decoded_images

    async def ingest_image_ocr(
        self,
        *,
        images: List[ImageUploadInput],
        request_workflow_id: str,
    ) -> ImageOCRIngestionResult:
        if self._index_knowledge and self._source_type == "image":
            if len(images) != 1:
                raise ImageOCRIngestionError(
                    error_code="INVALID_ARGUMENT",
                    message="Use ingest_image_ocr_batch for grouped image sources",
                    http_status_code=HTTPStatus.BAD_REQUEST,
                )
            batch_result = await self.ingest_image_ocr_batch(
                images=images,
                request_workflow_id=request_workflow_id,
            )
            item = batch_result.image_results[0]
            if item.status == "failed":
                raise ImageOCRIngestionError(
                    error_code=item.error_code or "OCR_FAILED",
                    message=item.message or "Image ingestion failed",
                    http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    failure_reason=item.failure_reason or "OCR_FAILED",
                    workflow_run_id=item.workflow_run_id,
                )
            return ImageOCRIngestionResult(
                workflow_run_id=item.workflow_run_id or 0,
                status=item.status,
                source_document_id=item.source_document_id or 0,
                source_type=item.source_type,
                source_display_name=batch_result.source_display_name,
                original_filename=item.original_filename,
                content_hash=item.content_hash or "",
                latency_metadata=batch_result.latency_metadata or {},
                index_status=item.index_status or "indexed",
                indexed_chunk_count=item.indexed_chunk_count,
                embedded_chunk_count=item.embedded_chunk_count,
                source_preview=batch_result.source_preview,
                image_count=batch_result.image_count,
            )
        return await self._ingest_image_ocr_legacy(
            images=images,
            request_workflow_id=request_workflow_id,
        )

    async def _ingest_image_ocr_legacy(
        self,
        *,
        images: List[ImageUploadInput],
        request_workflow_id: str,
    ) -> ImageOCRIngestionResult:
        business_started = perf_counter()
        normalized_images = self._validate_images(images)
        if self._index_knowledge and len(normalized_images) != 1:
            raise ImageOCRIngestionError(
                error_code="INVALID_ARGUMENT",
                message="Indexed image ingestion accepts one image per source",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        source_display_name = self._build_source_display_name(normalized_images)
        file_hash = self._build_file_hash(normalized_images)

        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="ingestion",
            metadata_json=json.dumps(
                {
                    "operation": "ingest_image_ocr",
                    "source_type": self._source_type,
                    "source_display_name": source_display_name,
                    "image_count": len(normalized_images),
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
            if self._index_knowledge:
                with self._unit_of_work_factory() as unit_of_work:
                    existing_source = unit_of_work.source_documents.find_indexed_image_source_by_file_hash(
                        file_hash=file_hash,
                        owner_scope="local",
                    )
                if existing_source is not None:
                    self._workflow_run_service.mark_workflow_succeeded(
                        workflow_run.id,
                        metadata_json=json.dumps(
                            {
                                "operation": "ingest_image_duplicate_guard",
                                "source_document_id": existing_source.id,
                                "source_type": "image",
                                "source_display_name": existing_source.display_name,
                                "file_hash": file_hash,
                                "content_hash": existing_source.content_hash,
                                "index_status": "indexed",
                                "result_status": "already_indexed",
                                "indexed_chunk_count": existing_source.chunk_count,
                                "embedded_chunk_count": existing_source.chunk_count,
                            },
                            sort_keys=True,
                        ),
                    )
                    return ImageOCRIngestionResult(
                        workflow_run_id=workflow_run.id,
                        status="already_indexed",
                        source_document_id=existing_source.id,
                        source_type=existing_source.source_kind,
                        source_display_name=existing_source.display_name,
                        original_filename=normalized_images[0].file_name,
                        content_hash=existing_source.content_hash,
                        latency_metadata={},
                        index_status=existing_source.status,
                        indexed_chunk_count=existing_source.chunk_count,
                        embedded_chunk_count=existing_source.chunk_count,
                    )

            ocr_started = perf_counter()
            parsed = await self._parse_images(
                images=normalized_images,
                request_workflow_id=request_workflow_id,
            )
            raw_text = self._extract_raw_text(parsed)
            ocr_ms = elapsed_ms(ocr_started)
            content_hash = self._build_content_hash(raw_text)
            if self._index_knowledge and self._source_type == "image":
                source_display_name = self._derive_image_display_name(
                    raw_text=raw_text,
                    fallback=normalized_images[0].file_name,
                )
            persist_started = perf_counter()
            with self._unit_of_work_factory() as unit_of_work:
                source_document = unit_of_work.source_documents.create_source_document(
                    source_type=self._source_type,
                    source_display_name=source_display_name,
                    original_filename=(
                        normalized_images[0].file_name
                        if len(normalized_images) == 1
                        else None
                    ),
                    raw_text=raw_text,
                    content_hash=content_hash,
                    file_hash=file_hash if self._index_knowledge else None,
                    owner_scope="local",
                    status="indexing" if self._index_knowledge else "parsed",
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
            if not self._index_knowledge:
                self._workflow_run_service.mark_workflow_succeeded(
                    workflow_run.id,
                    metadata_json=json.dumps(
                        {
                            "operation": "ingest_image_ocr",
                            "source_document_id": source_document_id,
                            "source_type": self._source_type,
                            "source_display_name": source_display_name,
                            "image_count": len(normalized_images),
                            "content_hash": content_hash,
                            "char_count": len(raw_text),
                            "index_status": "parsed",
                            **latency.as_dict(),
                        },
                        sort_keys=True,
                    ),
                )
                return ImageOCRIngestionResult(
                    workflow_run_id=workflow_run.id,
                    status="succeeded",
                    source_document_id=source_document_id,
                    source_type=persisted_source_type,
                    source_display_name=persisted_display_name,
                    original_filename=normalized_images[0].file_name,
                    content_hash=persisted_content_hash,
                    latency_metadata=latency.as_dict(),
                    index_status="parsed",
                )

            try:
                indexing_result = await self._knowledge_indexing_service.index_source_document(
                    source_document_id=source_document_id,
                    source_kind="image",
                    source_display_name=source_display_name,
                    raw_text=raw_text,
                    request_workflow_id=request_workflow_id,
                    citation_metadata=self._build_citation_metadata(
                        images=normalized_images,
                        source_display_name=source_display_name,
                    ),
                    owner_scope="local",
                    empty_chunks_error_code="OCR_FAILED",
                )
            except KnowledgeIndexingError as exc:
                raise ImageOCRIngestionError(
                    error_code=exc.error_code,
                    message=exc.message,
                    http_status_code=exc.http_status_code,
                    failure_reason=exc.failure_reason,
                ) from None
            indexed_chunk_count = indexing_result.indexed_chunk_count
            embedded_chunk_count = indexing_result.embedded_chunk_count
            embedding_metadata = indexing_result.embedding_metadata
            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    {
                        "operation": "ingest_image_ocr",
                        "source_document_id": source_document_id,
                        "source_type": self._source_type,
                        "source_display_name": source_display_name,
                        "image_count": len(normalized_images),
                        "file_hash": file_hash,
                        "content_hash": content_hash,
                        "char_count": len(raw_text),
                        "index_status": "indexed",
                        "indexed_chunk_count": indexed_chunk_count,
                        "embedded_chunk_count": embedded_chunk_count,
                        **embedding_metadata,
                        **latency.as_dict(),
                    },
                    sort_keys=True,
                ),
            )
        except WorkflowRunAuditUpdateError:
            raise
        except ImageOCRIngestionError as exc:
            self._mark_source_document_failed(source_document_id)
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
            self._mark_source_document_failed(source_document_id)
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
            )
            raise ImageOCRIngestionError(
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
                message=f"Failed to ingest image OCR source: {exc}",
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
            original_filename=normalized_images[0].file_name,
            content_hash=persisted_content_hash,
            latency_metadata=latency.as_dict(),
            index_status="indexed",
            indexed_chunk_count=indexed_chunk_count,
            embedded_chunk_count=embedded_chunk_count,
        )

    async def ingest_image_ocr_batch(
        self,
        *,
        images: List[ImageUploadInput],
        request_workflow_id: str,
    ) -> ImageOCRBatchIngestionResult:
        business_started = perf_counter()
        normalized_images = self._validate_images(images)
        if not self._index_knowledge or self._source_type != "image":
            raise ImageOCRIngestionError(
                error_code="INVALID_ARGUMENT",
                message="Grouped image indexing requires the active image source path",
                http_status_code=HTTPStatus.BAD_REQUEST,
            )
        batch_file_hash = self._build_file_hash(normalized_images)
        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="ingestion",
            metadata_json=json.dumps(
                {
                    "operation": "ingest_image_ocr_batch",
                    "source_type": "image",
                    "image_count": len(normalized_images),
                    "sequence_manifest": [
                        {
                            "sequence_index": image.sequence_index,
                            "original_filename": image.file_name,
                        }
                        for image in normalized_images
                    ],
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )
        source_document_id: Optional[int] = None
        try:
            with self._unit_of_work_factory() as unit_of_work:
                existing_source = unit_of_work.source_documents.find_indexed_image_source_by_file_hash(
                    file_hash=batch_file_hash,
                    owner_scope="local",
                )
            if existing_source is not None:
                self._workflow_run_service.mark_workflow_succeeded(
                    workflow_run.id,
                    metadata_json=json.dumps(
                        {
                            "operation": "ingest_image_duplicate_guard",
                            "source_document_id": existing_source.id,
                            "source_display_name": existing_source.display_name,
                            "file_hash": batch_file_hash,
                            "result_status": "already_indexed",
                        },
                        sort_keys=True,
                    ),
                )
                return ImageOCRBatchIngestionResult(
                    status="already_indexed",
                    source_type="image",
                    source_display_name=existing_source.display_name,
                    source_preview=existing_source.source_preview,
                    image_count=existing_source.image_count or len(normalized_images),
                    image_results=self._build_image_results(
                        images=normalized_images,
                        status="already_indexed",
                        workflow_run_id=workflow_run.id,
                        source_document_id=existing_source.id,
                        source_display_name=existing_source.display_name,
                        content_hash=existing_source.content_hash,
                        index_status=existing_source.status,
                        indexed_chunk_count=existing_source.chunk_count,
                        embedded_chunk_count=existing_source.chunk_count,
                    ),
                    workflow_run_ids=[workflow_run.id],
                    source_document_id=existing_source.id,
                    content_hash=existing_source.content_hash,
                    index_status=existing_source.status,
                    indexed_chunk_count=existing_source.chunk_count,
                    embedded_chunk_count=existing_source.chunk_count,
                    latency_metadata={},
                )

            ocr_started = perf_counter()
            parsed = await self._parse_images(
                images=normalized_images,
                request_workflow_id=request_workflow_id,
            )
            normalized_ocr_text = self._extract_raw_text(parsed, allow_empty=True)
            ordered_parts = self._build_ordered_image_parts(
                raw_text=normalized_ocr_text,
                images=normalized_images,
            )
            if not any(
                self._meaningful_ocr_lines(part.raw_text) for part in ordered_parts
            ):
                raise ImageOCRIngestionError(
                    error_code="OCR_FAILED",
                    message="No extractable text found in images",
                    http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    failure_reason="OCR_FAILED",
                )
            raw_text = self._build_grouped_raw_text(ordered_parts)
            ocr_ms = elapsed_ms(ocr_started)
            content_hash = self._build_content_hash(raw_text)
            source_title, source_preview = self._derive_grouped_source_label(
                ordered_parts=ordered_parts,
                fallback=normalized_images[0].file_name,
            )
            source_display_name = self._format_image_source_display_name(
                title=source_title,
                image_count=len(normalized_images),
            )
            source_metadata = json.dumps(
                {
                    "images": [
                        {
                            "sequence_index": image.sequence_index,
                            "original_filename": image.file_name,
                            "file_hash": self._build_single_file_hash(image),
                            "width": image.width,
                            "height": image.height,
                        }
                        for image in normalized_images
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            persist_started = perf_counter()
            with self._unit_of_work_factory() as unit_of_work:
                source_document = unit_of_work.source_documents.create_source_document(
                    source_type="image",
                    source_display_name=source_display_name,
                    original_filename=(
                        normalized_images[0].file_name
                        if len(normalized_images) == 1
                        else None
                    ),
                    source_preview=source_preview,
                    source_metadata=source_metadata,
                    image_count=len(normalized_images),
                    raw_text=raw_text,
                    content_hash=content_hash,
                    file_hash=batch_file_hash,
                    owner_scope="local",
                    status="indexing",
                )
                source_document_id = int(source_document.id)
            persist_ms = elapsed_ms(persist_started)
            latency = LatencyEvidence()
            latency.add(
                ocr_ms=ocr_ms,
                persist_ms=persist_ms,
                total_business_ms=elapsed_ms(business_started),
            )
            page_texts = [part.raw_text for part in ordered_parts]
            page_labels = [f"Image {part.sequence_index}" for part in ordered_parts]
            page_citation_metadata = [
                {
                    "image_index": part.sequence_index,
                    "sequence_index": part.sequence_index,
                    "original_filename": part.original_filename,
                    "file_hash": part.file_hash,
                    "width": part.width,
                    "height": part.height,
                }
                for part in ordered_parts
            ]
            try:
                indexing_result = await self._knowledge_indexing_service.index_source_document(
                    source_document_id=source_document_id,
                    source_kind="image",
                    source_display_name=source_display_name,
                    raw_text=raw_text,
                    request_workflow_id=request_workflow_id,
                    pages=page_texts,
                    page_labels=page_labels,
                    page_citation_metadata=page_citation_metadata,
                    citation_metadata={"source_preview": source_preview},
                    owner_scope="local",
                    empty_chunks_error_code="OCR_FAILED",
                )
            except KnowledgeIndexingError as exc:
                raise ImageOCRIngestionError(
                    error_code=exc.error_code,
                    message=exc.message,
                    http_status_code=exc.http_status_code,
                    failure_reason=exc.failure_reason,
                ) from None
            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run.id,
                metadata_json=json.dumps(
                    {
                        "operation": "ingest_image_ocr_batch",
                        "source_document_id": source_document_id,
                        "source_display_name": source_display_name,
                        "source_preview": source_preview,
                        "image_count": len(normalized_images),
                        "file_hash": batch_file_hash,
                        "content_hash": content_hash,
                        "index_status": "indexed",
                        "indexed_chunk_count": indexing_result.indexed_chunk_count,
                        "embedded_chunk_count": indexing_result.embedded_chunk_count,
                        "sequence_manifest": json.loads(source_metadata)["images"],
                        **indexing_result.embedding_metadata,
                        **latency.as_dict(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            return ImageOCRBatchIngestionResult(
                status="succeeded",
                source_type="image",
                source_display_name=source_display_name,
                source_preview=source_preview,
                image_count=len(normalized_images),
                image_results=self._build_image_results(
                    images=normalized_images,
                    status="succeeded",
                    workflow_run_id=workflow_run.id,
                    source_document_id=source_document_id,
                    source_display_name=source_display_name,
                    content_hash=content_hash,
                    index_status="indexed",
                    indexed_chunk_count=indexing_result.indexed_chunk_count,
                    embedded_chunk_count=indexing_result.embedded_chunk_count,
                ),
                workflow_run_ids=[workflow_run.id],
                source_document_id=source_document_id,
                content_hash=content_hash,
                index_status="indexed",
                indexed_chunk_count=indexing_result.indexed_chunk_count,
                embedded_chunk_count=indexing_result.embedded_chunk_count,
                latency_metadata=latency.as_dict(),
            )
        except WorkflowRunAuditUpdateError:
            raise
        except ImageOCRIngestionError as exc:
            self._mark_source_document_failed(source_document_id)
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
            self._mark_source_document_failed(source_document_id)
            self._mark_failed_workflow(
                workflow_run_id=workflow_run.id,
                failure_reason="UNKNOWN_ERROR",
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
            )
            raise ImageOCRIngestionError(
                error_code="SOURCE_DOCUMENT_CREATE_FAILED",
                message=f"Failed to ingest grouped image OCR source: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
                workflow_run_id=workflow_run.id,
            ) from exc

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
                width, height = (None, None)
                if self._validate_decoded_images:
                    width, height = inspect_image_dimensions(
                        image.file_bytes,
                        file_name=file_name,
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
                    width=width,
                    height=height,
                    sequence_index=index,
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
                    "source_type": self._source_type,
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

    def _extract_raw_text(
        self,
        parser_output: Dict[str, Any],
        *,
        allow_empty: bool = False,
    ) -> str:
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
        if not normalized_raw_text and allow_empty:
            return ""
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

    def _build_source_display_name(self, images: List[ImageUploadInput]) -> str:
        if self._source_type == "screenshot":
            return f"Screenshot batch ({len(images)} images)"
        if len(images) == 1:
            return images[0].file_name
        return f"Image batch ({len(images)} images)"

    def _derive_image_display_name(self, *, raw_text: str, fallback: str) -> str:
        for raw_line in raw_text.splitlines():
            candidate = " ".join(raw_line.split()).strip()
            if not candidate or IMAGE_SECTION_MARKER_PATTERN.fullmatch(candidate):
                continue
            if not any(character.isalnum() for character in candidate):
                continue
            bounded = self._bound_text(candidate, MAX_IMAGE_SOURCE_TITLE_CHARS)
            if bounded:
                return bounded
        return Path(fallback).stem or "image"

    def _build_ordered_image_parts(
        self,
        *,
        raw_text: str,
        images: List[ImageUploadInput],
    ) -> List[ImageOCRPart]:
        matches = list(IMAGE_SECTION_HEADER_PATTERN.finditer(raw_text))
        if not matches:
            if len(images) == 1 or not raw_text.strip():
                image = images[0]
                if len(images) == 1:
                    return [
                        ImageOCRPart(
                            sequence_index=image.sequence_index,
                            original_filename=image.file_name,
                            raw_text=raw_text,
                            file_hash=self._build_single_file_hash(image),
                            width=image.width,
                            height=image.height,
                        )
                        ]
                return [
                    ImageOCRPart(
                        sequence_index=image.sequence_index,
                        original_filename=image.file_name,
                        raw_text="",
                        file_hash=self._build_single_file_hash(image),
                        width=image.width,
                        height=image.height,
                    )
                    for image in images
                ]
            raise ImageOCRIngestionError(
                error_code="TOOL_OUTPUT_INVALID",
                message="Image OCR parser output is missing ordered image sections",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )

        section_text_by_index: Dict[int, str] = {}
        for position, match in enumerate(matches):
            sequence_index = int(match.group("sequence_index"))
            if sequence_index < 1 or sequence_index > len(images):
                raise ImageOCRIngestionError(
                    error_code="TOOL_OUTPUT_INVALID",
                    message="Image OCR parser returned an invalid sequence index",
                    http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                    failure_reason="UNKNOWN_ERROR",
                )
            if sequence_index in section_text_by_index:
                raise ImageOCRIngestionError(
                    error_code="TOOL_OUTPUT_INVALID",
                    message="Image OCR parser returned duplicate sequence indexes",
                    http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                    failure_reason="UNKNOWN_ERROR",
                )
            section_start = match.end()
            section_end = matches[position + 1].start() if position + 1 < len(matches) else len(raw_text)
            section_text_by_index[sequence_index] = raw_text[section_start:section_end].strip()

        parts: List[ImageOCRPart] = []
        for image in images:
            parts.append(
                ImageOCRPart(
                    sequence_index=image.sequence_index,
                    original_filename=image.file_name,
                    raw_text=section_text_by_index.get(image.sequence_index, ""),
                    file_hash=self._build_single_file_hash(image),
                    width=image.width,
                    height=image.height,
                )
            )
        return parts

    def _build_grouped_raw_text(self, parts: List[ImageOCRPart]) -> str:
        return "\n\n".join(
            f"[Image {part.sequence_index}: {part.original_filename}]\n{part.raw_text}".strip()
            for part in parts
        ).strip()

    def _derive_grouped_source_label(
        self,
        *,
        ordered_parts: List[ImageOCRPart],
        fallback: str,
    ) -> tuple[str, Optional[str]]:
        first_part = ordered_parts[0]
        lines = self._meaningful_ocr_lines(first_part.raw_text)
        title_mode = "fallback"
        title_line_index = 0
        title = ""
        if lines:
            first_line = lines[0]
            if len(first_line) <= MAX_IMAGE_SOURCE_TITLE_CHARS and not first_line.endswith(
                (".", "。", "!", "?", "！", "？")
            ):
                title = first_line
                title_mode = "heading"
            else:
                title = self._first_meaningful_sentence(lines)
                title_mode = "sentence"
            if title in lines:
                title_line_index = lines.index(title)
        if not title:
            title = Path(fallback).stem or "image"

        preview_lines = lines[title_line_index + 1 :]
        if len(ordered_parts) > 1:
            for part in ordered_parts[1:]:
                preview_lines.extend(self._meaningful_ocr_lines(part.raw_text))
        preview = self._bound_text(" ".join(preview_lines), MAX_IMAGE_SOURCE_PREVIEW_CHARS)
        if title_mode == "sentence" and preview == title:
            preview = None
        return self._bound_text(title, MAX_IMAGE_SOURCE_TITLE_CHARS), preview

    def _meaningful_ocr_lines(self, raw_text: str) -> List[str]:
        lines: List[str] = []
        for raw_line in raw_text.splitlines():
            candidate = " ".join(raw_line.split()).strip()
            if not candidate or IMAGE_SECTION_MARKER_PATTERN.fullmatch(candidate):
                continue
            if not any(character.isalnum() for character in candidate):
                continue
            lines.append(candidate)
        return lines

    def _first_meaningful_sentence(self, lines: List[str]) -> str:
        text = " ".join(lines).strip()
        sentence_match = re.search(r"[。！？!?]|(?<=[.])\s+", text)
        if sentence_match is None:
            return text
        return text[: sentence_match.end()].strip()

    def _format_image_source_display_name(self, *, title: str, image_count: int) -> str:
        prefix = "Screenshot" if image_count == 1 else "Screenshots"
        return f"{prefix} · {title}"

    def _bound_text(self, value: str, maximum: int) -> str:
        normalized = " ".join(value.split()).strip()
        if len(normalized) <= maximum:
            return normalized
        return normalized[: maximum - 1].rstrip() + "…"

    def _build_content_hash(self, raw_text: str) -> str:
        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    def _build_file_hash(self, images: List[ImageUploadInput]) -> str:
        if len(images) == 1:
            return self._build_single_file_hash(images[0])
        canonical_entries = [
            {
                "file_hash": self._build_single_file_hash(image),
                "sequence_index": image.sequence_index,
            }
            for image in images
        ]
        canonical = json.dumps(
            canonical_entries,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(b"knowvia-image-batch-v1\0" + canonical).hexdigest()

    def _build_single_file_hash(self, image: ImageUploadInput) -> str:
        return hashlib.sha256(image.file_bytes).hexdigest()

    def _build_image_results(
        self,
        *,
        images: List[ImageUploadInput],
        status: str,
        workflow_run_id: int,
        source_document_id: int,
        source_display_name: str,
        content_hash: str,
        index_status: str,
        indexed_chunk_count: int = 0,
        embedded_chunk_count: int = 0,
    ) -> List[ImageOCRIngestionItemResult]:
        return [
            ImageOCRIngestionItemResult(
                sequence_index=image.sequence_index,
                original_filename=image.file_name,
                status=status,
                workflow_run_id=workflow_run_id,
                source_document_id=source_document_id,
                source_type="image",
                source_display_name=source_display_name,
                content_hash=content_hash,
                index_status=index_status,
                indexed_chunk_count=indexed_chunk_count,
                embedded_chunk_count=embedded_chunk_count,
                file_hash=self._build_single_file_hash(image),
                width=image.width,
                height=image.height,
            )
            for image in images
        ]

    def _build_citation_metadata(
        self,
        *,
        images: List[ImageUploadInput],
        source_display_name: str,
    ) -> Dict[str, Any]:
        if len(images) == 1:
            image = images[0]
            return {
                "source_kind": "image",
                "source_display_name": source_display_name,
                "file_name": image.file_name,
                "original_filename": image.file_name,
                "width": image.width,
                "height": image.height,
            }
        return {
            "source_kind": "image",
            "source_display_name": self._build_source_display_name(images),
            "file_names": [image.file_name for image in images],
            "image_dimensions": [
                {
                    "file_name": image.file_name,
                    "width": image.width,
                    "height": image.height,
                }
                for image in images
            ],
        }

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
                    "source_type": self._source_type,
                    "error_code": error_code,
                },
                sort_keys=True,
            ),
        )

    def _mark_source_document_failed(self, source_document_id: Optional[int]) -> None:
        if source_document_id is None:
            return
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.source_documents.update_status(
                source_document_id=source_document_id,
                status="failed",
            )
