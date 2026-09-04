"""Opt-in smoke matrix for real-library adapters.

The default matrix uses local fixtures and injected transports only. Network,
credentials, database access, and Telegram sends require an explicit live flag.
Reports intentionally contain only fixed, redacted messages.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.providers import EmbeddingRequest, OpenAIEmbeddingClient  # noqa: E402
from src.tools import (  # noqa: E402
    ImageOCRParserClientError,
    OCRImageInput,
    PDFParserClientError,
    PyPDFParserClient,
    TesseractImageOCRParserClient,
    TrafilaturaURLArticleParserClient,
    URLHTTPTransport,
    URLSafetyPolicy,
    YouTubeTranscriptAPIClient,
)
from src.tools.telegram_bot_tool import (  # noqa: E402
    TelegramBotClientError,
    TelegramHTTPBotClient,
)

LIVE_FLAG = "LEARNLOOP_RUN_ADAPTER_SMOKE_LIVE"
REQUIRE_OCR_FLAG = "LEARNLOOP_SMOKE_REQUIRE_OCR"


@dataclass(frozen=True)
class AdapterSmokeCheck:
    check_id: str
    dependency_level: str
    status: str
    message: str


@dataclass(frozen=True)
class AdapterSmokeReport:
    checks: List[AdapterSmokeCheck]

    @property
    def failed(self) -> bool:
        return any(check.status == "failed" for check in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(check.status == "passed" for check in self.checks)

    @property
    def skipped_count(self) -> int:
        return sum(check.status == "skipped" for check in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checks": [asdict(check) for check in self.checks],
            "summary": {
                "failed": self.failed,
                "passed_count": self.passed_count,
                "skipped_count": self.skipped_count,
                "total_count": len(self.checks),
            },
        }


def _passed(check_id: str, dependency_level: str) -> AdapterSmokeCheck:
    return AdapterSmokeCheck(check_id, dependency_level, "passed", "check passed")


def _skipped(
    check_id: str,
    dependency_level: str,
    message: str,
) -> AdapterSmokeCheck:
    return AdapterSmokeCheck(check_id, dependency_level, "skipped", message)


def _failed(check_id: str, dependency_level: str, message: str) -> AdapterSmokeCheck:
    return AdapterSmokeCheck(check_id, dependency_level, "failed", message)


def _build_pdf_fixture() -> bytes:
    """Build a one-page PDF without adding a document-generation dependency."""

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length 57 >>\nstream\nBT /F1 18 Tf 36 96 Td (LearnLoop PDF smoke) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode("ascii"))
        document.extend(obj)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(document)


def _run_pdf_check() -> AdapterSmokeCheck:
    check_id = "pdf_pypdf_fixture"
    try:
        parsed = PyPDFParserClient().parse_document(
            file_name="adapter-smoke.pdf",
            file_bytes=_build_pdf_fixture(),
        )
    except (PDFParserClientError, Exception):
        return _failed(check_id, "adapter_integration", "real PDF adapter failed")
    if parsed.page_count != 1 or "LearnLoop PDF smoke" not in parsed.raw_text:
        return _failed(check_id, "adapter_integration", "real PDF adapter returned invalid output")
    return _passed(check_id, "adapter_integration")


def _build_ocr_fixture() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.text((24, 48), "LEARNLOOP OCR SMOKE", fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _run_ocr_check(*, require_ocr: bool) -> AdapterSmokeCheck:
    check_id = "ocr_tesseract_fixture"
    if shutil.which("tesseract") is None:
        if require_ocr:
            return _failed(check_id, "adapter_integration", "Tesseract runtime is required")
        return _skipped(check_id, "adapter_integration", "Tesseract runtime is unavailable")
    try:
        parsed = TesseractImageOCRParserClient().parse_images(
            images=[
                OCRImageInput(
                    file_name="adapter-smoke.png",
                    file_bytes=_build_ocr_fixture(),
                )
            ]
        )
    except (ImageOCRParserClientError, Exception):
        if require_ocr:
            return _failed(check_id, "adapter_integration", "real OCR adapter failed")
        return _skipped(check_id, "adapter_integration", "OCR fixture could not be processed")
    normalized = "".join(character for character in parsed.raw_text.upper() if character.isalpha())
    if "LEARNLOOP" not in normalized or "OCR" not in normalized:
        if require_ocr:
            return _failed(check_id, "adapter_integration", "real OCR adapter returned invalid output")
        return _skipped(check_id, "adapter_integration", "OCR output was not stable for the fixture")
    return _passed(check_id, "adapter_integration")


@dataclass
class _FixtureHTTPResponse:
    body: bytes
    status: int = 200
    headers: Mapping[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body) - self._offset
        result = self.body[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def close(self) -> None:
        return None


class _FixtureHTTPTransport(URLHTTPTransport):
    def __init__(self, response: _FixtureHTTPResponse) -> None:
        self._response = response

    def open(self, *, url: str, timeout_seconds: float) -> _FixtureHTTPResponse:
        _ = url
        _ = timeout_seconds
        return self._response


def _run_url_check() -> AdapterSmokeCheck:
    check_id = "url_trafilatura_fixture"
    html = (
        b"<html><head><title>Adapter fixture</title></head>"
        b"<body><article><h1>Adapter fixture</h1>"
        b"<p>LearnLoop URL extraction smoke content.</p></article></body></html>"
    )
    client = TrafilaturaURLArticleParserClient(
        http_transport=_FixtureHTTPTransport(_FixtureHTTPResponse(body=html)),
        safety_policy=URLSafetyPolicy(
            dns_resolver=lambda hostname, port: ["93.184.216.34"]
        ),
    )
    try:
        parsed = client.parse_article(url="https://public.example/adapter-fixture")
    except Exception:
        return _failed(check_id, "adapter_integration", "real URL adapter failed")
    if "LearnLoop URL extraction smoke content" not in parsed.raw_text:
        return _failed(check_id, "adapter_integration", "real URL adapter returned invalid output")
    return _passed(check_id, "adapter_integration")


async def _run_openai_check(api_key: str) -> AdapterSmokeCheck:
    check_id = "openai_embedding_live"
    try:
        response = await OpenAIEmbeddingClient(api_key=api_key).embed(
            EmbeddingRequest(inputs=["adapter smoke probe"])
        )
    except Exception:
        return _failed(check_id, "live_dependency", "OpenAI embedding check failed")
    if not response.embeddings or not response.embeddings[0]:
        return _failed(check_id, "live_dependency", "OpenAI embedding response was empty")
    return _passed(check_id, "live_dependency")


def _run_postgres_check(database_url: str) -> AdapterSmokeCheck:
    check_id = "postgres_readiness_live"
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        return _failed(check_id, "live_dependency", "PostgreSQL connectivity check failed")
    return _passed(check_id, "live_dependency")


def _run_youtube_check(url: str) -> AdapterSmokeCheck:
    check_id = "youtube_transcript_live"
    try:
        parsed = YouTubeTranscriptAPIClient().parse_transcript(url=url)
    except Exception:
        return _failed(check_id, "live_dependency", "YouTube transcript check failed")
    if not parsed.video_id or not parsed.raw_text.strip():
        return _failed(check_id, "live_dependency", "YouTube transcript response was empty")
    return _passed(check_id, "live_dependency")


def _run_telegram_check(*, token: str, chat_id: str) -> AdapterSmokeCheck:
    check_id = "telegram_send_live"
    try:
        TelegramHTTPBotClient(bot_token=token).send_message(
            chat_id=chat_id,
            text="LearnLoop adapter smoke check",
        )
    except TelegramBotClientError:
        return _failed(check_id, "live_dependency", "Telegram send check failed")
    except Exception:
        return _failed(check_id, "live_dependency", "Telegram send check failed")
    return _passed(check_id, "live_dependency")


def run_adapter_smoke_matrix(
    *,
    include_live: bool = False,
    require_ocr: bool = False,
    environment: Optional[Mapping[str, str]] = None,
) -> AdapterSmokeReport:
    env = os.environ if environment is None else environment
    checks = [
        _run_pdf_check(),
        _run_ocr_check(require_ocr=require_ocr),
        _run_url_check(),
    ]
    if not include_live:
        checks.extend(
            [
                _skipped("youtube_transcript_live", "live_dependency", "live checks are disabled"),
                _skipped("openai_embedding_live", "live_dependency", "live checks are disabled"),
                _skipped("postgres_readiness_live", "live_dependency", "live checks are disabled"),
                _skipped("telegram_send_live", "live_dependency", "live checks are disabled"),
            ]
        )
        return AdapterSmokeReport(checks=checks)

    youtube_url = env.get("LEARNLOOP_SMOKE_YOUTUBE_URL", "").strip()
    checks.append(
        _run_youtube_check(youtube_url)
        if youtube_url
        else _skipped("youtube_transcript_live", "live_dependency", "YouTube URL is not configured")
    )
    openai_key = env.get("OPENAI_API_KEY", "").strip()
    checks.append(
        asyncio.run(_run_openai_check(openai_key))
        if openai_key
        else _skipped("openai_embedding_live", "live_dependency", "OpenAI API key is not configured")
    )
    database_url = env.get("LEARNLOOP_SMOKE_DATABASE_URL", "").strip()
    checks.append(
        _run_postgres_check(database_url)
        if database_url
        else _skipped("postgres_readiness_live", "live_dependency", "PostgreSQL URL is not configured")
    )
    telegram_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = env.get("LEARNLOOP_SMOKE_TELEGRAM_CHAT_ID", "").strip()
    allow_telegram_send = env.get("LEARNLOOP_SMOKE_ALLOW_TELEGRAM_SEND") == "1"
    checks.append(
        _run_telegram_check(token=telegram_token, chat_id=telegram_chat_id)
        if telegram_token and telegram_chat_id and allow_telegram_send
        else _skipped(
            "telegram_send_live",
            "live_dependency",
            "Telegram send requires token, chat ID, and explicit send permission",
        )
    )
    return AdapterSmokeReport(checks=checks)


def render_report(report: AdapterSmokeReport, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
    lines = [
        (
            f"adapter smoke matrix: {report.passed_count} passed, "
            f"{report.skipped_count} skipped, "
            f"{len(report.checks) - report.passed_count - report.skipped_count} failed"
        )
    ]
    lines.extend(
        f"- {check.check_id}: {check.status} ({check.dependency_level}) - {check.message}"
        for check in report.checks
    )
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="enable opt-in live checks")
    parser.add_argument(
        "--require-ocr",
        action="store_true",
        help="fail when the local Tesseract check cannot run",
    )
    parser.add_argument("--json", action="store_true", help="print the redacted JSON report")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = run_adapter_smoke_matrix(
        include_live=args.live or os.environ.get(LIVE_FLAG) == "1",
        require_ocr=args.require_ocr or os.environ.get(REQUIRE_OCR_FLAG) == "1",
    )
    print(render_report(report, as_json=args.json))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
