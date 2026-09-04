from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.dependencies import get_embedding_client, get_tool_registry
from src.app.main import app
from src.db.base import Base
from src.db.models import KnowledgeChunk, NotionBlock, NotionPage, SourceDocument, WorkflowRun
from src.db.session import get_db_session, get_db_session_factory
from src.orchestrators import MVP_CHAT_TEXT_MAX_CHARS
from src.services.screenshot_quality import (
    build_screenshot_source_snapshot,
    detect_screenshot_language,
    preprocess_screenshot_ocr_text,
)
from src.tools import (
    ImageOCRParserClient,
    ImageOCRParserClientError,
    ImageOCRTool,
    OCRImageInput,
    PDFParserClient,
    PDFParserClientError,
    PDFParserTool,
    ParsedImageOCR,
    ParsedURLArticle,
    ParsedYouTubeTranscript,
    ToolRegistry,
    URLArticleParserClient,
    URLArticleParserClientError,
    URLArticleParserTool,
    YouTubeTranscriptParserClient,
    YouTubeTranscriptParserClientError,
    YouTubeTranscriptTool,
)
from src.providers import (
    EmbeddingCapabilities,
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    EmbeddingResponse,
)


@dataclass
class _FakeParsedPDF:
    raw_text: str
    page_count: int


class _FakePDFParserClient(PDFParserClient):
    def __init__(
        self,
        *,
        raw_text: str = "",
        page_count: int = 1,
        should_fail: bool = False,
        failure_message: str = "parse failed",
    ) -> None:
        self._raw_text = raw_text
        self._page_count = page_count
        self._should_fail = should_fail
        self._failure_message = failure_message

    def parse_document(self, *, file_name: str, file_bytes: bytes):
        _ = file_name
        _ = file_bytes
        if self._should_fail:
            raise PDFParserClientError(self._failure_message)
        return _FakeParsedPDF(raw_text=self._raw_text, page_count=self._page_count)


