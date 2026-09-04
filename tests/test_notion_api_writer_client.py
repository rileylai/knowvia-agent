from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Mapping, Tuple

from src.tools import (
    NotionAPIWriterClient,
    NotionHTTPResponse,
    NotionHTTPTransport,
    NotionReaderTool,
    NotionWriterTool,
    ToolContext,
)
from src.tools.notion_writer_tool import NotionAppendRequest


class _FakeNotionHTTPTransport(NotionHTTPTransport):
    def __init__(self, responses: List[NotionHTTPResponse]) -> None:
        self._responses = list(responses)
        self.calls: List[Tuple[str, str, Dict[str, Any]]] = []

    def get_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> NotionHTTPResponse:
        self.calls.append(("GET", path, {}))
        return self._responses.pop(0)

    def patch_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NotionHTTPResponse:
        self.calls.append(("PATCH", path, dict(payload)))
        return self._responses.pop(0)


def _page_and_zone_responses() -> List[NotionHTTPResponse]:
    return [
        NotionHTTPResponse(
            status_code=200,
            payload={
                "properties": {
                    "Name": {
                        "title": [{"type": "text", "plain_text": "Live Page"}]
                    }
                },
                "last_edited_time": "2026-07-27T10:00:00.000Z",
            },
        ),
        NotionHTTPResponse(
            status_code=200,
            payload={
                "results": [
                    {
                        "id": "zone-1",
                        "type": "toggle",
                        "has_children": True,
                        "toggle": {
                            "rich_text": [
                                {"type": "text", "plain_text": "AI Supplement Zone"}
                            ]
                        },
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            },
        ),
        NotionHTTPResponse(
            status_code=200,
            payload={
                "results": [
                    {
                        "id": "date-1",
                        "type": "toggle",
                        "has_children": True,
                        "toggle": {
                            "rich_text": [
                                {"type": "text", "plain_text": "2026-07-27"}
                            ]
                        },
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            },
        ),
        NotionHTTPResponse(
            status_code=200,
            payload={"results": [], "has_more": False, "next_cursor": None},
        ),
    ]


def _append_responses() -> List[NotionHTTPResponse]:
    return [
        NotionHTTPResponse(
            status_code=200,
            payload={"results": [{"id": "topic-1"}]},
        ),
        NotionHTTPResponse(
            status_code=200,
            payload={"results": [{"id": f"line-{index}"} for index in range(6)]},
        ),
    ]


def _request() -> NotionAppendRequest:
    return NotionAppendRequest(
        page_id="page-1",
        change_request_id=42,
        topic_title="Append Topic",
        source_display_name="source.pdf",
        summary="A safe summary.",
        concepts=["concept"],
        notes=["note"],
        append_date="2026-07-27",
        idempotency_key="change-request-42",
    )


def test_notion_api_writer_uses_only_read_and_append_http_operations() -> None:
    transport = _FakeNotionHTTPTransport(
        _page_and_zone_responses() + _page_and_zone_responses() + _append_responses()
    )
    client = NotionAPIWriterClient(token="secret-token", transport=transport)

    result = client.append_to_ai_supplement_zone(request=_request())

    assert result.idempotent_replay is False
    assert result.created_date_group is False
    assert result.target_path == (
        "Knowledge/Live Page/AI Supplement Zone/2026-07-27/Append Topic"
    )
    assert result.appended_block_count == 8
    assert [method for method, _, _ in transport.calls] == [
        "GET",
        "GET",
        "GET",
        "GET",
        "GET",
        "GET",
        "GET",
        "GET",
        "PATCH",
        "PATCH",
    ]
    assert all(
        path.startswith("/v1/pages/")
        or path.startswith("/v1/blocks/")
        for _, path, _ in transport.calls
    )
    assert all("DELETE" not in method and "PUT" not in method for method, _, _ in transport.calls)
    paragraph_payload = transport.calls[-1][2]["children"]
    assert paragraph_payload[-1]["paragraph"]["rich_text"][0]["text"]["content"].startswith(
        "LearnLoop Change Request: change-request-42"
    )


def test_notion_api_writer_tool_maps_auth_failure_without_upstream_body() -> None:
    transport = _FakeNotionHTTPTransport(
        [
            NotionHTTPResponse(
                status_code=401,
                payload={"message": "Bearer secret-token raw_text=private"},
            )
        ]
    )
    tool = NotionWriterTool(
        NotionAPIWriterClient(token="secret-token", transport=transport)
    )

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-writer-auth"),
            arguments={
                "page_id": "page-1",
                "change_request_id": 42,
                "topic_title": "Append Topic",
                "source_display_name": "source.pdf",
                "summary": "A safe summary.",
                "concepts": ["concept"],
                "notes": [],
            },
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "NOTION_AUTH_FAILED"
    assert result.error.message == "Notion authorization failed"
    assert "secret-token" not in result.error.message
    assert "private" not in result.error.message
