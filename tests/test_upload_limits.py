from __future__ import annotations

import asyncio
import base64
from io import BytesIO

from PIL import Image
from fastapi.testclient import TestClient

from src.app.main import app
from src.services import (
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_IMAGE_PIXELS,
    MAX_OCR_IMAGE_BYTES,
    MAX_OCR_IMAGE_COUNT,
    UploadValidationError,
    inspect_image_dimensions,
    validate_extracted_text,
)
from src.tools import (
    ImageOCRParserClient,
    ImageOCRTool,
    OCRImageInput,
    PDFParserClient,
    PDFParserTool,
    ParsedImageOCR,
    ParsedPDFDocument,
    ToolContext,
)


class _FakePDFParser(PDFParserClient):
    def __init__(self, *, page_count: int = 1, raw_text: str = "text") -> None:
        self._page_count = page_count
        self._raw_text = raw_text

    def parse_document(self, *, file_name: str, file_bytes: bytes) -> ParsedPDFDocument:
        _ = file_name
        _ = file_bytes
        return ParsedPDFDocument(
            raw_text=self._raw_text,
            page_count=self._page_count,
        )


class _FakeOCRParser(ImageOCRParserClient):
    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        return ParsedImageOCR(raw_text="ok", image_count=len(images))


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_pdf_tool_rejects_page_and_extracted_text_limits() -> None:
    page_result = asyncio.run(
        PDFParserTool(_FakePDFParser(page_count=101)).run(
            context=ToolContext(workflow_id="pdf-limit"),
            arguments={
                "file_name": "large.pdf",
                "file_bytes_base64": _encoded(b"pdf"),
            },
        )
    )
    text_result = asyncio.run(
        PDFParserTool(
            _FakePDFParser(raw_text="x" * (MAX_EXTRACTED_TEXT_CHARS + 1))
        ).run(
            context=ToolContext(workflow_id="text-limit"),
            arguments={
                "file_name": "large-text.pdf",
                "file_bytes_base64": _encoded(b"pdf"),
            },
        )
    )

    assert page_result.error is not None
    assert page_result.error.code == "PDF_PAGE_LIMIT_EXCEEDED"
    assert text_result.error is not None
    assert text_result.error.code == "EXTRACTED_TEXT_LIMIT_EXCEEDED"


def test_ocr_tool_rejects_count_and_per_file_byte_limits() -> None:
    too_many = asyncio.run(
        ImageOCRTool(_FakeOCRParser()).run(
            context=ToolContext(workflow_id="count-limit"),
            arguments={
                "images": [
                    {
                        "file_name": f"image-{index}.png",
                        "file_bytes_base64": _encoded(b"x"),
                    }
                    for index in range(MAX_OCR_IMAGE_COUNT + 1)
                ]
            },
        )
    )
    too_large = asyncio.run(
        ImageOCRTool(_FakeOCRParser()).run(
            context=ToolContext(workflow_id="byte-limit"),
            arguments={
                "images": [
                    {
                        "file_name": "large.png",
                        "file_bytes_base64": _encoded(
                            b"x" * (MAX_OCR_IMAGE_BYTES + 1)
                        ),
                    }
                ]
            },
        )
    )

    assert too_many.error is not None
    assert too_many.error.code == "UPLOAD_LIMIT_EXCEEDED"
    assert too_large.error is not None
    assert too_large.error.code == "UPLOAD_TOO_LARGE"


def test_image_dimension_inspection_rejects_pixel_bomb() -> None:
    width = 10_000
    height = (MAX_IMAGE_PIXELS // width) + 1
    image = Image.new("1", (width, height))
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    try:
        inspect_image_dimensions(buffer.getvalue(), file_name="bomb.png")
    except UploadValidationError as exc:
        assert exc.error_code == "IMAGE_PIXEL_LIMIT_EXCEEDED"
    else:
        raise AssertionError("expected pixel limit failure")


def test_extracted_text_limit_is_deterministic() -> None:
    try:
        validate_extracted_text("x" * (MAX_EXTRACTED_TEXT_CHARS + 1))
    except UploadValidationError as exc:
        assert exc.error_code == "EXTRACTED_TEXT_LIMIT_EXCEEDED"
    else:
        raise AssertionError("expected extracted text limit failure")


def test_document_api_rejects_invalid_mime_before_workflow_creation() -> None:
    response = TestClient(app).post(
        "/api/ingest/document",
        files={"document": ("notes.pdf", b"pdf", "text/plain")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error_code"] == "INVALID_UPLOAD_MIME"
    assert detail["workflow_run_id"] is None


def test_image_api_rejects_batch_count_before_reading_files() -> None:
    response = TestClient(app).post(
        "/api/ingest/image-ocr",
        files=[
            ("images", (f"image-{index}.png", b"image", "image/png"))
            for index in range(MAX_OCR_IMAGE_COUNT + 1)
        ],
    )

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["error_code"] == "UPLOAD_LIMIT_EXCEEDED"
    assert detail["workflow_run_id"] is None
