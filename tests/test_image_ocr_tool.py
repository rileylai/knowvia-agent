from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from src.tools import (
    ImageOCRParserClient,
    ImageOCRParserClientError,
    ImageOCRTool,
    OCRImageInput,
    ParsedImageOCR,
    ToolContext,
)
from src.tools.image_ocr_tool import (
    TESSERACT_OCR_LANGUAGE,
    TESSERACT_REQUIRED_LANGUAGES,
    TesseractImageOCRParserClient,
)


class _FakeImageOCRParserClient(ImageOCRParserClient):
    def __init__(
        self,
        *,
        raw_text: str = "Extracted OCR text",
        should_fail: bool = False,
    ) -> None:
        self._raw_text = raw_text
        self._should_fail = should_fail

    def parse_images(self, *, images: list[OCRImageInput]) -> ParsedImageOCR:
        _ = images
        if self._should_fail:
            raise ImageOCRParserClientError("ocr failed")
        return ParsedImageOCR(raw_text=self._raw_text, image_count=2)


def _build_png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def _install_fake_pytesseract(
    monkeypatch: pytest.MonkeyPatch,
    *,
    available_languages: set[str],
) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []

    def image_to_string(_image, *, lang: str, config: str) -> str:
        calls.append({"lang": lang, "config": config})
        return f"section-{len(calls)}"

    fake_module = SimpleNamespace(
        get_languages=lambda config="": sorted(available_languages),
        image_to_string=image_to_string,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_module)
    return calls


def test_image_ocr_tool_returns_extracted_text() -> None:
    tool = ImageOCRTool(_FakeImageOCRParserClient(raw_text="line-1\nline-2"))

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-1"),
            arguments={
                "images": [
                    {
                        "file_name": "img1.png",
                        "file_bytes_base64": base64.b64encode(b"image-1").decode("ascii"),
                    },
                    {
                        "file_name": "img2.png",
                        "file_bytes_base64": base64.b64encode(b"image-2").decode("ascii"),
                    },
                ]
            },
        )
    )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["raw_text"] == "line-1\nline-2"
    assert result.structured_content["image_count"] == 2
    assert result.structured_content["char_count"] == len("line-1\nline-2")


def test_image_ocr_tool_rejects_empty_images() -> None:
    tool = ImageOCRTool(_FakeImageOCRParserClient())

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-2"),
            arguments={"images": []},
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENT"


def test_image_ocr_tool_maps_client_error_to_ocr_failed() -> None:
    tool = ImageOCRTool(_FakeImageOCRParserClient(should_fail=True))

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-3"),
            arguments={
                "images": [
                    {
                        "file_name": "img1.png",
                        "file_bytes_base64": base64.b64encode(b"image-1").decode("ascii"),
                    }
                ]
            },
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "OCR_FAILED"
    assert result.error.message == "ocr failed"


def test_tesseract_parser_uses_required_languages_for_every_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_pytesseract(
        monkeypatch,
        available_languages=set(TESSERACT_REQUIRED_LANGUAGES),
    )
    image_bytes = _build_png_bytes()

    parsed = TesseractImageOCRParserClient().parse_images(
        images=[
            OCRImageInput(file_name="first.png", file_bytes=image_bytes),
            OCRImageInput(file_name="second.png", file_bytes=image_bytes),
        ]
    )

    assert calls == [
        {"lang": "eng+chi_tra+chi_sim", "config": "--psm 6"},
        {"lang": "eng+chi_tra+chi_sim", "config": "--psm 6"},
    ]
    assert TESSERACT_OCR_LANGUAGE == "eng+chi_tra+chi_sim"
    assert parsed.image_count == 2
    assert parsed.raw_text.index("[Image 1: first.png]") < parsed.raw_text.index(
        "[Image 2: second.png]"
    )


@pytest.mark.parametrize("missing_language", TESSERACT_REQUIRED_LANGUAGES)
def test_tesseract_parser_fails_closed_when_required_language_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    missing_language: str,
) -> None:
    calls = _install_fake_pytesseract(
        monkeypatch,
        available_languages=set(TESSERACT_REQUIRED_LANGUAGES) - {missing_language},
    )

    with pytest.raises(ImageOCRParserClientError) as exc_info:
        TesseractImageOCRParserClient().parse_images(
            images=[
                OCRImageInput(
                    file_name="mixed-script.png",
                    file_bytes=_build_png_bytes(),
                )
            ]
        )

    assert exc_info.value.error_code == "OCR_FAILED"
    assert missing_language in str(exc_info.value)
    assert calls == []
