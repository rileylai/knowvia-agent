from __future__ import annotations

import asyncio
import base64

from src.tools import (
    PDFParserClient,
    PDFParserClientError,
    PDFParserTool,
    ParsedPDFDocument,
    ToolContext,
)


class _FakePDFParserClient(PDFParserClient):
    def __init__(
        self,
        *,
        raw_text: str = "Extracted PDF text",
        page_count: int = 1,
        should_fail: bool = False,
    ) -> None:
        self._raw_text = raw_text
        self._page_count = page_count
        self._should_fail = should_fail

    def parse_document(self, *, file_name: str, file_bytes: bytes) -> ParsedPDFDocument:
        _ = file_name
        _ = file_bytes
        if self._should_fail:
            raise PDFParserClientError("parse failed")
        return ParsedPDFDocument(raw_text=self._raw_text, page_count=self._page_count)


def test_pdf_parser_tool_returns_extracted_text() -> None:
    tool = PDFParserTool(_FakePDFParserClient(raw_text="Hello PDF", page_count=2))

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-1"),
            arguments={
                "file_name": "lecture.pdf",
                "file_bytes_base64": base64.b64encode(b"fake-pdf-bytes").decode(
                    "ascii"
                ),
            },
        )
    )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["raw_text"] == "Hello PDF"
    assert result.structured_content["page_count"] == 2
    assert result.structured_content["char_count"] == len("Hello PDF")


def test_pdf_parser_tool_returns_invalid_argument_for_bad_base64() -> None:
    tool = PDFParserTool(_FakePDFParserClient())

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-2"),
            arguments={
                "file_name": "lecture.pdf",
                "file_bytes_base64": "!!!not-base64!!!",
            },
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENT"


def test_pdf_parser_tool_maps_parse_error_to_pdf_parse_failed() -> None:
    tool = PDFParserTool(_FakePDFParserClient(should_fail=True))

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-3"),
            arguments={
                "file_name": "lecture.pdf",
                "file_bytes_base64": base64.b64encode(b"fake-pdf-bytes").decode(
                    "ascii"
                ),
            },
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "PDF_PARSE_FAILED"
    assert result.error.message == "parse failed"
