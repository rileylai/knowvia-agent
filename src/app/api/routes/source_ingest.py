from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from src.app.dependencies import (
    build_embedding_batch_service,
    get_business_unit_of_work_factory,
    get_cost_tracker,
    get_embedding_client,
    get_tool_registry,
)
from src.app.schemas import (
    ChatTextIngestionRequest,
    SourceDocumentCreateRequest,
    SourceDocumentCreateResponse,
    YouTubeIngestionRequest,
    URLIngestionRequest,
)
from src.db.session import (
    SessionFactory,
    UnitOfWorkFactory,
    get_db_session_factory,
)
from src.orchestrators import (
    ChatTextIngestionError,
    ChatTextIngestionOrchestrator,
    DocumentIngestionError,
    DocumentIngestionOrchestrator,
    ImageOCRIngestionError,
    ImageOCRIngestionOrchestrator,
    ImageUploadInput,
    SourceDocumentOrchestrator,
    SourceDocumentWorkflowError,
    YouTubeIngestionError,
    YouTubeIngestionOrchestrator,
    URLIngestionError,
    URLIngestionOrchestrator,
)
from src.providers import EmbeddingClient
from src.services import (
    MAX_OCR_IMAGE_BYTES,
    MAX_OCR_IMAGE_COUNT,
    MAX_OCR_TOTAL_BYTES,
    MAX_PDF_BYTES,
    CostTracker,
    UploadValidationError,
    WorkflowRunService,
    validate_file_bytes,
    validate_image_metadata,
    validate_ocr_batch,
    validate_pdf_metadata,
    upload_error_http_status,
)
from src.tools import ToolRegistry

router = APIRouter()


def _upload_http_exception(exc: UploadValidationError) -> HTTPException:
    return HTTPException(
        status_code=upload_error_http_status(exc.error_code),
        detail={
            "error_code": exc.error_code,
            "message": exc.message,
            "failure_reason": exc.failure_reason,
            "workflow_run_id": None,
        },
    )


async def _read_upload_with_limit(
    upload: UploadFile,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    file_bytes = await upload.read(maximum_bytes + 1)
    validate_file_bytes(
        file_bytes=file_bytes,
        maximum_bytes=maximum_bytes,
        label=label,
    )
    return file_bytes


def _build_source_document_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
) -> SourceDocumentOrchestrator:
    return SourceDocumentOrchestrator(
        unit_of_work_factory=unit_of_work_factory,
        workflow_run_service=WorkflowRunService(db_session_factory),
    )


def _build_chat_text_ingestion_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
) -> ChatTextIngestionOrchestrator:
    return ChatTextIngestionOrchestrator(
        unit_of_work_factory=unit_of_work_factory,
        workflow_run_service=WorkflowRunService(db_session_factory),
    )


def _build_document_ingestion_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    tool_registry: ToolRegistry,
    embedding_client: Optional[EmbeddingClient],
    cost_tracker: CostTracker,
) -> DocumentIngestionOrchestrator:
    return DocumentIngestionOrchestrator(
        tool_registry=tool_registry,
        unit_of_work_factory=unit_of_work_factory,
        workflow_run_service=WorkflowRunService(db_session_factory),
        embedding_batch_service=build_embedding_batch_service(embedding_client),
        cost_tracker=cost_tracker,
    )


def _build_image_ocr_ingestion_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    tool_registry: ToolRegistry,
) -> ImageOCRIngestionOrchestrator:
    return ImageOCRIngestionOrchestrator(
        tool_registry=tool_registry,
        unit_of_work_factory=unit_of_work_factory,
        workflow_run_service=WorkflowRunService(db_session_factory),
    )


def _build_url_ingestion_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    tool_registry: ToolRegistry,
) -> URLIngestionOrchestrator:
    return URLIngestionOrchestrator(
        tool_registry=tool_registry,
        unit_of_work_factory=unit_of_work_factory,
        workflow_run_service=WorkflowRunService(db_session_factory),
    )


def _build_youtube_ingestion_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    tool_registry: ToolRegistry,
) -> YouTubeIngestionOrchestrator:
    return YouTubeIngestionOrchestrator(
        tool_registry=tool_registry,
        unit_of_work_factory=unit_of_work_factory,
        workflow_run_service=WorkflowRunService(db_session_factory),
    )


@router.post("/api/ingest/source", response_model=SourceDocumentCreateResponse)
async def create_source_document(
    payload: SourceDocumentCreateRequest,
    request: Request,
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
) -> SourceDocumentCreateResponse:
    orchestrator = _build_source_document_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        result = await orchestrator.create_source_document(
            source_type=payload.source_type,
            source_display_name=payload.source_display_name,
            raw_text=payload.raw_text,
            request_workflow_id=request_workflow_id,
        )
    except SourceDocumentWorkflowError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": exc.workflow_run_id,
            },
        ) from exc

    return SourceDocumentCreateResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        source_document_id=result.source_document_id,
        source_type=result.source_type,
        source_display_name=result.source_display_name,
        content_hash=result.content_hash,
    )


@router.post("/api/ingest/url", response_model=SourceDocumentCreateResponse)
async def ingest_url_article(
    payload: URLIngestionRequest,
    request: Request,
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> SourceDocumentCreateResponse:
    orchestrator = _build_url_ingestion_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        tool_registry=tool_registry,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        result = await orchestrator.ingest_url(
            url=payload.url,
            request_workflow_id=request_workflow_id,
        )
    except URLIngestionError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": exc.workflow_run_id,
            },
        ) from exc

    return SourceDocumentCreateResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        source_document_id=result.source_document_id,
        source_type=result.source_type,
        source_display_name=result.source_display_name,
        content_hash=result.content_hash,
    )


