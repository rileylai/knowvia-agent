from __future__ import annotations

import asyncio
import hashlib
import json
from io import BytesIO
from typing import Optional

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument, WorkflowRun
from src.db.unit_of_work import SqlAlchemyUnitOfWork
from src.orchestrators import (
    ImageOCRIngestionError,
    ImageOCRIngestionOrchestrator,
    ImageUploadInput,
)
from src.providers import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingRequest,
    EmbeddingResponse,
)
from src.rag import ProductionChunkRetriever
from src.repositories import ChunkRepository, SourceDocumentRepository
from src.services import EmbeddingBatchService, WorkflowRunService
from src.tools import (
    ImageOCRParserClient,
    ImageOCRParserClientError,
    ImageOCRTool,
    OCRImageInput,
    ParsedImageOCR,
    ToolRegistry,
)


class _FakeImageOCRParserClient(ImageOCRParserClient):
    def __init__(self, *, raw_text: str = "The screenshot says bounded execution.") -> None:
        self.raw_text = raw_text
        self.calls = 0

    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        self.calls += 1
        if len(images) > 1:
            return ParsedImageOCR(
                raw_text="\n\n".join(
                    f"[Image {index}: {image.file_name}]\n{self.raw_text}"
                    for index, image in enumerate(images, start=1)
                ),
                image_count=len(images),
            )
        return ParsedImageOCR(raw_text=self.raw_text, image_count=len(images))


class _FailingImageOCRParserClient(ImageOCRParserClient):
    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        _ = images
        raise ImageOCRParserClientError("tesseract unavailable")


class _EmptyImageOCRParserClient(ImageOCRParserClient):
    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        return ParsedImageOCR(raw_text="   ", image_count=len(images))


class _FirstImageWithoutHeadingParserClient(ImageOCRParserClient):
    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        return ParsedImageOCR(
            raw_text=(
                "[Image 1: fallback.png]\n---\n...\n"
                "[Image 2: body.png]\nUseful body text"
            ),
            image_count=len(images),
        )


class _OutOfOrderImageOCRParserClient(ImageOCRParserClient):
    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        sections = [
            f"[Image {index}: {image.file_name}]\n"
            f"OCR content for {image.file_name}"
            for index, image in enumerate(images, start=1)
        ]
        return ParsedImageOCR(raw_text="\n\n".join(reversed(sections)), image_count=len(images))


class _FakeEmbeddingClient(EmbeddingClient):
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.requests: list[EmbeddingRequest] = []

    @property
    def name(self) -> str:
        return "fake"

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> Optional[EmbeddingCapabilities]:
        return EmbeddingCapabilities(
            provider="fake",
            model=model,
            dimensions=dimensions,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        if self.should_fail:
            from src.providers import EmbeddingClientError

            raise EmbeddingClientError("provider unavailable")
        return EmbeddingResponse(
            provider="fake",
            model=request.model or "fake-embedding",
            embeddings=[[0.1] * 1536 for _ in request.inputs],
            indices=list(range(len(request.inputs))),
            token_input=len(request.inputs),
        )


def _build_session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            NotionPage.__table__,
            NotionBlock.__table__,
            SourceDocument.__table__,
            KnowledgeChunk.__table__,
            WorkflowRun.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _build_image_bytes(*, color: str = "white") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 18), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _build_orchestrator(session_factory, parser_client, embedding_client):
    registry = ToolRegistry()
    registry.register_tool(ImageOCRTool(parser_client))
    return ImageOCRIngestionOrchestrator(
        tool_registry=registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        workflow_run_service=WorkflowRunService(session_factory),
        embedding_batch_service=EmbeddingBatchService(
            embedding_client=embedding_client,
            model="text-embedding-3-small",
            dimensions=1536,
        ),
    )


