from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from io import BytesIO
from typing import Optional, Tuple


MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_EXTRACTED_TEXT_CHARS = 200_000

MAX_OCR_IMAGE_COUNT = 10
MAX_OCR_IMAGE_BYTES = 5 * 1024 * 1024
MAX_OCR_TOTAL_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

PDF_MIME_TYPES = frozenset({"application/pdf"})
IMAGE_MIME_TYPES = frozenset(
    {
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
    }
)


@dataclass(frozen=True)
class UploadValidationError(ValueError):
    error_code: str
    message: str
    failure_reason: str

    def __str__(self) -> str:
        return self.message


def validate_pdf_metadata(
    *,
    file_name: str,
    mime_type: Optional[str] = None,
) -> None:
    if not file_name.strip().lower().endswith(".pdf"):
        raise _error(
            "INVALID_UPLOAD_TYPE",
            "Uploaded document must be a .pdf file",
        )
    normalized_mime_type = _normalize_mime_type(mime_type)
    if normalized_mime_type and normalized_mime_type not in PDF_MIME_TYPES:
        raise _error(
            "INVALID_UPLOAD_MIME",
            "Uploaded PDF must use MIME type application/pdf",
        )


def validate_image_metadata(*, mime_type: Optional[str]) -> None:
    normalized_mime_type = _normalize_mime_type(mime_type)
    if normalized_mime_type and normalized_mime_type not in IMAGE_MIME_TYPES:
        raise _error(
            "INVALID_UPLOAD_MIME",
            "Uploaded image MIME type is not supported",
        )


def validate_file_bytes(
    *,
    file_bytes: bytes,
    maximum_bytes: int,
    label: str,
) -> None:
    if not file_bytes:
        raise _error("EMPTY_UPLOAD", f"{label} is empty")
    if len(file_bytes) > maximum_bytes:
        raise _error(
            "UPLOAD_TOO_LARGE",
            f"{label} exceeds the {maximum_bytes} byte limit",
        )


def validate_ocr_batch(*, image_count: int, total_bytes: int) -> None:
    if image_count > MAX_OCR_IMAGE_COUNT:
        raise _error(
            "UPLOAD_LIMIT_EXCEEDED",
            f"OCR image count exceeds the {MAX_OCR_IMAGE_COUNT} image limit",
        )
    if total_bytes > MAX_OCR_TOTAL_BYTES:
        raise _error(
            "UPLOAD_TOO_LARGE",
            f"OCR batch exceeds the {MAX_OCR_TOTAL_BYTES} byte limit",
        )


def validate_pdf_page_count(page_count: int) -> None:
    if page_count > MAX_PDF_PAGES:
        raise _error(
            "PDF_PAGE_LIMIT_EXCEEDED",
            f"PDF exceeds the {MAX_PDF_PAGES} page limit",
        )


def validate_extracted_text(text: str) -> None:
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        raise _error(
            "EXTRACTED_TEXT_LIMIT_EXCEEDED",
            f"Extracted text exceeds the {MAX_EXTRACTED_TEXT_CHARS} character limit",
        )


def inspect_image_dimensions(file_bytes: bytes, *, file_name: str) -> Tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(BytesIO(file_bytes)) as image:
            width, height = image.size
    except UploadValidationError:
        raise
    except Exception as exc:
        raise _error(
            "INVALID_IMAGE",
            f"Unable to inspect image '{file_name}'",
        ) from exc

    if width * height > MAX_IMAGE_PIXELS:
        raise _error(
            "IMAGE_PIXEL_LIMIT_EXCEEDED",
            f"Image exceeds the {MAX_IMAGE_PIXELS} pixel limit",
        )
    return width, height


def upload_error_http_status(error_code: str) -> int:
    if error_code in {"UPLOAD_TOO_LARGE", "UPLOAD_LIMIT_EXCEEDED"}:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    if error_code in {
        "PDF_PAGE_LIMIT_EXCEEDED",
        "IMAGE_PIXEL_LIMIT_EXCEEDED",
        "EXTRACTED_TEXT_LIMIT_EXCEEDED",
        "INVALID_IMAGE",
    }:
        return HTTPStatus.UNPROCESSABLE_ENTITY
    return HTTPStatus.BAD_REQUEST


def _normalize_mime_type(mime_type: Optional[str]) -> str:
    return (mime_type or "").split(";", 1)[0].strip().lower()


def _error(error_code: str, message: str) -> UploadValidationError:
    return UploadValidationError(
        error_code=error_code,
        message=message,
        failure_reason=error_code,
    )
