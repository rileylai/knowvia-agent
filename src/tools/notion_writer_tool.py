from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from src.tools.base import Tool
from src.tools.models import ToolContext, ToolResult, ToolSpec


class NotionWriterClientError(Exception):
    pass


class NotionWriterAuthError(NotionWriterClientError):
    pass


class NotionWriterPageNotFoundError(NotionWriterClientError):
    pass


class NotionWritePolicyViolationError(NotionWriterClientError):
    pass


class NotionAppendVerificationError(NotionWriterClientError):
    pass


NOTION_APPEND_VERIFICATION_ATTEMPTS = 3
NOTION_APPEND_IDENTITY_PREFIX = "LearnLoop Change Request: "


@dataclass
class NotionAppendRequest:
    page_id: str
    change_request_id: int
    topic_title: str
    source_display_name: str
    summary: str
    concepts: List[str]
    notes: List[str]
    append_date: str
    idempotency_key: str


@dataclass
class NotionAppendResult:
    page_id: str
    change_request_id: int
    target_path: str
    appended_block_count: int
    created_date_group: bool
    idempotent_replay: bool
    section_lines: List[str]


@dataclass
class InMemoryAISupplementEntry:
    change_request_id: int
    topic_title: str
    source_display_name: str
    summary: str
    concepts: List[str]
    notes: List[str]
    append_date: str
    target_path: str
    idempotency_key: str
    section_lines: List[str]
    appended_block_count: int
    created_date_group: bool


@dataclass
class InMemoryNotionWriteOperation:
    operation: str
    page_id: str
    change_request_id: int
    idempotency_key: str
    target_path: str
    appended_block_count: int
    created_date_group: bool


@dataclass
class InMemoryNotionPageSnapshot:
    page_id: str
    title: str
    notion_path: str
    original_blocks: List[str] = field(default_factory=list)
    ai_supplement_entries: List[InMemoryAISupplementEntry] = field(default_factory=list)


class NotionWriterClient:
    def append_to_ai_supplement_zone(
        self,
        *,
        request: NotionAppendRequest,
    ) -> NotionAppendResult:
        raise NotImplementedError

    def find_ai_supplement_by_identity(
        self,
        *,
        page_id: str,
        idempotency_key: str,
        change_request_id: Optional[int] = None,
    ) -> Optional[NotionAppendResult]:
        raise NotImplementedError


