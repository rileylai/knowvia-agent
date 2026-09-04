from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib import error, request
from urllib.parse import quote, urlencode

from src.observability.external_error import (
    ExternalErrorCategory,
    ExternalErrorDiagnostic,
    classify_http_error,
    parse_retry_after_seconds,
)
from src.rag import BlockPathNode, BlockPathSnapshot, build_block_paths
from src.tools.notion_reader_tool import (
    NotionBlockNode,
    NotionPageTree,
    NotionPageSummary,
    NotionReaderClient,
    NotionReaderClientError,
)

DEFAULT_NOTION_API_BASE_URL = "https://api.notion.com"
DEFAULT_NOTION_VERSION = "2022-06-28"
DEFAULT_NOTION_PAGE_PATH_PREFIX = "Knowledge"
MAX_NOTION_PAGE_SIZE = 100
_NOTION_UUID_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
_RETRYABLE_NOTION_CATEGORIES = frozenset(
    {
        ExternalErrorCategory.TIMEOUT,
        ExternalErrorCategory.TRANSPORT_UNAVAILABLE,
        ExternalErrorCategory.REQUEST_TIMEOUT,
        ExternalErrorCategory.RATE_LIMITED,
        ExternalErrorCategory.UPSTREAM_SERVER_ERROR,
    }
)


@dataclass(frozen=True)
class NotionHTTPResponse:
    status_code: int
    payload: Optional[Dict[str, Any]]
    diagnostic: Optional[ExternalErrorDiagnostic] = None


