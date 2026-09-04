from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Iterable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.tools.base import Tool
from src.tools.models import ToolContext, ToolResult, ToolSpec


@dataclass
class ParsedURLArticle:
    url: str
    raw_text: str
    title: Optional[str] = None


class URLArticleParserClientError(Exception):
    def __init__(self, message: str, *, code: str = "URL_FETCH_FAILED") -> None:
        super().__init__(message)
        self.code = code


MAX_URL_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_URL_REDIRECTS = 5
URL_FETCH_TIMEOUT_SECONDS = 30.0
MAX_URL_LENGTH = 2048
MAX_URL_TITLE_CHARS = 512
SUPPORTED_URL_CONTENT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain"}
)


def _is_public_ip_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # is_global is intentionally stricter than only checking RFC1918. It also
    # excludes loopback, link-local, multicast, unspecified, and reserved IPs.
    return address.is_global


def _default_dns_resolver(hostname: str, port: int) -> Iterable[str]:
    try:
        records = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise URLArticleParserClientError(
            "URL host DNS resolution failed",
            code="URL_DNS_RESOLUTION_FAILED",
        ) from exc
    return {record[4][0] for record in records}


class URLSafetyPolicy:
    """Deterministic URL validation used immediately before each HTTP request."""

    def __init__(
        self,
        *,
        dns_resolver: Optional[Callable[[str, int], Iterable[str]]] = None,
    ) -> None:
        self._dns_resolver = dns_resolver or _default_dns_resolver

    def validate_syntax(self, url: str) -> None:
        if len(url) > MAX_URL_LENGTH:
            raise URLArticleParserClientError(
                "url exceeds the maximum length",
                code="INVALID_ARGUMENT",
            )
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise URLArticleParserClientError(
                "url must be an absolute http/https URL",
                code="INVALID_ARGUMENT",
            )
        if parsed.username is not None or parsed.password is not None:
            raise URLArticleParserClientError(
                "URL credentials are not supported",
                code="INVALID_ARGUMENT",
            )
        try:
            hostname = parsed.hostname
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except ValueError as exc:
            raise URLArticleParserClientError(
                "URL host or port is invalid",
                code="INVALID_ARGUMENT",
            ) from exc
        if not hostname or any(character.isspace() for character in hostname):
            raise URLArticleParserClientError(
                "URL host is invalid",
                code="INVALID_ARGUMENT",
            )
        if hostname.lower().rstrip(".") == "localhost":
            raise URLArticleParserClientError(
                "URL host is not allowed",
                code="URL_SSRF_BLOCKED",
            )

        try:
            literal_address = ipaddress.ip_address(hostname)
        except ValueError:
            literal_address = None
        if literal_address is not None and not literal_address.is_global:
            raise URLArticleParserClientError(
                "URL host resolves to a non-public IP address",
                code="URL_SSRF_BLOCKED",
            )

    def validate(self, url: str) -> None:
        self.validate_syntax(url)
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if hostname is None:
            raise URLArticleParserClientError(
                "URL host is invalid",
                code="INVALID_ARGUMENT",
            )
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            resolved_addresses = set(self._dns_resolver(hostname, port))
        except URLArticleParserClientError:
            raise
        except (OSError, ValueError) as exc:
            raise URLArticleParserClientError(
                "URL host DNS resolution failed",
                code="URL_DNS_RESOLUTION_FAILED",
            ) from exc
        if not resolved_addresses:
            raise URLArticleParserClientError(
                "URL host DNS resolution returned no addresses",
                code="URL_DNS_RESOLUTION_FAILED",
            )
        if any(not _is_public_ip_address(address) for address in resolved_addresses):
            raise URLArticleParserClientError(
                "URL host resolves to a non-public IP address",
                code="URL_SSRF_BLOCKED",
            )


class URLHTTPTransport:
    def open(self, *, url: str, timeout_seconds: float) -> Any:
        raise NotImplementedError


class _NoRedirectHandler(HTTPRedirectHandler):
    def _return_redirect_response(
        self,
        request: Request,
        response: Any,
        code: int,
        message: str,
        headers: Any,
    ) -> Any:
        _ = request
        _ = code
        _ = message
        _ = headers
        return response

    http_error_301 = _return_redirect_response
    http_error_302 = _return_redirect_response
    http_error_303 = _return_redirect_response
    http_error_307 = _return_redirect_response
    http_error_308 = _return_redirect_response


