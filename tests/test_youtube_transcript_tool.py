from __future__ import annotations

import asyncio

from src.tools import (
    ParsedYouTubeTranscript,
    ToolContext,
    YouTubeTranscriptParserClient,
    YouTubeTranscriptParserClientError,
    YouTubeTranscriptTool,
)


class _FakeYouTubeTranscriptParserClient(YouTubeTranscriptParserClient):
    def __init__(
        self,
        *,
        raw_text: str = "Extracted YouTube transcript",
        should_fail: bool = False,
    ) -> None:
        self._raw_text = raw_text
        self._should_fail = should_fail

    def parse_transcript(self, *, url: str) -> ParsedYouTubeTranscript:
        if self._should_fail:
            raise YouTubeTranscriptParserClientError("transcript not found")
        return ParsedYouTubeTranscript(
            video_id="dQw4w9WgXcQ",
            source_display_name="YouTube transcript (dQw4w9WgXcQ)",
            raw_text=self._raw_text,
        )


def test_youtube_transcript_tool_returns_extracted_text() -> None:
    tool = YouTubeTranscriptTool(
        _FakeYouTubeTranscriptParserClient(raw_text="Attention lecture transcript")
    )

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-1"),
            arguments={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
    )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["video_id"] == "dQw4w9WgXcQ"
    assert (
        result.structured_content["source_display_name"]
        == "YouTube transcript (dQw4w9WgXcQ)"
    )
    assert result.structured_content["raw_text"] == "Attention lecture transcript"
    assert result.structured_content["char_count"] == len("Attention lecture transcript")


def test_youtube_transcript_tool_rejects_unsupported_url() -> None:
    tool = YouTubeTranscriptTool(_FakeYouTubeTranscriptParserClient())

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-2"),
            arguments={"url": "https://example.com/watch?v=dQw4w9WgXcQ"},
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENT"


def test_youtube_transcript_tool_maps_client_error_to_not_found() -> None:
    tool = YouTubeTranscriptTool(_FakeYouTubeTranscriptParserClient(should_fail=True))

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-3"),
            arguments={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "YOUTUBE_TRANSCRIPT_NOT_FOUND"
    assert result.error.message == "transcript not found"