class NotionHTTPTransportError(Exception):
    """Transport failure without upstream response details."""

    def __init__(
        self,
        message: str = "Notion API transport request failed",
        *,
        diagnostic: Optional[ExternalErrorDiagnostic] = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic
        self.category = diagnostic.category if diagnostic is not None else None
        self.retryable = diagnostic.retryable if diagnostic is not None else False
        self.http_status = diagnostic.http_status if diagnostic is not None else None
        self.retry_after_seconds = (
            diagnostic.retry_after_seconds if diagnostic is not None else None
        )


class NotionHTTPTransport:
    def get_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> NotionHTTPResponse:
        raise NotImplementedError

    def post_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NotionHTTPResponse:
        raise NotionHTTPTransportError("Notion POST transport is not supported")

    def patch_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NotionHTTPResponse:
        raise NotionHTTPTransportError("Notion PATCH transport is not supported")


class UrllibNotionHTTPTransport(NotionHTTPTransport):
    """Small stdlib transport for the Notion REST API."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_NOTION_API_BASE_URL,
        timeout_seconds: float = 10.0,
    ) -> None:
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = normalized_base_url
        self._timeout_seconds = timeout_seconds

    def get_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> NotionHTTPResponse:
        return self._request_json(
            method="GET",
            path=path,
            query=query,
            headers=headers,
            payload=None,
        )

    def post_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NotionHTTPResponse:
        return self._request_json(
            method="POST",
            path=path,
            query=query,
            headers=headers,
            payload=payload,
        )

    def patch_json(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NotionHTTPResponse:
        return self._request_json(
            method="PATCH",
            path=path,
            query=query,
            headers=headers,
            payload=payload,
        )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        payload: Optional[Mapping[str, Any]],
    ) -> NotionHTTPResponse:
        url = f"{self._base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urlencode(dict(query))}"
        request_headers = dict(headers)
        request_body = None
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
            request_body = json.dumps(dict(payload)).encode("utf-8")
        req = request.Request(
            url=url,
            headers=request_headers,
            data=request_body,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                status_code = int(response.status)
                response_body = response.read()
        except error.HTTPError as exc:
            # The body may contain private page text or upstream diagnostics.
            # Read it so the connection can be reused, but never expose it.
            try:
                exc.read()
            except OSError:
                pass
            retry_after = parse_retry_after_seconds(
                exc.headers.get("Retry-After") if exc.headers is not None else None
            )
            diagnostic = classify_http_error(
                status_code=int(exc.code),
                retry_after_seconds=retry_after,
            )
            return NotionHTTPResponse(
                status_code=int(exc.code),
                payload=None,
                diagnostic=diagnostic,
            )
        except TimeoutError:
            raise NotionHTTPTransportError(
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.TIMEOUT,
                    retryable=True,
                )
            ) from None
        except error.URLError as exc:
            category = (
                ExternalErrorCategory.TIMEOUT
                if isinstance(exc.reason, TimeoutError)
                else ExternalErrorCategory.TRANSPORT_UNAVAILABLE
            )
            raise NotionHTTPTransportError(
                diagnostic=ExternalErrorDiagnostic(
                    category=category,
                    retryable=True,
                )
            ) from None
        except OSError:
            raise NotionHTTPTransportError(
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.TRANSPORT_UNAVAILABLE,
                    retryable=True,
                )
            ) from None

        try:
            parsed_payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed_payload = None
        if not isinstance(parsed_payload, dict):
            parsed_payload = None
        return NotionHTTPResponse(status_code=status_code, payload=parsed_payload)


class NotionAPIClientError(NotionReaderClientError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        diagnostic: Optional[ExternalErrorDiagnostic] = None,
    ) -> None:
        super().__init__(code=code, message=message)
        self.diagnostic = diagnostic
        self.category = diagnostic.category if diagnostic is not None else None
        self.retryable = diagnostic.retryable if diagnostic is not None else False
        self.http_status = diagnostic.http_status if diagnostic is not None else None
        self.retry_after_seconds = (
            diagnostic.retry_after_seconds if diagnostic is not None else None
        )


class NotionAPIReaderClient(NotionReaderClient):
    """Read one Notion page and all nested blocks without write capabilities."""

    def __init__(
        self,
        *,
        token: str,
        transport: Optional[NotionHTTPTransport] = None,
        base_url: str = DEFAULT_NOTION_API_BASE_URL,
        notion_version: str = DEFAULT_NOTION_VERSION,
        page_path_prefix: str = DEFAULT_NOTION_PAGE_PATH_PREFIX,
        page_size: int = MAX_NOTION_PAGE_SIZE,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 30.0,
        sleeper: Optional[Callable[[float], None]] = None,
        infrastructure_exception_classifier: Optional[
            Callable[[BaseException], Optional[BaseException]]
        ] = None,
    ) -> None:
        normalized_token = token.strip()
        if not normalized_token:
            raise NotionAPIClientError(
                code="NOTION_NOT_CONFIGURED",
                message="Notion token is not configured. Set NOTION_TOKEN.",
            )
        normalized_version = notion_version.strip()
        if not normalized_version:
            raise ValueError("notion_version must not be empty")
        normalized_prefix = "/".join(
            part.strip() for part in page_path_prefix.split("/") if part.strip()
        )
        if not normalized_prefix:
            raise ValueError("page_path_prefix must not be empty")
        if not 1 <= page_size <= MAX_NOTION_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_NOTION_PAGE_SIZE}")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if not math.isfinite(retry_base_seconds) or retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be positive and finite")
        if not math.isfinite(retry_max_seconds) or retry_max_seconds <= 0:
            raise ValueError("retry_max_seconds must be positive and finite")

        self._token = normalized_token
        self._notion_version = normalized_version
        self._page_path_prefix = normalized_prefix
        self._page_size = page_size
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._sleeper = sleeper or time.sleep
        self._infrastructure_exception_classifier = (
            infrastructure_exception_classifier
        )
        self._transport = transport or UrllibNotionHTTPTransport(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )

    def fetch_page_tree(self, page_id: str) -> Optional[NotionPageTree]:
        normalized_page_id = normalize_notion_page_id(page_id)
        if not normalized_page_id:
            raise NotionAPIClientError(
                code="INVALID_ARGUMENT",
                message="page_id is required",
            )

        page_payload = self._request_json(
            path=f"/v1/pages/{quote(normalized_page_id, safe='')}",
            query={},
            not_found_code="NOTION_PAGE_NOT_FOUND",
        )
        if page_payload is None:
            return None

        title = _extract_page_title(page_payload)
        block_payloads = self._fetch_children(normalized_page_id)
        block_nodes = [
            self._build_block_node(block_payload) for block_payload in block_payloads
        ]
        notion_path = _normalize_path(f"{self._page_path_prefix}/{title}")
        block_paths = build_block_paths(
            page_path=notion_path,
            blocks=block_nodes,
        )
        return NotionPageTree(
            page_id=normalized_page_id,
            title=title,
            notion_path=notion_path,
            parent_notion_page_id=_extract_parent_page_id(page_payload),
            last_edited_time=_parse_datetime(page_payload.get("last_edited_time")),
            blocks=[_to_notion_block_node(block) for block in block_paths],
        )

    def list_pages(self) -> List[NotionPageSummary]:
        pages: List[NotionPageSummary] = []
        cursor: Optional[str] = None
        seen_cursors = set()
        seen_page_ids = set()
        while True:
            payload: Dict[str, Any] = {
                "filter": {"property": "object", "value": "page"},
                "page_size": self._page_size,
            }
            if cursor is not None:
                payload["start_cursor"] = cursor
            response_payload = self._request_json(
                method="POST",
                path="/v1/search",
                query={},
                payload=payload,
            )
            if response_payload is None:
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion discovery response is missing",
                )
            raw_results = response_payload.get("results")
            if not isinstance(raw_results, list):
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion discovery response is invalid",
                )
            for item in raw_results:
                if not isinstance(item, dict) or item.get("object") not in {
                    None,
                    "page",
                }:
                    continue
                page_id = item.get("id")
                if not isinstance(page_id, str) or not page_id.strip():
                    raise NotionAPIClientError(
                        code="NOTION_BLOCK_FETCH_FAILED",
                        message="Notion discovery response is missing page id",
                    )
                normalized_page_id = normalize_notion_page_id(page_id)
                if normalized_page_id in seen_page_ids:
                    continue
                seen_page_ids.add(normalized_page_id)
                pages.append(
                    NotionPageSummary(
                        page_id=normalized_page_id,
                        title=_extract_page_title(item),
                        parent_notion_page_id=_extract_parent_page_id(item),
                        last_edited_time=_parse_datetime(item.get("last_edited_time")),
                    )
                )

            if response_payload.get("has_more") is not True:
                return pages
            next_cursor = response_payload.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion discovery pagination cursor is invalid",
                )
            normalized_cursor = next_cursor.strip()
            if normalized_cursor in seen_cursors:
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion discovery pagination cursor repeated",
                )
            seen_cursors.add(normalized_cursor)
            cursor = normalized_cursor

    def _fetch_children(self, parent_id: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        seen_cursors = set()
        while True:
            query = {"page_size": str(self._page_size)}
            if cursor is not None:
                query["start_cursor"] = cursor
            payload = self._request_json(
                path=f"/v1/blocks/{quote(parent_id, safe='')}/children",
                query=query,
            )
            if payload is None:
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion block response is missing",
                )
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion block response is invalid",
                )
            for item in raw_results:
                if not isinstance(item, dict):
                    raise NotionAPIClientError(
                        code="NOTION_BLOCK_FETCH_FAILED",
                        message="Notion block response is invalid",
                    )
                results.append(item)

            has_more = payload.get("has_more") is True
            next_cursor = payload.get("next_cursor")
            if not has_more:
                return results
            if not isinstance(next_cursor, str) or not next_cursor.strip():
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion block pagination cursor is invalid",
                )
            normalized_cursor = next_cursor.strip()
            if normalized_cursor in seen_cursors:
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion block pagination cursor repeated",
                )
            seen_cursors.add(normalized_cursor)
            cursor = normalized_cursor

    def _build_block_node(self, payload: Dict[str, Any]) -> BlockPathNode:
        block_id = payload.get("id")
        block_type = payload.get("type")
        if not isinstance(block_id, str) or not block_id.strip():
            raise NotionAPIClientError(
                code="NOTION_BLOCK_FETCH_FAILED",
                message="Notion block response is missing block id",
            )
        if not isinstance(block_type, str) or not block_type.strip():
            raise NotionAPIClientError(
                code="NOTION_BLOCK_FETCH_FAILED",
                message="Notion block response is missing block type",
            )

        raw_children: List[Dict[str, Any]] = []
        # Page-reference blocks are indexed as their own pages. Following them
        # here would add the same source block IDs to both page trees.
        if (
            payload.get("has_children") is True
            and block_type.strip() not in {"child_page", "link_to_page"}
        ):
            raw_children = self._fetch_children(block_id.strip())
        return BlockPathNode(
            block_id=block_id.strip(),
            block_type=block_type.strip(),
            content_text=_extract_block_text(payload, block_type.strip()),
            children=[self._build_block_node(child) for child in raw_children],
        )

    def _request_json(
        self,
        *,
        method: str = "GET",
        path: str,
        query: Mapping[str, str],
        payload: Optional[Mapping[str, Any]] = None,
        not_found_code: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": self._notion_version,
            "Accept": "application/json",
        }
        attempt = 0
        while True:
            attempt += 1
            try:
                if method == "POST":
                    response = self._transport.post_json(
                        path=path,
                        query=query,
                        headers=headers,
                        payload=payload or {},
                    )
                else:
                    response = self._transport.get_json(
                        path=path,
                        query=query,
                        headers=headers,
                    )
            except NotionHTTPTransportError as exc:
                diagnostic = exc.diagnostic or ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.TRANSPORT_UNAVAILABLE,
                    retryable=True,
                )
                if self._should_retry(diagnostic, attempt):
                    self._sleeper(self._retry_delay(diagnostic, attempt))
                    continue
                raise NotionAPIClientError(
                    code="NOTION_BLOCK_FETCH_FAILED",
                    message="Notion API transport request failed",
                    diagnostic=diagnostic,
                ) from None
            except Exception as exc:
                infrastructure_error = (
                    self._infrastructure_exception_classifier(exc)
                    if self._infrastructure_exception_classifier is not None
                    else None
                )
                if infrastructure_error is not None:
                    raise infrastructure_error from exc
                raise

            diagnostic = response.diagnostic
            if diagnostic is None and not 200 <= response.status_code < 300:
                diagnostic = classify_http_error(status_code=response.status_code)
            if diagnostic is not None and self._should_retry(diagnostic, attempt):
                self._sleeper(self._retry_delay(diagnostic, attempt))
                continue
            break

        if response.status_code in (401, 403):
            raise NotionAPIClientError(
                code="NOTION_AUTH_FAILED",
                message="Notion authorization failed",
                diagnostic=diagnostic,
            )
        if response.status_code == 404 and not_found_code is not None:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            raise NotionAPIClientError(
                code="NOTION_BLOCK_FETCH_FAILED",
                message="Notion API request failed",
                diagnostic=diagnostic,
            )
        if not isinstance(response.payload, dict):
            raise NotionAPIClientError(
                code="NOTION_BLOCK_FETCH_FAILED",
                message="Notion API response is invalid",
                diagnostic=ExternalErrorDiagnostic(
                    category=ExternalErrorCategory.RESPONSE_INVALID,
                    retryable=False,
                    http_status=response.status_code,
                ),
            )
        return response.payload

    def _should_retry(
        self,
        diagnostic: ExternalErrorDiagnostic,
        attempt: int,
    ) -> bool:
        return bool(
            attempt < self._max_attempts
            and diagnostic.retryable
            and diagnostic.category in _RETRYABLE_NOTION_CATEGORIES
        )

    def _retry_delay(
        self,
        diagnostic: ExternalErrorDiagnostic,
        attempt: int,
    ) -> float:
        backoff = self._retry_base_seconds * (2 ** (attempt - 1))
        retry_after = float(diagnostic.retry_after_seconds or 0)
        return min(self._retry_max_seconds, max(backoff, retry_after))


def _extract_page_title(page_payload: Mapping[str, Any]) -> str:
    properties = page_payload.get("properties")
    if isinstance(properties, dict):
        for property_payload in properties.values():
            if not isinstance(property_payload, dict):
                continue
            title_items = property_payload.get("title")
            if isinstance(title_items, list):
                title = _extract_rich_text(title_items)
                if title:
                    return title
    return "Untitled Notion Page"


def _extract_parent_page_id(page_payload: Mapping[str, Any]) -> Optional[str]:
    """Return a real page parent; workspace/database/block parents are roots."""

    parent = page_payload.get("parent")
    if not isinstance(parent, Mapping):
        return None
    if str(parent.get("type") or "").strip().lower() != "page_id":
        return None
    parent_page_id = parent.get("page_id")
    if not isinstance(parent_page_id, str) or not parent_page_id.strip():
        return None
    return normalize_notion_page_id(parent_page_id)


def normalize_notion_page_id(page_id: str) -> str:
    """Use one stable UUID form for Notion page IDs from config and API results."""
    normalized = page_id.strip()
    compact = normalized.replace("-", "")
    if _NOTION_UUID_HEX_PATTERN.fullmatch(compact):
        return "-".join(
            (
                compact[0:8],
                compact[8:12],
                compact[12:16],
                compact[16:20],
                compact[20:32],
            )
        ).lower()
    return normalized


def _extract_block_text(payload: Mapping[str, Any], block_type: str) -> str:
    content = payload.get(block_type)
    if not isinstance(content, dict):
        return ""
    for key in ("rich_text", "title", "caption"):
        items = content.get(key)
        if isinstance(items, list):
            text = _extract_rich_text(items)
            if text:
                return text
    expression = content.get("expression")
    if isinstance(expression, str):
        return expression.strip()
    url = content.get("url")
    if isinstance(url, str):
        return url.strip()
    return ""


def _extract_rich_text(items: List[Any]) -> str:
    parts: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        plain_text = item.get("plain_text")
        if isinstance(plain_text, str) and plain_text:
            parts.append(plain_text)
            continue
        text_payload = item.get("text")
        if isinstance(text_payload, dict):
            content = text_payload.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
    return "".join(parts).strip()


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_notion_block_node(block: BlockPathSnapshot) -> NotionBlockNode:
    return NotionBlockNode(
        block_id=block.block_id,
        block_type=block.block_type,
        content_text=block.content_text,
        block_path=block.block_path,
        children=[_to_notion_block_node(child) for child in block.children],
    )


def _normalize_path(value: str) -> str:
    return "/".join(part.strip() for part in value.split("/") if part.strip())