class _FakeEmbeddingClient(EmbeddingClient):
    def __init__(self, *, should_fail: bool = False) -> None:
        self._should_fail = should_fail

    @property
    def name(self) -> str:
        return "openai"

    def get_capabilities(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> EmbeddingCapabilities:
        return EmbeddingCapabilities(
            provider="openai",
            model=model,
            dimensions=dimensions,
            max_input_count=2048,
            max_single_input_tokens=8192,
            max_aggregate_tokens=300000,
            tokenizer_model=model,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if self._should_fail:
            raise EmbeddingClientError("embedding provider unavailable")
        return EmbeddingResponse(
            provider="openai",
            model=request.model or "text-embedding-3-small",
            embeddings=[[0.1] * 1536 for _ in request.inputs],
            indices=list(range(len(request.inputs))),
            token_input=len(request.inputs),
        )


class _FakeURLArticleParserClient(URLArticleParserClient):
    def __init__(
        self,
        *,
        raw_text: str = "",
        should_fail: bool = False,
        failure_message: str = "fetch failed",
    ) -> None:
        self._raw_text = raw_text
        self._should_fail = should_fail
        self._failure_message = failure_message

    def parse_article(self, *, url: str) -> ParsedURLArticle:
        if self._should_fail:
            raise URLArticleParserClientError(self._failure_message)
        return ParsedURLArticle(url=url, raw_text=self._raw_text)


class _FakeYouTubeTranscriptParserClient(YouTubeTranscriptParserClient):
    def __init__(
        self,
        *,
        video_id: str = "dQw4w9WgXcQ",
        source_display_name: str = "YouTube transcript (dQw4w9WgXcQ)",
        raw_text: str = "",
        should_fail: bool = False,
        failure_message: str = "transcript not found",
    ) -> None:
        self._video_id = video_id
        self._source_display_name = source_display_name
        self._raw_text = raw_text
        self._should_fail = should_fail
        self._failure_message = failure_message

    def parse_transcript(self, *, url: str) -> ParsedYouTubeTranscript:
        _ = url
        if self._should_fail:
            raise YouTubeTranscriptParserClientError(self._failure_message)
        return ParsedYouTubeTranscript(
            video_id=self._video_id,
            source_display_name=self._source_display_name,
            raw_text=self._raw_text,
        )


class _FakeImageOCRParserClient(ImageOCRParserClient):
    def __init__(
        self,
        *,
        should_fail: bool = False,
        failure_message: str = "ocr failed",
    ) -> None:
        self._should_fail = should_fail
        self._failure_message = failure_message

    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        if self._should_fail:
            raise ImageOCRParserClientError(self._failure_message)
        ordered_lines = [
            f"OCR[{index}] {image.file_name}"
            for index, image in enumerate(images, start=1)
        ]
        return ParsedImageOCR(
            raw_text="\n".join(ordered_lines),
            image_count=len(images),
        )


class _MixedScriptImageOCRParserClient(ImageOCRParserClient):
    def __init__(self) -> None:
        self.raw_text = ""

    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        sections = []
        for index, image in enumerate(images, start=1):
            sections.append(
                f"[Image {index}: {image.file_name}]\n"
                "SQL 查詢與索引\n"
                "Back\n"
                "EXPLAIN 顯示執行計畫"
            )
        self.raw_text = "\n\n".join(sections)
        return ParsedImageOCR(raw_text=self.raw_text, image_count=len(images))


def _count_cjk(value: str) -> int:
    return sum(
        0x3400 <= ord(character) <= 0x4DBF
        or 0x4E00 <= ord(character) <= 0x9FFF
        for character in value
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
            SourceDocument.__table__,
            KnowledgeChunk.__table__,
            WorkflowRun.__table__,
            NotionPage.__table__,
            NotionBlock.__table__,
        ],
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _build_tool_registry_with_pdf_parser(parser_client: PDFParserClient) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(PDFParserTool(parser_client))
    return registry


def _build_tool_registry_with_url_parser(
    parser_client: URLArticleParserClient,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(URLArticleParserTool(parser_client))
    return registry


def _build_tool_registry_with_youtube_parser(
    parser_client: YouTubeTranscriptParserClient,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(YouTubeTranscriptTool(parser_client))
    return registry


def _build_tool_registry_with_image_ocr_parser(
    parser_client: ImageOCRParserClient,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(ImageOCRTool(parser_client))
    return registry


def test_ingest_source_api_creates_one_document_per_supported_source_type() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory

    try:
        client = TestClient(app)
        payloads = [
            ("pdf", "lecture1.pdf", "Transformer notes from lecture 1"),
            ("url", "https://example.com/nlp", "Article summary about attention"),
            ("youtube", "NLP Crash Course", "Transcript text for decoder block"),
            ("screenshot", "week5-screenshots", "OCR text from three screenshots"),
            ("chat_text", "chat-2026-05-24", "Pasted chat text with key concepts"),
        ]

        response_bodies = []
        for source_type, display_name, raw_text in payloads:
            response = client.post(
                "/api/ingest/source",
                json={
                    "source_type": source_type,
                    "source_display_name": display_name,
                    "raw_text": raw_text,
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "succeeded"
            assert body["source_type"] == source_type
            assert body["source_display_name"] == display_name
            assert body["content_hash"] == hashlib.sha256(
                raw_text.encode("utf-8")
            ).hexdigest()
            response_bodies.append(body)

        session: Session = session_factory()
        try:
            source_documents = session.query(SourceDocument).order_by(SourceDocument.id.asc()).all()
            assert len(source_documents) == len(payloads)
            assert {row.source_type for row in source_documents} == {
                "pdf",
                "url",
                "youtube",
                "screenshot",
                "chat_text",
            }

            workflow_runs = session.query(WorkflowRun).order_by(WorkflowRun.id.asc()).all()
            assert len(workflow_runs) == len(payloads)
            assert {row.workflow_type for row in workflow_runs} == {"ingestion"}
            assert {row.status for row in workflow_runs} == {"succeeded"}

            returned_source_ids = {body["source_document_id"] for body in response_bodies}
            persisted_source_ids = {row.id for row in source_documents}
            assert returned_source_ids == persisted_source_ids
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_source_api_rejects_unsupported_source_type() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory

    try:
        client = TestClient(app)
        response = client.post(
            "/api/ingest/source",
            json={
                "source_type": "audio",
                "source_display_name": "podcast-episode",
                "raw_text": "audio transcript text",
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["error_code"] == "INVALID_ARGUMENT"
        assert detail["failure_reason"] == "UNKNOWN_ERROR"

        session: Session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            assert session.query(WorkflowRun).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_source_api_rejects_blank_raw_text() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory

    try:
        client = TestClient(app)
        response = client.post(
            "/api/ingest/source",
            json={
                "source_type": "chat_text",
                "source_display_name": "chat-empty",
                "raw_text": "   ",
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["error_code"] == "INVALID_ARGUMENT"
        assert detail["message"] == "raw_text must not be empty"

        session: Session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            assert session.query(WorkflowRun).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_document_api_persists_extracted_pdf_text_and_filename() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_pdf_parser(
            _FakePDFParserClient(raw_text="Transformer attention summary", page_count=2)
        )

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = lambda: _FakeEmbeddingClient()

    try:
        client = TestClient(app)
        response = client.post(
            "/api/ingest/document",
            files={
                "document": (
                    "lecture-week5.pdf",
                    b"%PDF-1.4 fake-pdf-content",
                    "application/pdf",
                )
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["source_type"] == "pdf"
        assert payload["source_display_name"] == "lecture-week5.pdf"
        assert payload["content_hash"] == hashlib.sha256(
            "Transformer attention summary".encode("utf-8")
        ).hexdigest()

        session: Session = session_factory()
        try:
            source_document = session.get(SourceDocument, payload["source_document_id"])
            assert source_document is not None
            assert source_document.source_type == "pdf"
            assert source_document.source_display_name == "lecture-week5.pdf"
            assert source_document.raw_text == "Transformer attention summary"

            workflow_run = session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None

            # Step 21 must not perform Notion writes.
            assert session.query(NotionPage).count() == 0
            assert session.query(NotionBlock).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_document_api_returns_pdf_parse_failed() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_pdf_parser(
            _FakePDFParserClient(
                should_fail=True,
                failure_message="no extractable text",
            )
        )

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/ingest/document",
            files={
                "document": (
                    "bad.pdf",
                    b"%PDF-1.4 invalid",
                    "application/pdf",
                )
            },
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "PDF_PARSE_FAILED"
        assert detail["failure_reason"] == "PDF_PARSE_FAILED"
        assert detail["workflow_run_id"] is not None

        session: Session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            workflow_run = session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "PDF_PARSE_FAILED"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_document_api_returns_safe_error_when_embedding_fails() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_pdf_parser(
            _FakePDFParserClient(raw_text="Searchable PDF text", page_count=1)
        )

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = lambda: _FakeEmbeddingClient(
        should_fail=True
    )

    try:
        response = TestClient(app).post(
            "/api/ingest/document",
            files={
                "document": (
                    "provider-failure.pdf",
                    b"%PDF-1.4 fake-pdf-content",
                    "application/pdf",
                )
            },
        )

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert detail["error_code"] == "EMBEDDING_PROVIDER_ERROR"
        assert detail["failure_reason"] == "EMBEDDING_PROVIDER_ERROR"
        assert detail["workflow_run_id"] is not None

        session: Session = session_factory()
        try:
            source_document = session.query(SourceDocument).one()
            assert source_document.status == "failed"
            assert session.query(KnowledgeChunk).count() == 0
            workflow_run = session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.status == "failed"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_url_api_persists_extracted_text_and_full_url_display_name() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_url_parser(
            _FakeURLArticleParserClient(raw_text="Attention article summary")
        )

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override
    app.dependency_overrides[get_embedding_client] = lambda: _FakeEmbeddingClient()

    try:
        client = TestClient(app)
        source_url = "https://example.com/nlp-week5"
        response = client.post(
            "/api/ingest/url",
            json={"url": source_url},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["source_type"] == "url"
        assert payload["source_display_name"] == source_url
        assert payload["requested_url"] == source_url
        assert payload["final_url"] == source_url
        assert payload["index_status"] == "indexed"
        assert payload["indexed_chunk_count"] == 1
        assert payload["embedded_chunk_count"] == 1
        assert payload["content_hash"] == hashlib.sha256(
            "Attention article summary".encode("utf-8")
        ).hexdigest()

        session: Session = session_factory()
        try:
            source_document = session.get(SourceDocument, payload["source_document_id"])
            assert source_document is not None
            assert source_document.source_type == "url"
            assert source_document.source_display_name == source_url
            assert source_document.raw_text == "Attention article summary"
            assert source_document.requested_url == source_url
            assert source_document.final_url == source_url
            assert source_document.status == "indexed"
            assert session.query(KnowledgeChunk).count() == 1

            workflow_run = session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None

            # Step 22 must not perform Notion writes.
            assert session.query(NotionPage).count() == 0
            assert session.query(NotionBlock).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_url_api_returns_url_fetch_failed() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_url_parser(
            _FakeURLArticleParserClient(
                should_fail=True,
                failure_message="article fetch failed",
            )
        )

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/ingest/url",
            json={"url": "https://example.com/fail"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "URL_FETCH_FAILED"
        assert detail["failure_reason"] == "URL_FETCH_FAILED"
        assert detail["workflow_run_id"] is not None

        session: Session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            workflow_run = session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "URL_FETCH_FAILED"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_youtube_api_persists_transcript_with_source_display_name() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_youtube_parser(
            _FakeYouTubeTranscriptParserClient(
                source_display_name="YouTube transcript (dQw4w9WgXcQ)",
                raw_text="This is transcript content for attention notes.",
            )
        )

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        response = client.post(
            "/api/ingest/youtube",
            json={"url": youtube_url},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["source_type"] == "youtube"
        assert payload["source_display_name"] == "YouTube transcript (dQw4w9WgXcQ)"
        assert payload["content_hash"] == hashlib.sha256(
            "This is transcript content for attention notes.".encode("utf-8")
        ).hexdigest()

        session: Session = session_factory()
        try:
            source_document = session.get(SourceDocument, payload["source_document_id"])
            assert source_document is not None
            assert source_document.source_type == "youtube"
            assert (
                source_document.source_display_name
                == "YouTube transcript (dQw4w9WgXcQ)"
            )
            assert (
                source_document.raw_text
                == "This is transcript content for attention notes."
            )

            workflow_run = session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None

            # Step 23 must not perform Notion writes.
            assert session.query(NotionPage).count() == 0
            assert session.query(NotionBlock).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_youtube_api_returns_transcript_not_found() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_youtube_parser(
            _FakeYouTubeTranscriptParserClient(
                should_fail=True,
                failure_message="No transcript available",
            )
        )

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/ingest/youtube",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "YOUTUBE_TRANSCRIPT_NOT_FOUND"
        assert detail["failure_reason"] == "YOUTUBE_TRANSCRIPT_NOT_FOUND"
        assert detail["workflow_run_id"] is not None

        session: Session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            workflow_run = session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "YOUTUBE_TRANSCRIPT_NOT_FOUND"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_image_ocr_api_creates_one_screenshot_source_in_order() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_image_ocr_parser(_FakeImageOCRParserClient())

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/ingest/image-ocr",
            files=[
                ("images", ("slide-1.png", b"fake-image-1", "image/png")),
                ("images", ("slide-2.png", b"fake-image-2", "image/png")),
                ("images", ("slide-3.png", b"fake-image-3", "image/png")),
            ],
        )
        assert response.status_code == 200
        payload = response.json()
        expected_raw_text = "\n".join(
            [
                "OCR[1] slide-1.png",
                "OCR[2] slide-2.png",
                "OCR[3] slide-3.png",
            ]
        )
        assert payload["status"] == "succeeded"
        assert payload["source_type"] == "screenshot"
        assert payload["source_display_name"] == "Screenshot batch (3 images)"
        assert payload["content_hash"] == hashlib.sha256(
            expected_raw_text.encode("utf-8")
        ).hexdigest()

        session: Session = session_factory()
        try:
            source_document = session.get(SourceDocument, payload["source_document_id"])
            assert source_document is not None
            assert source_document.source_type == "screenshot"
            assert source_document.source_display_name == "Screenshot batch (3 images)"
            assert source_document.raw_text == expected_raw_text

            workflow_run = session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None
            workflow_metadata = json.loads(workflow_run.metadata_json or "{}")
            for latency_key in (
                "download_ms",
                "ocr_ms",
                "llm_ms",
                "persist_ms",
                "preview_delivery_ms",
                "total_business_ms",
            ):
                assert latency_key in workflow_metadata
                assert workflow_metadata[latency_key] >= 0

            # Step 24 must not perform Notion writes.
            assert session.query(NotionPage).count() == 0
            assert session.query(NotionBlock).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_image_ocr_preserves_mixed_script_cjk_through_snapshot() -> None:
    session_factory = _build_session_factory()
    parser = _MixedScriptImageOCRParserClient()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_image_ocr_parser(parser)

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        response = TestClient(app).post(
            "/api/ingest/image-ocr",
            files=[
                ("images", ("first.png", b"fake-image-1", "image/png")),
                ("images", ("second.png", b"fake-image-2", "image/png")),
            ],
        )
        assert response.status_code == 200
        payload = response.json()

        session: Session = session_factory()
        try:
            source_document = session.get(
                SourceDocument,
                payload["source_document_id"],
            )
            assert source_document is not None
            persisted_text = source_document.raw_text
            snapshot = build_screenshot_source_snapshot(persisted_text)
            language = detect_screenshot_language(snapshot.text)
            expected_text = preprocess_screenshot_ocr_text(parser.raw_text)

            assert persisted_text == expected_text
            assert _count_cjk(persisted_text) > 0
            assert _count_cjk(persisted_text) == _count_cjk(parser.raw_text)
            assert language.code in {"en", "zh-Hant", "ja", "ko", "ru", "ar"}
            assert language.code != "en"
            assert "Back" not in persisted_text
            assert persisted_text.index("[Image 1: first.png]") < persisted_text.index(
                "[Image 2: second.png]"
            )
            assert payload["content_hash"] == hashlib.sha256(
                persisted_text.encode("utf-8")
            ).hexdigest()
            assert snapshot.digest == payload["content_hash"]
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_image_ocr_api_returns_ocr_failed() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _tool_registry_override() -> ToolRegistry:
        return _build_tool_registry_with_image_ocr_parser(
            _FakeImageOCRParserClient(
                should_fail=True,
                failure_message="tesseract unavailable",
            )
        )

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory
    app.dependency_overrides[get_tool_registry] = _tool_registry_override

    try:
        client = TestClient(app)
        response = client.post(
            "/api/ingest/image-ocr",
            files=[
                ("images", ("slide-1.png", b"fake-image-1", "image/png")),
                ("images", ("slide-2.png", b"fake-image-2", "image/png")),
                ("images", ("slide-3.png", b"fake-image-3", "image/png")),
            ],
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error_code"] == "OCR_FAILED"
        assert detail["failure_reason"] == "OCR_FAILED"
        assert detail["workflow_run_id"] is not None

        session: Session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            workflow_run = session.get(WorkflowRun, detail["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "failed"
            assert workflow_run.failure_reason == "OCR_FAILED"
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_chat_text_api_persists_short_text() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory

    try:
        client = TestClient(app)
        chat_text = "Meeting notes about retrieval quality and attention concepts."
        response = client.post(
            "/api/ingest/chat-text",
            json={
                "chat_text": chat_text,
                "source_display_name": "chat-2026-05-25",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "succeeded"
        assert payload["source_type"] == "chat_text"
        assert payload["source_display_name"] == "chat-2026-05-25"
        assert payload["content_hash"] == hashlib.sha256(
            chat_text.encode("utf-8")
        ).hexdigest()

        session: Session = session_factory()
        try:
            source_document = session.get(SourceDocument, payload["source_document_id"])
            assert source_document is not None
            assert source_document.source_type == "chat_text"
            assert source_document.source_display_name == "chat-2026-05-25"
            assert source_document.raw_text == chat_text

            workflow_run = session.get(WorkflowRun, payload["workflow_run_id"])
            assert workflow_run is not None
            assert workflow_run.workflow_type == "ingestion"
            assert workflow_run.status == "succeeded"
            assert workflow_run.failure_reason is None

            # Step 25 must not perform Notion writes.
            assert session.query(NotionPage).count() == 0
            assert session.query(NotionBlock).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()


def test_ingest_chat_text_api_rejects_over_limit_text() -> None:
    session_factory = _build_session_factory()

    def _db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_db_session_factory] = lambda: session_factory

    try:
        client = TestClient(app)
        too_long_chat_text = "a" * (MVP_CHAT_TEXT_MAX_CHARS + 1)
        response = client.post(
            "/api/ingest/chat-text",
            json={
                "chat_text": too_long_chat_text,
                "source_display_name": "chat-too-long",
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["error_code"] == "INVALID_ARGUMENT"
        assert detail["failure_reason"] == "UNKNOWN_ERROR"
        assert "exceeds MVP length limit" in detail["message"]
        assert detail["workflow_run_id"] is None

        session: Session = session_factory()
        try:
            assert session.query(SourceDocument).count() == 0
            assert session.query(WorkflowRun).count() == 0
        finally:
            session.close()
    finally:
        app.dependency_overrides.clear()