class InMemoryNotionWriterClient(NotionWriterClient):
    def __init__(self, pages: Dict[str, InMemoryNotionPageSnapshot]) -> None:
        self._pages = pages
        self._idempotent_results: Dict[Tuple[str, str], NotionAppendResult] = {}
        self._operations: List[InMemoryNotionWriteOperation] = []

    def append_to_ai_supplement_zone(
        self,
        *,
        request: NotionAppendRequest,
    ) -> NotionAppendResult:
        page = self._pages.get(request.page_id)
        if page is None:
            raise NotionWriterPageNotFoundError(
                f"Notion page is not found: page_id={request.page_id}"
            )

        normalized_page_path = page.notion_path.strip()
        if not normalized_page_path:
            raise NotionWritePolicyViolationError(
                "Target page notion_path is empty and cannot host AI Supplement Zone"
            )

        idempotency_scope = (request.page_id, request.idempotency_key)
        existing_result = self._idempotent_results.get(idempotency_scope)
        if existing_result is not None:
            return replace(existing_result, idempotent_replay=True)
        durable_result = self.find_ai_supplement_by_identity(
            page_id=request.page_id,
            idempotency_key=request.idempotency_key,
        )
        if durable_result is not None:
            self._idempotent_results[idempotency_scope] = durable_result
            return durable_result

        target_path = (
            f"{normalized_page_path}/AI Supplement Zone/"
            f"{request.append_date}/{request.topic_title}"
        )
        if "/AI Supplement Zone/" not in target_path:
            raise NotionWritePolicyViolationError(
                "Append target path must be under AI Supplement Zone"
            )

        created_date_group = not any(
            entry.append_date == request.append_date for entry in page.ai_supplement_entries
        )
        section_lines = self._build_section_lines(request=request)
        # One block represents the topic entry; every Notes item is a separate
        # paragraph in section_lines, so the count follows the rendered shape.
        appended_block_count = len(section_lines) + 1 + int(created_date_group)

        page.ai_supplement_entries.append(
            InMemoryAISupplementEntry(
                change_request_id=request.change_request_id,
                topic_title=request.topic_title,
                source_display_name=request.source_display_name,
                summary=request.summary,
                concepts=list(request.concepts),
                notes=list(request.notes),
                append_date=request.append_date,
                target_path=target_path,
                idempotency_key=request.idempotency_key,
                section_lines=section_lines,
                appended_block_count=appended_block_count,
                created_date_group=created_date_group,
            )
        )
        result = NotionAppendResult(
            page_id=request.page_id,
            change_request_id=request.change_request_id,
            target_path=target_path,
            appended_block_count=appended_block_count,
            created_date_group=created_date_group,
            idempotent_replay=False,
            section_lines=section_lines,
        )
        self._idempotent_results[idempotency_scope] = result
        self._operations.append(
            InMemoryNotionWriteOperation(
                operation="append_ai_supplement_zone",
                page_id=request.page_id,
                change_request_id=request.change_request_id,
                idempotency_key=request.idempotency_key,
                target_path=target_path,
                appended_block_count=appended_block_count,
                created_date_group=created_date_group,
            )
        )
        return result

    def find_ai_supplement_by_identity(
        self,
        *,
        page_id: str,
        idempotency_key: str,
        change_request_id: Optional[int] = None,
    ) -> Optional[NotionAppendResult]:
        page = self._pages.get(page_id)
        if page is None:
            raise NotionWriterPageNotFoundError(
                f"Notion page is not found: page_id={page_id}"
            )

        for entry in page.ai_supplement_entries:
            if entry.idempotency_key != idempotency_key:
                continue
            return NotionAppendResult(
                page_id=page_id,
                change_request_id=entry.change_request_id,
                target_path=entry.target_path,
                appended_block_count=entry.appended_block_count,
                created_date_group=entry.created_date_group,
                idempotent_replay=True,
                section_lines=list(entry.section_lines),
            )
        return None

    def get_page_snapshot(self, page_id: str) -> Optional[InMemoryNotionPageSnapshot]:
        return self._pages.get(page_id)

    def list_operations(
        self,
        *,
        page_id: Optional[str] = None,
    ) -> List[InMemoryNotionWriteOperation]:
        if page_id is None:
            return list(self._operations)
        return [operation for operation in self._operations if operation.page_id == page_id]

    def _build_section_lines(self, *, request: NotionAppendRequest) -> List[str]:
        concepts_text = "; ".join(request.concepts)
        return [
            f"Source: {request.source_display_name}",
            f"Summary: {request.summary}",
            f"Key Concepts: {concepts_text}",
            "Notes:",
            *(f"- {note}" for note in request.notes),
            f"{NOTION_APPEND_IDENTITY_PREFIX}{request.idempotency_key}",
        ]


