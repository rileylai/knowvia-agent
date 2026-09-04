from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict

from src.tools.base import Tool
from src.tools.models import ToolContext, ToolResult, ToolSpec
from src.services import (
    MAX_PDF_BYTES,
    MAX_EXTRACTED_TEXT_CHARS,
    UploadValidationError,
    validate_extracted_text,
    validate_file_bytes,
    validate_pdf_page_count,
)


@dataclass
class ParsedPDFDocument:
    raw_text: str
    page_count: int


class PDFParserClientError(Exception):
    def __init__(self, message: str, *, error_code: str = "PDF_PARSE_FAILED") -> None:
        super().__init__(message)
        self.error_code = error_code


class PDFParserClient:
    def parse_document(self, *, file_name: str, file_bytes: bytes) -> ParsedPDFDocument:
        raise NotImplementedError


class PyPDFParserClient(PDFParserClient):
    def parse_document(self, *, file_name: str, file_bytes: bytes) -> ParsedPDFDocument:
        _ = file_name
        try:
            from pypdf import PdfReader
        except ModuleNotFoundError as exc:
            raise PDFParserClientError("pypdf dependency is missing") from exc

        try:
            reader = PdfReader(BytesIO(file_bytes))
        except Exception as exc:
            raise PDFParserClientError(f"Failed to open PDF: {exc}") from exc

        try:
            validate_pdf_page_count(len(reader.pages))
        except UploadValidationError as exc:
            raise PDFParserClientError(
                exc.message,
                error_code=exc.error_code,
            ) from exc

        page_texts = []
        extracted_chars = 0
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise PDFParserClientError(
                    f"Failed to extract text from PDF page: {exc}"
                ) from exc
            stripped = text.strip()
            if stripped:
                extracted_chars += len(stripped)
                if extracted_chars > MAX_EXTRACTED_TEXT_CHARS:
                    raise PDFParserClientError(
                        (
                            "Extracted text exceeds the "
                            f"{MAX_EXTRACTED_TEXT_CHARS} character limit"
                        ),
                        error_code="EXTRACTED_TEXT_LIMIT_EXCEEDED",
                    )
                page_texts.append(stripped)

        raw_text = "\n\n".join(page_texts).strip()
        if not raw_text:
            raise PDFParserClientError("No extractable text found in PDF")
        try:
            validate_extracted_text(raw_text)
        except UploadValidationError as exc:
            raise PDFParserClientError(
                exc.message,
                error_code=exc.error_code,
            ) from exc

        return ParsedPDFDocument(raw_text=raw_text, page_count=len(reader.pages))


class PDFParserTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="pdf_parser",
            description="Parse one PDF file and extract normalized plain text.",
            input_schema={
                "type": "object",
                "required": ["file_name", "file_bytes_base64"],
                "properties": {
                    "file_name": {"type": "string"},
                    "file_bytes_base64": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["raw_text", "page_count", "char_count"],
                "properties": {
                    "raw_text": {"type": "string"},
                    "page_count": {"type": "integer"},
                    "char_count": {"type": "integer"},
                },
            },
        )

    def __init__(self, parser_client: PDFParserClient) -> None:
        self._parser_client = parser_client

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        _ = context
        file_name = str(arguments.get("file_name", "")).strip()
        if not file_name:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="file_name is required",
            )

        encoded = str(arguments.get("file_bytes_base64", "")).strip()
        if not encoded:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="file_bytes_base64 is required",
            )

        try:
            file_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message=f"file_bytes_base64 is invalid: {exc}",
            )
        if not file_bytes:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="file_bytes_base64 decoded to empty bytes",
            )
        try:
            validate_file_bytes(
                file_bytes=file_bytes,
                maximum_bytes=MAX_PDF_BYTES,
                label="Uploaded PDF",
            )
        except UploadValidationError as exc:
            return ToolResult.failure(
                code=exc.error_code,
                message=exc.message,
            )

        try:
            parsed = self._parser_client.parse_document(
                file_name=file_name,
                file_bytes=file_bytes,
            )
        except PDFParserClientError as exc:
            return ToolResult.failure(code=exc.error_code, message=str(exc))

        try:
            validate_pdf_page_count(parsed.page_count)
            validate_extracted_text(parsed.raw_text)
        except UploadValidationError as exc:
            return ToolResult.failure(code=exc.error_code, message=exc.message)

        return ToolResult.success(
            content=(
                f"parsed file={file_name} pages={parsed.page_count} "
                f"char_count={len(parsed.raw_text)}"
            ),
            structured_content={
                "raw_text": parsed.raw_text,
                "page_count": parsed.page_count,
                "char_count": len(parsed.raw_text),
            },
        )
