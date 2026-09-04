from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, urlparse

from src.tools.base import Tool
from src.tools.models import ToolContext, ToolResult, ToolSpec

_YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_SUPPORTED_YOUTUBE_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
}


def extract_youtube_video_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None

    host = parsed.netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if host not in _SUPPORTED_YOUTUBE_HOSTS:
        return None

    video_id = ""
    if host == "youtu.be":
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            video_id = path_parts[0]
    else:
        query_value = parse_qs(parsed.query).get("v")
        if query_value:
            video_id = query_value[0]
        else:
            path_parts = [part for part in parsed.path.split("/") if part]
            if len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
                video_id = path_parts[1]

    normalized_video_id = video_id.strip()
    if not normalized_video_id:
        return None
    if not _YOUTUBE_VIDEO_ID_PATTERN.match(normalized_video_id):
        return None
    return normalized_video_id


@dataclass
class ParsedYouTubeTranscript:
    video_id: str
    source_display_name: str
    raw_text: str


class YouTubeTranscriptParserClientError(Exception):
    pass


class YouTubeTranscriptParserClient:
    def parse_transcript(self, *, url: str) -> ParsedYouTubeTranscript:
        raise NotImplementedError


class YouTubeTranscriptAPIClient(YouTubeTranscriptParserClient):
    def parse_transcript(self, *, url: str) -> ParsedYouTubeTranscript:
        video_id = extract_youtube_video_id(url)
        if not video_id:
            raise YouTubeTranscriptParserClientError(
                "url must be a supported YouTube video URL"
            )

        snippets = self._fetch_snippets(video_id)
        raw_text = self._build_raw_text(snippets)
        if not raw_text:
            raise YouTubeTranscriptParserClientError(
                "No transcript found for this YouTube video"
            )

        return ParsedYouTubeTranscript(
            video_id=video_id,
            source_display_name=f"YouTube transcript ({video_id})",
            raw_text=raw_text,
        )

    def _fetch_snippets(self, video_id: str) -> Iterable[Any]:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ModuleNotFoundError as exc:
            raise YouTubeTranscriptParserClientError(
                "youtube-transcript-api dependency is missing"
            ) from exc

        try:
            api_client = YouTubeTranscriptApi()
            if hasattr(api_client, "fetch"):
                return api_client.fetch(video_id, languages=["en"])
        except Exception:
            # Fall back to legacy class-level APIs below.
            pass

        try:
            if hasattr(YouTubeTranscriptApi, "get_transcript"):
                return YouTubeTranscriptApi.get_transcript(video_id, languages=["en"])
            if hasattr(YouTubeTranscriptApi, "list_transcripts"):
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                transcript = transcript_list.find_transcript(["en"])
                return transcript.fetch()
            raise YouTubeTranscriptParserClientError(
                "Unsupported youtube-transcript-api client interface"
            )
        except Exception as exc:
            raise YouTubeTranscriptParserClientError(
                f"Failed to fetch YouTube transcript: {exc}"
            ) from exc

    def _build_raw_text(self, snippets: Iterable[Any]) -> str:
        lines = []
        for snippet in snippets:
            text_value = self._extract_text(snippet)
            if not text_value:
                continue
            lines.append(text_value)
        return "\n".join(lines).strip()

    def _extract_text(self, snippet: Any) -> str:
        if isinstance(snippet, dict):
            text = snippet.get("text")
        else:
            text = getattr(snippet, "text", None)
        if not isinstance(text, str):
            return ""
        return text.strip()


class YouTubeTranscriptTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="youtube_transcript_parser",
            description="Fetch one YouTube transcript and extract normalized plain text.",
            input_schema={
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["video_id", "source_display_name", "raw_text", "char_count"],
                "properties": {
                    "video_id": {"type": "string"},
                    "source_display_name": {"type": "string"},
                    "raw_text": {"type": "string"},
                    "char_count": {"type": "integer"},
                },
            },
        )

    def __init__(self, parser_client: YouTubeTranscriptParserClient) -> None:
        self._parser_client = parser_client

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        _ = context
        url = str(arguments.get("url", "")).strip()
        if not url:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="url is required",
            )
        video_id = extract_youtube_video_id(url)
        if not video_id:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="url must be a supported YouTube video URL",
            )

        try:
            parsed = self._parser_client.parse_transcript(url=url)
        except YouTubeTranscriptParserClientError as exc:
            return ToolResult.failure(
                code="YOUTUBE_TRANSCRIPT_NOT_FOUND",
                message=str(exc),
            )

        normalized_raw_text = parsed.raw_text.strip()
        if not normalized_raw_text:
            return ToolResult.failure(
                code="YOUTUBE_TRANSCRIPT_NOT_FOUND",
                message="No transcript found for this YouTube video",
            )

        source_display_name = parsed.source_display_name.strip()
        if not source_display_name:
            source_display_name = f"YouTube transcript ({parsed.video_id})"

        return ToolResult.success(
            content=f"parsed youtube_video_id={parsed.video_id} char_count={len(normalized_raw_text)}",
            structured_content={
                "video_id": parsed.video_id,
                "source_display_name": source_display_name,
                "raw_text": normalized_raw_text,
                "char_count": len(normalized_raw_text),
            },
        )