@router.post("/api/ingest/document", response_model=SourceDocumentCreateResponse)
async def ingest_pdf_document(
    request: Request,
    document: UploadFile = File(...),
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
    cost_tracker: CostTracker = Depends(get_cost_tracker),
) -> SourceDocumentCreateResponse:
    orchestrator = _build_document_ingestion_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        tool_registry=tool_registry,
        embedding_client=embedding_client,
        cost_tracker=cost_tracker,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        try:
            validate_pdf_metadata(
                file_name=document.filename or "",
                mime_type=document.content_type,
            )
            document_bytes = await _read_upload_with_limit(
                document,
                maximum_bytes=MAX_PDF_BYTES,
                label="Uploaded document",
            )
        except UploadValidationError as exc:
            raise _upload_http_exception(exc) from exc
        result = await orchestrator.ingest_document(
            file_name=document.filename or "",
            file_bytes=document_bytes,
            mime_type=document.content_type,
            request_workflow_id=request_workflow_id,
        )
    except DocumentIngestionError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": exc.workflow_run_id,
            },
        ) from exc
    finally:
        await document.close()

    return SourceDocumentCreateResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        source_document_id=result.source_document_id,
        source_type=result.source_type,
        source_display_name=result.source_display_name,
        content_hash=result.content_hash,
        index_status=result.index_status,
        indexed_chunk_count=result.indexed_chunk_count,
        embedded_chunk_count=result.embedded_chunk_count,
    )


@router.post("/api/ingest/youtube", response_model=SourceDocumentCreateResponse)
async def ingest_youtube_transcript(
    payload: YouTubeIngestionRequest,
    request: Request,
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> SourceDocumentCreateResponse:
    orchestrator = _build_youtube_ingestion_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        tool_registry=tool_registry,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        result = await orchestrator.ingest_youtube(
            url=payload.url,
            request_workflow_id=request_workflow_id,
        )
    except YouTubeIngestionError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": exc.workflow_run_id,
            },
        ) from exc

    return SourceDocumentCreateResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        source_document_id=result.source_document_id,
        source_type=result.source_type,
        source_display_name=result.source_display_name,
        content_hash=result.content_hash,
    )


@router.post("/api/ingest/chat-text", response_model=SourceDocumentCreateResponse)
async def ingest_chat_text(
    payload: ChatTextIngestionRequest,
    request: Request,
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
) -> SourceDocumentCreateResponse:
    orchestrator = _build_chat_text_ingestion_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        result = await orchestrator.ingest_chat_text(
            chat_text=payload.chat_text,
            source_display_name=payload.source_display_name,
            request_workflow_id=request_workflow_id,
        )
    except ChatTextIngestionError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": exc.workflow_run_id,
            },
        ) from exc

    return SourceDocumentCreateResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        source_document_id=result.source_document_id,
        source_type=result.source_type,
        source_display_name=result.source_display_name,
        content_hash=result.content_hash,
    )


@router.post("/api/ingest/image-ocr", response_model=SourceDocumentCreateResponse)
async def ingest_image_ocr(
    request: Request,
    images: list[UploadFile] = File(...),
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_business_unit_of_work_factory),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> SourceDocumentCreateResponse:
    orchestrator = _build_image_ocr_ingestion_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        tool_registry=tool_registry,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    image_inputs: list[ImageUploadInput] = []
    try:
        if len(images) > MAX_OCR_IMAGE_COUNT:
            raise _upload_http_exception(
                UploadValidationError(
                    error_code="UPLOAD_LIMIT_EXCEEDED",
                    message=(
                        f"OCR image count exceeds the {MAX_OCR_IMAGE_COUNT} image limit"
                    ),
                    failure_reason="UPLOAD_LIMIT_EXCEEDED",
                )
            )
        total_bytes = 0
        for index, image in enumerate(images, start=1):
            try:
                validate_image_metadata(mime_type=image.content_type)
                image_bytes = await _read_upload_with_limit(
                    image,
                    maximum_bytes=MAX_OCR_IMAGE_BYTES,
                    label=f"images[{index}]",
                )
            except UploadValidationError as exc:
                raise _upload_http_exception(exc) from exc
            total_bytes += len(image_bytes)
            try:
                validate_ocr_batch(
                    image_count=len(image_inputs) + 1,
                    total_bytes=total_bytes,
                )
            except UploadValidationError as exc:
                raise _upload_http_exception(exc) from exc
            image_file_name = (image.filename or "").strip() or f"image-{index}"
            image_inputs.append(
                ImageUploadInput(
                    file_name=image_file_name,
                    file_bytes=image_bytes,
                    mime_type=image.content_type,
                )
            )

        result = await orchestrator.ingest_image_ocr(
            images=image_inputs,
            request_workflow_id=request_workflow_id,
        )
    except ImageOCRIngestionError as exc:
        raise HTTPException(
            status_code=exc.http_status_code,
            detail={
                "error_code": exc.error_code,
                "message": exc.message,
                "failure_reason": exc.failure_reason,
                "workflow_run_id": exc.workflow_run_id,
            },
        ) from exc
    finally:
        for image in images:
            await image.close()

    return SourceDocumentCreateResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        source_document_id=result.source_document_id,
        source_type=result.source_type,
        source_display_name=result.source_display_name,
        content_hash=result.content_hash,
    )