class NotionWriterTool(Tool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="notion_writer",
            description=(
                "Append accepted supplement content under AI Supplement Zone "
                "using append-only semantics."
            ),
            input_schema={
                "type": "object",
                "required": [
                    "page_id",
                    "change_request_id",
                    "topic_title",
                    "source_display_name",
                    "summary",
                    "concepts",
                    "notes",
                ],
                "properties": {
                    "page_id": {"type": "string"},
                    "change_request_id": {"type": "integer"},
                    "topic_title": {"type": "string"},
                    "source_display_name": {"type": "string"},
                    "summary": {"type": "string"},
                    "concepts": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "array", "items": {"type": "string"}},
                    "append_date": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "required": [
                    "page_id",
                    "change_request_id",
                    "target_path",
                    "appended_block_count",
                    "created_date_group",
                    "idempotent_replay",
                    "section_lines",
                ],
                "properties": {
                    "page_id": {"type": "string"},
                    "change_request_id": {"type": "integer"},
                    "target_path": {"type": "string"},
                    "appended_block_count": {"type": "integer"},
                    "created_date_group": {"type": "boolean"},
                    "idempotent_replay": {"type": "boolean"},
                    "section_lines": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        )

    def __init__(self, notion_writer_client: NotionWriterClient) -> None:
        self._notion_writer_client = notion_writer_client

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        _ = context

        try:
            page_id = self._require_non_empty_string(arguments, "page_id")
            change_request_id = self._parse_positive_int(arguments, "change_request_id")
            topic_title = self._require_non_empty_string(arguments, "topic_title")
            source_display_name = self._require_non_empty_string(
                arguments, "source_display_name"
            )
            summary = self._require_non_empty_string(arguments, "summary")
            concepts = self._parse_string_list(
                arguments,
                key="concepts",
                allow_empty=False,
            )
            notes = self._parse_string_list(
                arguments,
                key="notes",
                allow_empty=True,
            )
            append_date = self._parse_append_date(arguments.get("append_date"))
            idempotency_key = str(
                arguments.get("idempotency_key", f"change-request-{change_request_id}")
            ).strip()
            if not idempotency_key:
                idempotency_key = f"change-request-{change_request_id}"
        except ValueError as exc:
            return ToolResult.failure(
                code="INVALID_ARGUMENT",
                message=str(exc),
            )

        request = NotionAppendRequest(
            page_id=page_id,
            change_request_id=change_request_id,
            topic_title=topic_title,
            source_display_name=source_display_name,
            summary=summary,
            concepts=concepts,
            notes=notes,
            append_date=append_date,
            idempotency_key=idempotency_key,
        )

        try:
            append_result = self._notion_writer_client.append_to_ai_supplement_zone(
                request=request
            )
            append_result = await self._verify_append_visibility(
                request=request,
                append_result=append_result,
            )
        except NotionWriterPageNotFoundError as exc:
            return ToolResult.failure(
                code="NOTION_PAGE_NOT_FOUND",
                message=str(exc),
            )
        except NotionWriterAuthError as exc:
            return ToolResult.failure(
                code="NOTION_AUTH_FAILED",
                message=str(exc),
            )
        except NotionWritePolicyViolationError as exc:
            return ToolResult.failure(
                code="WRITE_POLICY_VIOLATION",
                message=str(exc),
            )
        except NotionAppendVerificationError as exc:
            return ToolResult.failure(
                code="NOTION_APPEND_NOT_VERIFIED",
                message=str(exc),
            )
        except NotionWriterClientError as exc:
            return ToolResult.failure(
                code="UNKNOWN_ERROR",
                message=f"Failed to append supplement content: {exc}",
            )

        return ToolResult.success(
            content=(
                f"appended change_request_id={append_result.change_request_id} "
                f"to {append_result.target_path}"
            ),
            structured_content={
                "page_id": append_result.page_id,
                "change_request_id": append_result.change_request_id,
                "target_path": append_result.target_path,
                "appended_block_count": append_result.appended_block_count,
                "created_date_group": append_result.created_date_group,
                "idempotent_replay": append_result.idempotent_replay,
                "section_lines": append_result.section_lines,
            },
        )

    async def _verify_append_visibility(
        self,
        *,
        request: NotionAppendRequest,
        append_result: NotionAppendResult,
    ) -> NotionAppendResult:
        for attempt in range(NOTION_APPEND_VERIFICATION_ATTEMPTS):
            visible_result = self._notion_writer_client.find_ai_supplement_by_identity(
                page_id=request.page_id,
                idempotency_key=request.idempotency_key,
                change_request_id=request.change_request_id,
            )
            if visible_result is not None:
                if visible_result.change_request_id != request.change_request_id:
                    raise NotionAppendVerificationError(
                        "Notion append identity belongs to a different change request"
                    )
                return replace(
                    visible_result,
                    idempotent_replay=append_result.idempotent_replay,
                )
            if attempt < NOTION_APPEND_VERIFICATION_ATTEMPTS - 1:
                await asyncio.sleep(0)

        raise NotionAppendVerificationError(
            "Notion append was not visible after bounded verification"
        )

    def _require_non_empty_string(self, arguments: Dict[str, Any], key: str) -> str:
        value = str(arguments.get(key, "")).strip()
        if not value:
            raise ValueError(f"{key} is required")
        return value

    def _parse_positive_int(self, arguments: Dict[str, Any], key: str) -> int:
        raw_value = arguments.get(key)
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if value <= 0:
            raise ValueError(f"{key} must be positive")
        return value

    def _parse_string_list(
        self,
        arguments: Dict[str, Any],
        *,
        key: str,
        allow_empty: bool,
    ) -> List[str]:
        raw_items = arguments.get(key)
        if not isinstance(raw_items, list):
            raise ValueError(f"{key} must be a list")
        if not raw_items and not allow_empty:
            raise ValueError(f"{key} must not be empty")

        normalized_items: List[str] = []
        for index, raw_item in enumerate(raw_items, start=1):
            if not isinstance(raw_item, str):
                raise ValueError(f"{key}[{index}] must be a string")
            normalized_item = raw_item.strip()
            if not normalized_item:
                raise ValueError(f"{key}[{index}] must not be empty")
            normalized_items.append(normalized_item)
        return normalized_items

    def _parse_append_date(self, raw_value: Any) -> str:
        if raw_value is None:
            return date.today().isoformat()
        normalized = str(raw_value).strip()
        if not normalized:
            return date.today().isoformat()
        try:
            parsed = date.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("append_date must be in YYYY-MM-DD format") from exc
        return parsed.isoformat()