class UrllibURLHTTPTransport(URLHTTPTransport):
    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirectHandler())

    def open(self, *, url: str, timeout_seconds: float) -> Any:
        request = Request(
            url=url,
            headers={"User-Agent": "LearnLoopAgent/0.1 (+https://local.learnloop)"},
            method="GET",
        )
        try:
            return self._opener.open(request, timeout=timeout_seconds)
        except HTTPError as exc:
            raise URLArticleParserClientError(
                "URL fetch returned an unsuccessful response",
                code="URL_FETCH_FAILED",
            ) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise URLArticleParserClientError(
                "URL fetch failed",
                code="URL_FETCH_FAILED",
            ) from exc


class URLArticleParserClient:
    def parse_article(self, *, url: str) -> ParsedURLArticle:
        raise NotImplementedError


class TrafilaturaURLArticleParserClient(URLArticleParserClient):
    def __init__(
        self,
        *,
        http_transport: Optional[URLHTTPTransport] = None,
        safety_policy: Optional[URLSafetyPolicy] = None,
        max_response_bytes: int = MAX_URL_RESPONSE_BYTES,
        max_redirects: int = MAX_URL_REDIRECTS,
        timeout_seconds: float = URL_FETCH_TIMEOUT_SECONDS,
    ) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._http_transport = http_transport or UrllibURLHTTPTransport()
        self._safety_policy = safety_policy or URLSafetyPolicy()
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._timeout_seconds = timeout_seconds

    def parse_article(self, *, url: str) -> ParsedURLArticle:
        try:
            import trafilatura
        except ModuleNotFoundError as exc:
            raise URLArticleParserClientError("trafilatura dependency is missing") from exc

        downloaded = self._download_article(url)
        html = downloaded.body
        if downloaded.content_type == "text/plain":
            extracted = html
        else:
            try:
                extracted = trafilatura.extract(html)
            except Exception as exc:
                raise URLArticleParserClientError("Failed to extract article text") from exc

        if extracted is None:
            raise URLArticleParserClientError("No extractable text found in URL article")

        normalized_text = extracted.strip()
        if not normalized_text:
            raise URLArticleParserClientError("No extractable text found in URL article")

        return ParsedURLArticle(
            url=downloaded.final_url,
            raw_text=normalized_text,
            title=_extract_html_title(html),
        )

    def _download_html(self, url: str) -> str:
        return self._download_article(url).body

    def _download_article(self, url: str) -> "_DownloadedURLArticle":
        current_url = url
        redirect_count = 0
        while True:
            self._safety_policy.validate(current_url)
            try:
                response = self._http_transport.open(
                    url=current_url,
                    timeout_seconds=self._timeout_seconds,
                )
            except URLArticleParserClientError:
                raise
            except Exception as exc:
                raise URLArticleParserClientError("URL fetch failed") from exc

            try:
                status_code = int(
                    getattr(response, "status", getattr(response, "status_code", 200))
                )
                headers = getattr(response, "headers", {})
                if status_code in {301, 302, 303, 307, 308}:
                    location = headers.get("Location")
                    if not location:
                        raise URLArticleParserClientError("URL redirect has no location")
                    if redirect_count >= self._max_redirects:
                        raise URLArticleParserClientError(
                            "URL redirect limit exceeded",
                            code="URL_REDIRECT_LIMIT_EXCEEDED",
                        )
                    current_url = urljoin(current_url, str(location))
                    redirect_count += 1
                    continue

                if status_code < 200 or status_code >= 300:
                    raise URLArticleParserClientError(
                        "URL fetch returned an unsuccessful response",
                        code="URL_FETCH_FAILED",
                    )

                content_type = str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
                if content_type not in SUPPORTED_URL_CONTENT_TYPES:
                    raise URLArticleParserClientError(
                        "URL response content type is not supported",
                        code="URL_RESPONSE_TYPE_UNSUPPORTED",
                    )
                content_length = headers.get("Content-Length")
                if content_length is not None:
                    try:
                        if int(content_length) > self._max_response_bytes:
                            raise URLArticleParserClientError(
                                "URL response exceeds the size limit",
                                code="URL_RESPONSE_TOO_LARGE",
                            )
                    except ValueError:
                        pass
                body = self._read_bounded_body(response)
                charset = self._get_charset(headers)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            break

        try:
            decoded_body = body.decode(charset, errors="replace")
        except (LookupError, UnicodeError) as exc:
            raise URLArticleParserClientError("Failed to decode URL content") from exc
        return _DownloadedURLArticle(
            final_url=current_url,
            body=decoded_body,
            content_type=content_type,
        )

    def _read_bounded_body(self, response: Any) -> bytes:
        chunks = []
        total_bytes = 0
        while True:
            chunk = response.read(
                min(64 * 1024, self._max_response_bytes - total_bytes + 1)
            )
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
            if total_bytes > self._max_response_bytes:
                raise URLArticleParserClientError(
                    "URL response exceeds the size limit",
                    code="URL_RESPONSE_TOO_LARGE",
                )
        return b"".join(chunks)

    def _get_charset(self, headers: Mapping[str, Any]) -> str:
        content_type = str(headers.get("Content-Type", ""))
        for part in content_type.split(";")[1:]:
            name, separator, value = part.strip().partition("=")
            if separator and name.lower() == "charset" and value.strip():
                return value.strip().strip('"\'')
        return "utf-8"


class URLArticleParserTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="url_article_parser",
            description="Fetch one URL article and extract normalized plain text.",
            input_schema={
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": ["url", "raw_text", "char_count"],
                "properties": {
                    "url": {"type": "string"},
                    "final_url": {"type": "string"},
                    "title": {"type": ["string", "null"]},
                    "raw_text": {"type": "string"},
                    "char_count": {"type": "integer"},
                },
            },
        )

    def __init__(self, parser_client: URLArticleParserClient) -> None:
        self._parser_client = parser_client

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        _ = context
        url = str(arguments.get("url", "")).strip()
        if not url:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message="url is required",
            )
        try:
            URLSafetyPolicy().validate_syntax(url)
        except URLArticleParserClientError as exc:
            return ToolResult.failure(
                code=exc.code,
                message=str(exc),
            )

        try:
            parsed = self._parser_client.parse_article(url=url)
        except URLArticleParserClientError as exc:
            return ToolResult.failure(
                code=exc.code,
                message=str(exc),
            )

        normalized_raw_text = parsed.raw_text.strip()
        if not normalized_raw_text:
            return ToolResult.failure(
                code="URL_FETCH_FAILED",
                message="No extractable text found in URL article",
            )

        return ToolResult.success(
            content=f"parsed URL article char_count={len(normalized_raw_text)}",
            structured_content={
                "url": parsed.url,
                "final_url": parsed.url,
                "title": parsed.title,
                "raw_text": normalized_raw_text,
                "char_count": len(normalized_raw_text),
            },
        )

    def _is_supported_url(self, value: str) -> bool:
        try:
            URLSafetyPolicy().validate_syntax(value)
        except URLArticleParserClientError:
            return False
        return True


@dataclass(frozen=True)
class _DownloadedURLArticle:
    final_url: str
    body: str
    content_type: str


class _HTMLTitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        _ = attrs
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and len("".join(self._parts)) < MAX_URL_TITLE_CHARS:
            self._parts.append(data)

    def title(self) -> Optional[str]:
        normalized = " ".join("".join(self._parts).split())
        return normalized[:MAX_URL_TITLE_CHARS] or None


def _extract_html_title(html: str) -> Optional[str]:
    parser = _HTMLTitleParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return None
    return parser.title()


def canonicalize_url(url: str) -> str:
    """Normalize only URL syntax used for deterministic source identity."""
    URLSafetyPolicy().validate_syntax(url)
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if hostname is None:
        raise URLArticleParserClientError(
            "URL host is invalid",
            code="INVALID_ARGUMENT",
        )
    normalized_host = hostname.lower().rstrip(".")
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    port = parsed.port
    default_port = 443 if scheme == "https" else 80
    netloc = normalized_host
    if port is not None and port != default_port:
        netloc = f"{netloc}:{port}"
    return urlunsplit(
        (
            scheme,
            netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
