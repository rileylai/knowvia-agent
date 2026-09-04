from __future__ import annotations

import asyncio
import io
from typing import Dict, List, Mapping, Tuple
from urllib import error

import pytest
from rq.timeouts import JobTimeoutException

from src.observability.external_error import (
    ExternalErrorCategory,
    ExternalErrorDiagnostic,
    classify_http_error,
)
from src.tools import notion_api_reader_client as notion_reader_module
from src.tools import (
    NotionAPIReaderClient,
    NotionAPIClientError,
    NotionHTTPResponse,
    NotionHTTPTransport,
    NotionHTTPTransportError,
    NotionReaderTool,
    ToolContext,
    UrllibNotionHTTPTransport,
    normalize_notion_page_id,
)
from src.queue import classify_rq_execution_exception
from src.services import InfrastructureExecutionTimeout


class _FakeNotionHTTPTransport(NotionHTTPTransport):
    def __init__(self, responses: List[NotionHTTPResponse]) -> None:
        self._responses = list(responses)
        self.calls: List[Tuple[str, Dict[str, str], Dict[str, str]]] = []

    def get_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> NotionHTTPResponse:
        self.calls.append((path, dict(query), dict(headers)))
        return self._responses.pop(0)

    def post_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> NotionHTTPResponse:
        self.calls.append((path, dict(query), dict(headers)))
        return self._responses.pop(0)


def _page_response() -> NotionHTTPResponse:
    return NotionHTTPResponse(
        status_code=200,
        payload={
            "last_edited_time": "2026-07-27T10:00:00.000Z",
            "properties": {
                "Name": {
                    "id": "title",
                    "type": "title",
                    "title": [{"type": "text", "plain_text": "Live Page"}],
                }
            },
        },
    )