def test_image_ingestion_indexes_ocr_text_through_generic_knowledge_pipeline() -> None:
    session_factory = _build_session_factory()
    embedding_client = _FakeEmbeddingClient()
    image_bytes = _build_image_bytes()
    orchestrator = _build_orchestrator(
        session_factory,
        _FakeImageOCRParserClient(),
        embedding_client,
    )

    result = asyncio.run(
        orchestrator.ingest_image_ocr(
            images=[
                ImageUploadInput(
                    file_name="architecture.png",
                    file_bytes=image_bytes,
                    mime_type="image/png",
                )
            ],
            request_workflow_id="wf-image-success",
        )
    )

    assert result.status == "succeeded"
    assert result.source_type == "image"
    assert result.index_status == "indexed"
    assert result.indexed_chunk_count == 1
    assert result.embedded_chunk_count == 1
    assert len(embedding_client.requests) == 1

    session: Session = session_factory()
    try:
        source_document = session.get(SourceDocument, result.source_document_id)
        assert source_document is not None
        assert source_document.source_type == "image"
        assert source_document.status == "indexed"
        assert source_document.file_hash == hashlib.sha256(image_bytes).hexdigest()
        assert source_document.original_filename == "architecture.png"

        chunk = session.query(KnowledgeChunk).one()
        assert chunk.source_kind == "image"
        assert chunk.eligibility_status == "eligible"
        assert chunk.locator == "Image 1 · chunk 1"
        citation = json.loads(chunk.citation_metadata or "{}")
        assert citation["original_filename"] == "architecture.png"
        assert citation["source_kind"] == "image"
        assert citation["source_display_name"] == (
            "Screenshot · The screenshot says bounded execution."
        )
        assert citation["locator"] == "Image 1 · chunk 1"
        assert citation["image_index"] == 1
        assert citation["sequence_index"] == 1
        assert citation["file_hash"] == hashlib.sha256(image_bytes).hexdigest()

        retrieved = ProductionChunkRetriever(
            chunk_repository=ChunkRepository(session)
        ).retrieve(query_text="bounded execution", source_kinds=["image"], top_k=3)
        assert len(retrieved) == 1
        assert retrieved[0].source_kind == "image"
        assert retrieved[0].locator == "Image 1 · chunk 1"
    finally:
        session.close()


def test_exact_duplicate_image_reuses_indexed_source_without_reprocessing() -> None:
    session_factory = _build_session_factory()
    parser_client = _FakeImageOCRParserClient()
    embedding_client = _FakeEmbeddingClient()
    image_bytes = _build_image_bytes()
    orchestrator = _build_orchestrator(
        session_factory,
        parser_client,
        embedding_client,
    )

    first = asyncio.run(
        orchestrator.ingest_image_ocr(
            images=[
                ImageUploadInput(
                    file_name="architecture.png",
                    file_bytes=image_bytes,
                    mime_type="image/png",
                )
            ],
            request_workflow_id="wf-image-first",
        )
    )
    second = asyncio.run(
        orchestrator.ingest_image_ocr(
            images=[
                ImageUploadInput(
                    file_name="renamed-architecture.png",
                    file_bytes=image_bytes,
                    mime_type="image/png",
                )
            ],
            request_workflow_id="wf-image-duplicate",
        )
    )

    assert first.status == "succeeded"
    assert second.status == "already_indexed"
    assert second.source_document_id == first.source_document_id
    assert second.source_display_name == "Screenshot · The screenshot says bounded execution."
    assert second.indexed_chunk_count == 1
    assert parser_client.calls == 1
    assert len(embedding_client.requests) == 1

    session: Session = session_factory()
    try:
        assert session.query(SourceDocument).count() == 1
        assert session.query(KnowledgeChunk).count() == 1
    finally:
        session.close()


