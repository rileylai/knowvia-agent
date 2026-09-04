from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping

from src.tools import (
    MAX_URL_REDIRECTS,
    ParsedURLArticle,
    TrafilaturaURLArticleParserClient,
    ToolContext,
    URLArticleParserClient,
    URLArticleParserClientError,
    URLArticleParserTool,
    URLHTTPTransport,
    URLSafetyPolicy,
)


@dataclass
class _FakeHTTPResponse:
    status: int = 200
    headers: Mapping[str, str] = None  # type: ignore[assignment]
    body: bytes = b"<html>ok</html>"

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {"Content-Type": "text/html"}
        self._offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body) - self._offset
        result = self.body[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True


class _FakeHTTPTransport(URLHTTPTransport):
    def __init__(self, responses: Dict[str, _FakeHTTPResponse]) -> None:
        self._responses = responses
        self.requested_urls = []

    def open(self, *, url: str, timeout_seconds: float) -> Any:
        _ = timeout_seconds
        self.requested_urls.append(url)
        response = self._responses.get(url)
        if response is None:
            raise AssertionError(f"unexpected URL request: {url}")
        return response


def _resolver(addresses: Iterable[str]):
    def resolve(hostname: str, port: int) -> Iterable[str]:
        _ = hostname
        _ = port
        return addresses

    return resolve


class _FakeURLArticleParserClient(URLArticleParserClient):
    def __init__(
        self,
        *,
        raw_text: str = "Extracted URL article text",
        should_fail: bool = False,
    ) -> None:
        self._raw_text = raw_text
        self._should_fail = should_fail

    def parse_article(self, *, url: str) -> ParsedURLArticle:
        if self._should_fail:
            raise URLArticleParserClientError("fetch failed")
        return ParsedURLArticle(url=url, raw_text=self._raw_text)


def test_url_article_parser_tool_returns_extracted_text() -> None:
    tool = URLArticleParserTool(
        _FakeURLArticleParserClient(raw_text="Attention is all you need")
    )

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-1"),
            arguments={"url": "https://example.com/article"},
        )
    )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["url"] == "https://example.com/article"
    assert result.structured_content["raw_text"] == "Attention is all you need"
    assert result.structured_content["char_count"] == len("Attention is all you need")


def test_url_article_parser_tool_rejects_non_http_url() -> None:
    tool = URLArticleParserTool(_FakeURLArticleParserClient())

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-2"),
            arguments={"url": "ftp://example.com/article"},
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "INVALID_ARGUMENT"


def test_url_article_parser_tool_rejects_ipv4_and_ipv6_private_hosts() -> None:
    tool = URLArticleParserTool(_FakeURLArticleParserClient())

    for url in ("http://127.0.0.1/private", "http://[::1]/private"):
        result = asyncio.run(
            tool.run(
                context=ToolContext(workflow_id="wf-private"),
                arguments={"url": url},
            )
        )

        assert result.is_error is True
        assert result.error is not None
        assert result.error.code == "URL_SSRF_BLOCKED"


def test_url_safety_policy_rejects_dns_result_in_private_range() -> None:
    policy = URLSafetyPolicy(dns_resolver=_resolver(["10.0.0.9", "93.184.216.34"]))

    try:
        policy.validate("https://example.com/article")
    except URLArticleParserClientError as exc:
        assert exc.code == "URL_SSRF_BLOCKED"
    else:
        raise AssertionError("private DNS result must be blocked")


def test_url_parser_rejects_redirect_to_private_host() -> None:
    transport = _FakeHTTPTransport(
        {
            "https://public.example/article": _FakeHTTPResponse(
                status=302,
                headers={"Location": "http://127.0.0.1/admin"},
            )
        }
    )
    policy = URLSafetyPolicy(
        dns_resolver=lambda hostname, port: (
            ["93.184.216.34"] if hostname == "public.example" else ["127.0.0.1"]
        )
    )
    client = TrafilaturaURLArticleParserClient(
        http_transport=transport,
        safety_policy=policy,
    )

    try:
        client._download_html("https://public.example/article")
    except URLArticleParserClientError as exc:
        assert exc.code == "URL_SSRF_BLOCKED"
    else:
        raise AssertionError("redirect to private host must be blocked")
    assert transport.requested_urls == ["https://public.example/article"]


def test_url_parser_enforces_redirect_limit() -> None:
    urls = [f"https://public.example/redirect-{index}" for index in range(MAX_URL_REDIRECTS + 2)]
    responses = {
        url: _FakeHTTPResponse(
            status=302,
            headers={"Location": urls[index + 1]},
        )
        for index, url in enumerate(urls[:-1])
    }
    responses[urls[-1]] = _FakeHTTPResponse(
        headers={"Content-Type": "text/html"},
        body=b"<html>done</html>",
    )
    transport = _FakeHTTPTransport(responses)
    client = TrafilaturaURLArticleParserClient(
        http_transport=transport,
        safety_policy=URLSafetyPolicy(dns_resolver=_resolver(["93.184.216.34"])),
    )

    try:
        client._download_html(urls[0])
    except URLArticleParserClientError as exc:
        assert exc.code == "URL_REDIRECT_LIMIT_EXCEEDED"
    else:
        raise AssertionError("redirect chain must be bounded")
    assert len(transport.requested_urls) == MAX_URL_REDIRECTS + 1


def test_url_parser_rejects_unsupported_content_type() -> None:
    transport = _FakeHTTPTransport(
        {
            "https://public.example/image": _FakeHTTPResponse(
                headers={"Content-Type": "image/png"},
                body=b"not an article",
            )
        }
    )
    client = TrafilaturaURLArticleParserClient(
        http_transport=transport,
        safety_policy=URLSafetyPolicy(dns_resolver=_resolver(["93.184.216.34"])),
    )

    try:
        client._download_html("https://public.example/image")
    except URLArticleParserClientError as exc:
        assert exc.code == "URL_RESPONSE_TYPE_UNSUPPORTED"
    else:
        raise AssertionError("non-text response must be rejected")


def test_url_parser_rejects_oversized_content_length_and_body() -> None:
    for response in (
        _FakeHTTPResponse(
            headers={"Content-Type": "text/html", "Content-Length": "11"},
            body=b"small body",
        ),
        _FakeHTTPResponse(
            headers={"Content-Type": "text/html"},
            body=b"0123456789x",
        ),
    ):
        transport = _FakeHTTPTransport({"https://public.example/large": response})
        client = TrafilaturaURLArticleParserClient(
            http_transport=transport,
            safety_policy=URLSafetyPolicy(dns_resolver=_resolver(["93.184.216.34"])),
            max_response_bytes=10,
        )

        try:
            client._download_html("https://public.example/large")
        except URLArticleParserClientError as exc:
            assert exc.code == "URL_RESPONSE_TOO_LARGE"
        else:
            raise AssertionError("oversized response must be rejected")


def test_url_article_parser_tool_maps_client_error_to_url_fetch_failed() -> None:
    tool = URLArticleParserTool(_FakeURLArticleParserClient(should_fail=True))

    result = asyncio.run(
        tool.run(
            context=ToolContext(workflow_id="wf-3"),
            arguments={"url": "https://example.com/fail"},
        )
    )

    assert result.is_error is True
    assert result.error is not None
    assert result.error.code == "URL_FETCH_FAILED"
    assert result.error.message == "fetch failed"