def test_notion_api_reader_fetches_paginated_nested_read_only_tree() -> None:
    transport = _FakeNotionHTTPTransport(
        [
            _page_response(),
            NotionHTTPResponse(
                status_code=200,
                payload={
                    "results": [
                        {
                            "id": "heading-1",
                            "type": "heading_2",
                            "has_children": True,
                            "heading_2": {
                                "rich_text": [
                                    {"type": "text", "plain_text": "Attention"}
                                ]
                            },
                        }
                    ],
                    "has_more": True,
                    "next_cursor": "cursor-1",
                },
            ),
            NotionHTTPResponse(
                status_code=200,
                payload={
                    "results": [
                        {
                            "id": "paragraph-2",
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {
                                "rich_text": [
                                    {"type": "text", "plain_text": "Second page"}
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
                            "id": "paragraph-1-child",
                            "type": "paragraph",
                            "has_children": False,
                            "paragraph": {
                                "rich_text": [
                                    {"type": "text", "plain_text": "Nested note"}
                                ]
                            },
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
            ),
        ]
    )
    client = NotionAPIReaderClient(
        token="secret-token",
        transport=transport,
        page_size=1,
    )

    page = client.fetch_page_tree("page/with id")

    assert page is not None
    assert page.title == "Live Page"
    assert page.notion_path == "Knowledge/Live Page"
    assert page.last_edited_time is not None
    assert page.last_edited_time.isoformat() == "2026-07-27T10:00:00+00:00"
    assert len(page.blocks) == 2
    assert page.blocks[0].block_path == "Knowledge/Live Page/Attention"
    assert page.blocks[0].children[0].content_text == "Nested note"
    assert (
        page.blocks[0].children[0].block_path
        == "Knowledge/Live Page/Attention/Nested note"
    )
    assert [call[0] for call in transport.calls] == [
        "/v1/pages/page%2Fwith%20id",
        "/v1/blocks/page%2Fwith%20id/children",
        "/v1/blocks/page%2Fwith%20id/children",
        "/v1/blocks/heading-1/children",
    ]
    assert transport.calls[2][1] == {"page_size": "1", "start_cursor": "cursor-1"}
    assert transport.calls[0][2]["Authorization"] == "Bearer secret-token"
    assert transport.calls[0][2]["Notion-Version"] == "2022-06-28"


def test_notion_api_reader_preserves_only_real_page_parent_identity() -> None:
    transport = _FakeNotionHTTPTransport(
        [
            NotionHTTPResponse(
                status_code=200,
                payload={
                    "parent": {"type": "page_id", "page_id": "PARENT"},
                    "properties": {
                        "Name": {"title": [{"plain_text": "Child"}]}
                    },
                },
            ),
            NotionHTTPResponse(
                status_code=200,
                payload={"results": [], "has_more": False, "next_cursor": None},
            ),
        ]
    )
    client = NotionAPIReaderClient(token="secret-token", transport=transport)

    page = client.fetch_page_tree("child")

    assert page is not None
    assert page.parent_notion_page_id == "PARENT"

    discovery_transport = _FakeNotionHTTPTransport(
        [
            NotionHTTPResponse(
                status_code=200,
                payload={
                    "results": [
                        {
                            "id": "child",
                            "parent": {"type": "page_id", "page_id": "parent"},
                            "properties": {"Name": {"title": [{"plain_text": "Child"}]}},
                        },
                        {
                            "id": "root",
                            "parent": {"type": "database_id", "database_id": "db"},
                            "properties": {"Name": {"title": [{"plain_text": "Root"}]}},
                        },
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
            )
        ]
    )
    discovered = NotionAPIReaderClient(
        token="secret-token", transport=discovery_transport
    ).list_pages()
    assert discovered[0].parent_notion_page_id == "parent"
    assert discovered[1].parent_notion_page_id is None


def test_notion_api_reader_returns_none_for_missing_page() -> None:
    transport = _FakeNotionHTTPTransport(
        [NotionHTTPResponse(status_code=404, payload={"message": "private body"})]
    )
    client = NotionAPIReaderClient(token="secret-token", transport=transport)

    assert client.fetch_page_tree("missing-page") is None
    assert len(transport.calls) == 1


def test_normalize_notion_page_id_accepts_compact_uuid() -> None:
    assert (
        normalize_notion_page_id("3AC80014E94F806EA0F9E7A72A010C02")
        == "3ac80014-e94f-806e-a0f9-e7a72a010c02"
    )
    assert normalize_notion_page_id("page-1") == "page-1"


def test_notion_api_reader_does_not_inline_child_page_contents() -> None:
    transport = _FakeNotionHTTPTransport(
        [
            _page_response(),
            NotionHTTPResponse(
                status_code=200,
                payload={
                    "results": [
                        {
                            "id": "child-page-block",
                            "type": "child_page",
                            "has_children": True,
                            "child_page": {"title": "Child Page"},
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
            ),
        ]
    )
    client = NotionAPIReaderClient(token="secret-token", transport=transport)

    page = client.fetch_page_tree("parent-page")

    assert page is not None
    assert len(page.blocks) == 1
    assert page.blocks[0].block_type == "child_page"
    assert page.blocks[0].children == []
    assert [call[0] for call in transport.calls] == [
        "/v1/pages/parent-page",
        "/v1/blocks/parent-page/children",
    ]


def test_notion_api_reader_discovers_paginated_external_page_ids() -> None:
    transport = _FakeNotionHTTPTransport(
        [
            NotionHTTPResponse(
                status_code=200,
                payload={
                    "results": [
                        {
                            "object": "page",
                            "id": "page-1",
                            "last_edited_time": "2026-07-27T10:00:00.000Z",
                            "properties": {
                                "Name": {
                                    "title": [
                                        {"type": "text", "plain_text": "One"}
                                    ]
                                }
                            },
                        }
                    ],
                    "has_more": True,
                    "next_cursor": "search-cursor",
                },
            ),
            NotionHTTPResponse(
                status_code=200,
                payload={
                    "results": [
                        {
                            "object": "page",
                            "id": "page-2",
                            "properties": {
                                "Name": {
                                    "title": [
                                        {"type": "text", "plain_text": "Two"}
                                    ]
                                }
                            },
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                },
            ),
        ]
    )
    client = NotionAPIReaderClient(
        token="secret-token",
        transport=transport,
        page_size=1,
    )

    pages = client.list_pages()

    assert [(page.page_id, page.title) for page in pages] == [
        ("page-1", "One"),
        ("page-2", "Two"),
    ]
    assert transport.calls[0][0] == "/v1/search"
    assert transport.calls[1][0] == "/v1/search"


def test_notion_api_reader_tool_maps_auth_and_http_errors_without_redaction_leaks() -> None:
    secret = "secret-token"
    transport = _FakeNotionHTTPTransport(
        [
            NotionHTTPResponse(
                status_code=401,
                payload={
                    "message": f"Bearer {secret} raw_text=private page contents"
                },
            )
        ]
    )
    tool = NotionReaderTool(
        NotionAPIReaderClient(token=secret, transport=transport)
    )

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-live-notion"),
            arguments={"page_id": "page-1"},
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "NOTION_AUTH_FAILED"
    assert result.error.message == "Notion authorization failed"
    assert secret not in result.error.message
    assert "private page contents" not in result.error.message


def test_notion_api_reader_maps_invalid_http_payload_to_safe_block_error() -> None:
    transport = _FakeNotionHTTPTransport(
        [NotionHTTPResponse(status_code=200, payload=None)]
    )
    client = NotionAPIReaderClient(token="secret-token", transport=transport)

    with pytest.raises(Exception) as exc_info:
        client.fetch_page_tree("page-1")
    assert type(exc_info.value).__name__ == "NotionAPIClientError"
    assert str(exc_info.value) == "Notion API response is invalid"


def test_notion_api_reader_maps_upstream_failure_without_response_body() -> None:
    transport = _FakeNotionHTTPTransport(
        [
            NotionHTTPResponse(
                status_code=500,
                payload={"message": "raw_text=private page contents"},
            )
        ]
    )
    tool = NotionReaderTool(
        NotionAPIReaderClient(
            token="secret-token",
            transport=transport,
            max_attempts=1,
        )
    )

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-live-notion"),
            arguments={"page_id": "page-1"},
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "NOTION_BLOCK_FETCH_FAILED"
    assert result.error.message == "Notion API request failed"
    assert "private page contents" not in result.error.message


class _RQTimeoutTransport(NotionHTTPTransport):
    def get_json(self, **kwargs) -> NotionHTTPResponse:
        _ = kwargs
        raise JobTimeoutException("rq timeout body must not escape")


def test_rq_timeout_is_not_classified_as_notion_failure() -> None:
    client = NotionAPIReaderClient(
        token="secret-token",
        transport=_RQTimeoutTransport(),
        max_attempts=1,
        infrastructure_exception_classifier=classify_rq_execution_exception,
    )

    with pytest.raises(InfrastructureExecutionTimeout):
        client.fetch_page_tree("page-1")


@pytest.mark.parametrize(
    ("status_code", "expected_category", "expected_retryable"),
    [
        (400, ExternalErrorCategory.REQUEST_INVALID, False),
        (401, ExternalErrorCategory.AUTHENTICATION_FAILED, False),
        (403, ExternalErrorCategory.AUTHENTICATION_FAILED, False),
        (408, ExternalErrorCategory.REQUEST_TIMEOUT, True),
        (413, ExternalErrorCategory.REQUEST_TOO_LARGE, False),
        (422, ExternalErrorCategory.VALIDATION_FAILED, False),
        (429, ExternalErrorCategory.RATE_LIMITED, True),
        (500, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (501, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, False),
        (502, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (503, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
        (504, ExternalErrorCategory.UPSTREAM_SERVER_ERROR, True),
    ],
)
def test_notion_api_reader_exposes_safe_http_diagnostics(
    status_code: int,
    expected_category: ExternalErrorCategory,
    expected_retryable: bool,
) -> None:
    client = NotionAPIReaderClient(
        token="secret-token",
        max_attempts=1,
        transport=_FakeNotionHTTPTransport(
            [
                NotionHTTPResponse(
                    status_code=status_code,
                    payload={"message": "private page contents secret-token"},
                )
            ]
        ),
    )

    with pytest.raises(NotionAPIClientError) as exc_info:
        client.fetch_page_tree("page-1")

    error_value = exc_info.value
    assert error_value.category == expected_category
    assert error_value.retryable is expected_retryable
    assert error_value.http_status == status_code
    assert "private page contents" not in str(error_value)
    assert "secret-token" not in str(error_value)


@pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503, 504])
def test_notion_api_reader_retries_allowlisted_transient_status_only_for_current_read(
    status_code: int,
) -> None:
    transport = _FakeNotionHTTPTransport(
        [
            NotionHTTPResponse(
                status_code=status_code,
                payload=None,
                diagnostic=classify_http_error(status_code=status_code),
            ),
            _page_response(),
            NotionHTTPResponse(
                status_code=200,
                payload={"results": [], "has_more": False, "next_cursor": None},
            ),
        ]
    )
    sleeps: List[float] = []
    client = NotionAPIReaderClient(
        token="secret-token",
        transport=transport,
        sleeper=sleeps.append,
    )

    page = client.fetch_page_tree("page-1")

    assert page is not None
    assert [call[0] for call in transport.calls] == [
        "/v1/pages/page-1",
        "/v1/pages/page-1",
        "/v1/blocks/page-1/children",
    ]
    assert sleeps == [1.0]


def test_notion_api_reader_caps_retry_after_and_stops_at_max_attempts() -> None:
    diagnostic = ExternalErrorDiagnostic(
        category=ExternalErrorCategory.RATE_LIMITED,
        retryable=True,
        http_status=429,
        retry_after_seconds=120,
    )
    transport = _FakeNotionHTTPTransport(
        [
            NotionHTTPResponse(status_code=429, payload=None, diagnostic=diagnostic),
            NotionHTTPResponse(status_code=429, payload=None, diagnostic=diagnostic),
        ]
    )
    sleeps: List[float] = []
    client = NotionAPIReaderClient(
        token="secret-token",
        transport=transport,
        max_attempts=2,
        retry_max_seconds=30,
        sleeper=sleeps.append,
    )

    with pytest.raises(NotionAPIClientError) as exc_info:
        client.fetch_page_tree("page-1")

    assert exc_info.value.category == ExternalErrorCategory.RATE_LIMITED
    assert len(transport.calls) == 2
    assert sleeps == [30]


def test_notion_api_reader_exposes_invalid_response_diagnostic() -> None:
    client = NotionAPIReaderClient(
        token="secret-token",
        transport=_FakeNotionHTTPTransport(
            [NotionHTTPResponse(status_code=200, payload=None)]
        ),
    )

    with pytest.raises(NotionAPIClientError) as exc_info:
        client.fetch_page_tree("page-1")

    assert exc_info.value.category == ExternalErrorCategory.RESPONSE_INVALID
    assert exc_info.value.retryable is False
    assert exc_info.value.http_status == 200


def test_default_notion_transport_classifies_timeout_without_raw_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout):
        _ = request, timeout
        raise TimeoutError("private page contents secret-token")

    monkeypatch.setattr(notion_reader_module.request, "urlopen", fake_urlopen)
    transport = UrllibNotionHTTPTransport()

    with pytest.raises(NotionHTTPTransportError) as exc_info:
        transport.get_json(path="/v1/pages/page-1", query={}, headers={})

    assert exc_info.value.category == ExternalErrorCategory.TIMEOUT
    assert exc_info.value.retryable is True
    assert "private page contents" not in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)


def test_default_notion_transport_classifies_rate_limit_and_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout):
        _ = request, timeout
        raise error.HTTPError(
            url="https://api.notion.com/v1/pages/page-1",
            code=429,
            msg="private provider message",
            hdrs={"Retry-After": "15"},
            fp=io.BytesIO(b'{"message":"private page contents"}'),
        )

    monkeypatch.setattr(notion_reader_module.request, "urlopen", fake_urlopen)
    response = UrllibNotionHTTPTransport().get_json(
        path="/v1/pages/page-1",
        query={},
        headers={},
    )

    assert response.status_code == 429
    assert response.diagnostic is not None
    assert response.diagnostic.category == ExternalErrorCategory.RATE_LIMITED
    assert response.diagnostic.retryable is True
    assert response.diagnostic.retry_after_seconds == 15
    assert response.payload is None


def test_default_notion_transport_classifies_connection_failure_without_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout):
        _ = request, timeout
        raise error.URLError("private page contents secret-token")

    monkeypatch.setattr(notion_reader_module.request, "urlopen", fake_urlopen)

    with pytest.raises(NotionHTTPTransportError) as exc_info:
        UrllibNotionHTTPTransport().get_json(
            path="/v1/pages/page-1",
            query={},
            headers={},
        )

    assert exc_info.value.category == ExternalErrorCategory.TRANSPORT_UNAVAILABLE
    assert exc_info.value.retryable is True
    assert "private page contents" not in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)