def test_same_filename_with_different_image_bytes_creates_new_indexed_source() -> None:
    session_factory = _build_session_factory()
    parser_client = _FakeImageOCRParserClient()
    embedding_client = _FakeEmbeddingClient()
    first_bytes = _build_image_bytes()
    second_bytes = _build_image_bytes(color="lightblue")
    orchestrator = _build_orchestrator(
        session_factory,
        parser_client,
        embedding_client,
    )

    first = asyncio.run(
        orchestrator.ingest_image_ocr(
            images=[
                ImageUploadInput(
                    file_name="architecture.png",
                    file_bytes=first_bytes,
                    mime_type="image/png",
                )
            ],
            request_workflow_id="wf-image-a",
        )
    )
    second = asyncio.run(
        orchestrator.ingest_image_ocr(
            images=[
                ImageUploadInput(
                    file_name="architecture.png",
                    file_bytes=second_bytes,
                    mime_type="image/png",
                )
            ],
            request_workflow_id="wf-image-b",
        )
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert second.source_document_id != first.source_document_id
    assert parser_client.calls == 2
    assert len(embedding_client.requests) == 2

    session: Session = session_factory()
    try:
        assert session.query(SourceDocument).count() == 2
        assert session.query(KnowledgeChunk).count() == 2
    finally:
        session.close()


def test_image_batch_indexes_as_one_ordered_source_with_per_image_citations() -> None:
    session_factory = _build_session_factory()
    parser_client = _FakeImageOCRParserClient(raw_text="Title line")
    embedding_client = _FakeEmbeddingClient()
    orchestrator = _build_orchestrator(
        session_factory,
        parser_client,
        embedding_client,
    )

    result = asyncio.run(
        orchestrator.ingest_image_ocr_batch(
            images=[
                ImageUploadInput(
                    file_name="first.png",
                    file_bytes=_build_image_bytes(),
                    mime_type="image/png",
                ),
                ImageUploadInput(
                    file_name="second.png",
                    file_bytes=_build_image_bytes(color="lightblue"),
                    mime_type="image/png",
                ),
            ],
            request_workflow_id="wf-image-batch",
        )
    )

    assert result.status == "succeeded"
    assert result.source_display_name == "Screenshots · Title line"
    assert result.source_preview is not None
    assert len(result.image_results) == 2
    assert [item.status for item in result.image_results] == [
        "succeeded",
        "succeeded",
    ]
    assert result.indexed_chunk_count == 2
    assert result.embedded_chunk_count == 2
    assert parser_client.calls == 1

    session: Session = session_factory()
    try:
        sources = session.query(SourceDocument).order_by(SourceDocument.id).all()
        assert len(sources) == 1
        assert sources[0].original_filename is None
        metadata = json.loads(sources[0].source_metadata or "{}")
        assert [item["sequence_index"] for item in metadata["images"]] == [1, 2]
        assert [item["original_filename"] for item in metadata["images"]] == [
            "first.png",
            "second.png",
        ]
        assert all(len(item["file_hash"]) == 64 for item in metadata["images"])
        chunks = session.query(KnowledgeChunk).order_by(KnowledgeChunk.chunk_index).all()
        assert len(chunks) == 2
        assert [chunk.locator for chunk in chunks] == [
            "Image 1 · chunk 1",
            "Image 2 · chunk 1",
        ]
        citations = [json.loads(chunk.citation_metadata or "{}") for chunk in chunks]
        assert [citation["sequence_index"] for citation in citations] == [1, 2]
        assert [citation["image_index"] for citation in citations] == [1, 2]
        assert [citation["original_filename"] for citation in citations] == [
            "first.png",
            "second.png",
        ]
    finally:
        session.close()


def test_image_batch_fixes_source_order_before_out_of_order_ocr_completion() -> None:
    session_factory = _build_session_factory()
    orchestrator = _build_orchestrator(
        session_factory,
        _OutOfOrderImageOCRParserClient(),
        _FakeEmbeddingClient(),
    )

    result = asyncio.run(
        orchestrator.ingest_image_ocr_batch(
            images=[
                ImageUploadInput(
                    file_name="01.png",
                    file_bytes=_build_image_bytes(),
                    mime_type="image/png",
                ),
                ImageUploadInput(
                    file_name="02.png",
                    file_bytes=_build_image_bytes(color="lightblue"),
                    mime_type="image/png",
                ),
            ],
            request_workflow_id="wf-image-order",
        )
    )

    assert result.status == "succeeded"
    session: Session = session_factory()
    try:
        source = session.query(SourceDocument).one()
        assert source.raw_text.index("[Image 1: 01.png]") < source.raw_text.index(
            "[Image 2: 02.png]"
        )
        chunks = session.query(KnowledgeChunk).order_by(KnowledgeChunk.chunk_index).all()
        assert [chunk.locator for chunk in chunks] == [
            "Image 1 · chunk 1",
            "Image 2 · chunk 1",
        ]
    finally:
        session.close()


def test_grouped_image_batch_exact_dedup_uses_ordered_image_hashes() -> None:
    session_factory = _build_session_factory()
    parser_client = _FakeImageOCRParserClient(raw_text="Heading")
    orchestrator = _build_orchestrator(
        session_factory,
        parser_client,
        _FakeEmbeddingClient(),
    )
    first_images = [
        ImageUploadInput("first.png", _build_image_bytes(), "image/png"),
        ImageUploadInput("second.png", _build_image_bytes(color="lightblue"), "image/png"),
    ]

    first = asyncio.run(
        orchestrator.ingest_image_ocr_batch(
            images=first_images,
            request_workflow_id="wf-batch-first",
        )
    )
    duplicate = asyncio.run(
        orchestrator.ingest_image_ocr_batch(
            images=[
                ImageUploadInput("renamed-a.png", first_images[0].file_bytes, "image/png"),
                ImageUploadInput("renamed-b.png", first_images[1].file_bytes, "image/png"),
            ],
            request_workflow_id="wf-batch-duplicate",
        )
    )
    reordered = asyncio.run(
        orchestrator.ingest_image_ocr_batch(
            images=[first_images[1], first_images[0]],
            request_workflow_id="wf-batch-reordered",
        )
    )

    assert first.status == "succeeded"
    assert duplicate.status == "already_indexed"
    assert duplicate.source_document_id == first.source_document_id
    assert reordered.status == "succeeded"
    assert reordered.source_document_id != first.source_document_id
    assert parser_client.calls == 2

    session: Session = session_factory()
    try:
        assert session.query(SourceDocument).count() == 2
    finally:
        session.close()


def test_image_display_name_uses_first_meaningful_ocr_line_and_normalizes_whitespace() -> None:
    session_factory = _build_session_factory()
    orchestrator = _build_orchestrator(
        session_factory,
        _FakeImageOCRParserClient(
            raw_text="\n[Image 1: source.png]\n  Context   Engineering 具體例子  \n壓縮..."
        ),
        _FakeEmbeddingClient(),
    )

    result = asyncio.run(
        orchestrator.ingest_image_ocr(
            images=[
                ImageUploadInput(
                    file_name="Snipaste_2026-09-05_13-42-38.png",
                    file_bytes=_build_image_bytes(),
                    mime_type="image/png",
                )
            ],
            request_workflow_id="wf-image-title",
        )
    )

    assert result.source_display_name == "Screenshot · Context Engineering 具體例子"
    assert result.original_filename == "Snipaste_2026-09-05_13-42-38.png"


def test_image_display_name_is_bounded_and_falls_back_to_filename() -> None:
    session_factory = _build_session_factory()
    long_line = "Context " + ("Engineering " * 40)
    title_orchestrator = _build_orchestrator(
        session_factory,
        _FakeImageOCRParserClient(raw_text=long_line),
        _FakeEmbeddingClient(),
    )
    fallback_orchestrator = _build_orchestrator(
        session_factory,
        _FirstImageWithoutHeadingParserClient(),
        _FakeEmbeddingClient(),
    )

    title_result = asyncio.run(
        title_orchestrator.ingest_image_ocr(
            images=[
                ImageUploadInput(
                    file_name="long-title.png",
                    file_bytes=_build_image_bytes(),
                    mime_type="image/png",
                )
            ],
            request_workflow_id="wf-image-title-bounded",
        )
    )
    fallback_result = asyncio.run(
        fallback_orchestrator.ingest_image_ocr_batch(
            images=[
                ImageUploadInput(
                    file_name="fallback.png",
                    file_bytes=_build_image_bytes(color="lightblue"),
                    mime_type="image/png",
                ),
                ImageUploadInput(
                    file_name="body.png",
                    file_bytes=_build_image_bytes(color="lightgray"),
                    mime_type="image/png",
                ),
            ],
            request_workflow_id="wf-image-title-fallback",
        )
    )

    assert len(title_result.source_display_name) <= len("Screenshot · ") + 48
    assert title_result.source_display_name.startswith("Screenshot · Context Engineering")
    assert fallback_result.source_display_name == "Screenshots · fallback"


def test_image_display_name_uses_bounded_sentence_and_preview_after_title() -> None:
    session_factory = _build_session_factory()
    orchestrator = _build_orchestrator(
        session_factory,
        _FakeImageOCRParserClient(
            raw_text=(
                "[Image 1: sentence.png]\n"
                "每答完一題，就叫模型寫張小抄記下心得；下次答題時就能快速回顧。\n"
                "後續 OCR 內容應該成為 preview，而不是重複 title。"
            )
        ),
        _FakeEmbeddingClient(),
    )

    result = asyncio.run(
        orchestrator.ingest_image_ocr(
            images=[
                ImageUploadInput(
                    file_name="sentence.png",
                    file_bytes=_build_image_bytes(),
                    mime_type="image/png",
                )
            ],
            request_workflow_id="wf-image-sentence-title",
        )
    )

    assert len(result.source_display_name) <= len("Screenshot · ") + 48
    assert result.source_display_name.startswith("Screenshot · 每答完一題")
    assert result.source_preview == "後續 OCR 內容應該成為 preview，而不是重複 title。"
    assert result.source_display_name.removeprefix("Screenshot · ") not in (
        result.source_preview or ""
    )


def test_grouped_image_batch_reports_one_source_failure() -> None:
    session_factory = _build_session_factory()
    parser_client = _SelectiveImageOCRParserClient(failing_file_name="bad.png")
    orchestrator = _build_orchestrator(
        session_factory,
        parser_client,
        _FakeEmbeddingClient(),
    )

    with pytest.raises(ImageOCRIngestionError) as error:
        asyncio.run(
            orchestrator.ingest_image_ocr_batch(
                images=[
                    ImageUploadInput(
                        file_name="good.png",
                        file_bytes=_build_image_bytes(),
                        mime_type="image/png",
                    ),
                    ImageUploadInput(
                        file_name="bad.png",
                        file_bytes=_build_image_bytes(color="lightblue"),
                        mime_type="image/png",
                    ),
                ],
                request_workflow_id="wf-image-batch-mixed",
            )
        )

    assert error.value.error_code == "OCR_FAILED"


class _SelectiveImageOCRParserClient(ImageOCRParserClient):
    def __init__(self, *, failing_file_name: str) -> None:
        self.failing_file_name = failing_file_name

    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        if any(image.file_name == self.failing_file_name for image in images):
            raise ImageOCRParserClientError("ocr failed")
        return ParsedImageOCR(
            raw_text="\n\n".join(
                f"[Image {index}: {image.file_name}]\nGood image title"
                for index, image in enumerate(images, start=1)
            ),
            image_count=len(images),
        )


def test_embedding_failure_marks_image_source_failed_without_eligible_chunks() -> None:
    session_factory = _build_session_factory()
    orchestrator = _build_orchestrator(
        session_factory,
        _FakeImageOCRParserClient(),
        _FakeEmbeddingClient(should_fail=True),
    )

    try:
        asyncio.run(
            orchestrator.ingest_image_ocr_batch(
                images=[
                    ImageUploadInput(
                        file_name="provider-failure.png",
                        file_bytes=_build_image_bytes(),
                        mime_type="image/png",
                    )
                ],
                request_workflow_id="wf-image-embedding-failure",
            )
        )
    except ImageOCRIngestionError as exc:
        assert exc.error_code == "EMBEDDING_PROVIDER_ERROR"
        assert exc.failure_reason == "EMBEDDING_PROVIDER_ERROR"
    else:
        raise AssertionError("expected embedding failure")

    session: Session = session_factory()
    try:
        source_document = session.query(SourceDocument).one()
        assert source_document.status == "failed"
        assert session.query(KnowledgeChunk).count() == 0
        workflow_run = session.query(WorkflowRun).one()
        assert workflow_run.status == "failed"
    finally:
        session.close()


def test_empty_grouped_ocr_result_fails_closed_without_source_or_chunks() -> None:
    session_factory = _build_session_factory()
    orchestrator = _build_orchestrator(
        session_factory,
        _EmptyImageOCRParserClient(),
        _FakeEmbeddingClient(),
    )

    with pytest.raises(ImageOCRIngestionError) as exc_info:
        asyncio.run(
            orchestrator.ingest_image_ocr_batch(
                images=[
                    ImageUploadInput(
                        file_name="empty.png",
                        file_bytes=_build_image_bytes(),
                        mime_type="image/png",
                    ),
                    ImageUploadInput(
                        file_name="empty-2.png",
                        file_bytes=_build_image_bytes(color="gray"),
                        mime_type="image/png",
                    ),
                ],
                request_workflow_id="wf-image-empty-ocr",
            )
        )

    assert exc_info.value.error_code == "OCR_FAILED"
    assert exc_info.value.failure_reason == "OCR_FAILED"
    assert str(exc_info.value) == "No extractable text found in images"

    session: Session = session_factory()
    try:
        assert session.query(SourceDocument).count() == 0
        assert session.query(KnowledgeChunk).count() == 0
        workflow_run = session.query(WorkflowRun).one()
        assert workflow_run.status == "failed"
    finally:
        session.close()


def test_corrupted_image_is_rejected_before_ocr() -> None:
    session_factory = _build_session_factory()
    parser_client = _FakeImageOCRParserClient()
    orchestrator = _build_orchestrator(
        session_factory,
        parser_client,
        _FakeEmbeddingClient(),
    )

    try:
        asyncio.run(
            orchestrator.ingest_image_ocr(
                images=[
                    ImageUploadInput(
                        file_name="corrupted.png",
                        file_bytes=b"not an image",
                        mime_type="image/png",
                    )
                ],
                request_workflow_id="wf-image-corrupted",
            )
        )
    except ImageOCRIngestionError as exc:
        assert exc.error_code == "INVALID_IMAGE"
    else:
        raise AssertionError("expected corrupted image failure")

    assert parser_client.calls == 0


def test_over_pixel_image_is_rejected_before_ocr(monkeypatch) -> None:
    session_factory = _build_session_factory()
    parser_client = _FakeImageOCRParserClient()
    orchestrator = _build_orchestrator(
        session_factory,
        parser_client,
        _FakeEmbeddingClient(),
    )
    monkeypatch.setattr("src.services.upload_limits.MAX_IMAGE_PIXELS", 1)

    try:
        asyncio.run(
            orchestrator.ingest_image_ocr(
                images=[
                    ImageUploadInput(
                        file_name="too-many-pixels.png",
                        file_bytes=_build_image_bytes(),
                        mime_type="image/png",
                    )
                ],
                request_workflow_id="wf-image-pixels",
            )
        )
    except ImageOCRIngestionError as exc:
        assert exc.error_code == "IMAGE_PIXEL_LIMIT_EXCEEDED"
    else:
        raise AssertionError("expected pixel limit failure")

    assert parser_client.calls == 0


def test_image_source_is_visible_in_generic_indexed_source_inventory() -> None:
    session_factory = _build_session_factory()
    session: Session = session_factory()
    try:
        source = SourceDocument(
            id=1,
            source_type="image",
            source_display_name="architecture.png",
            content_hash="ocr-content-hash",
            file_hash="raw-file-hash",
            raw_text="private OCR text",
            owner_scope="local",
            status="indexed",
        )
        session.add(source)
        session.add(
            KnowledgeChunk(
                id=1,
                source_document_id=1,
                source_kind="image",
                source_display_name="architecture.png",
                chunk_index=0,
                chunk_text="private OCR chunk",
                eligibility_status="eligible",
                owner_scope="local",
            )
        )
        session.commit()

        summaries = SourceDocumentRepository(session).list_indexed_sources(
            owner_scope="local"
        )

        assert len(summaries) == 1
        assert summaries[0].source_kind == "image"
        assert summaries[0].display_name == "architecture.png"
        assert summaries[0].chunk_count == 1
    finally:
        session.close()
