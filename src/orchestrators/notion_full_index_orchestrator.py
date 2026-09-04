from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import List, Optional

from pydantic import BaseModel, ValidationError

from src.orchestrators.notion_page_index_orchestrator import (
    NotionPageIndexError,
    NotionPageIndexOrchestrator,
    NotionIndexedPageSnapshot,
)
from src.services import (
    InfrastructureExecutionTimeout,
    STANDARD_FAILURE_REASONS,
    WorkflowRunService,
)
from src.tools import ToolContext, ToolRegistry

NOTION_READER_TOOL_NAME = "notion_reader"


class _DiscoveredPagePayload(BaseModel):
    page_id: str
    title: str = ""
    parent_notion_page_id: Optional[str] = None


@dataclass
class NotionFullIndexedPageResult:
    page_id: str
    page_title: str
    notion_path: str
    indexed_block_count: int


@dataclass
class NotionFullIndexResult:
    workflow_run_id: int
    status: str
    discovered_page_count: int
    processed_page_count: int
    indexed_pages: List[NotionFullIndexedPageResult]


class NotionFullIndexOrchestrator:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        page_index_orchestrator: NotionPageIndexOrchestrator,
        workflow_run_service: WorkflowRunService,
    ) -> None:
        self._tool_registry = tool_registry
        self._page_index_orchestrator = page_index_orchestrator
        self._workflow_run_service = workflow_run_service

    def start_full_index_workflow(self, *, request_workflow_id: str) -> int:
        workflow_run = self._workflow_run_service.start_workflow(
            workflow_type="indexing",
            metadata_json=json.dumps(
                {
                    "operation": "index_full",
                    "request_workflow_id": request_workflow_id,
                },
                sort_keys=True,
            ),
        )
        return int(workflow_run.id)

    async def index_all(
        self,
        *,
        request_workflow_id: str,
        workflow_run_id: Optional[int] = None,
    ) -> NotionFullIndexResult:
        workflow_run_id = workflow_run_id or self.start_full_index_workflow(
            request_workflow_id=request_workflow_id,
        )
        indexed_pages: List[NotionFullIndexedPageResult] = []
        page_ids: List[str] = []
        current_page_id = ""
        failed_page_index: Optional[int] = None

        try:
            page_ids = await self._discover_page_ids(
                request_workflow_id=request_workflow_id,
            )
            for page_index, page_id in enumerate(page_ids):
                current_page_id = page_id
                failed_page_index = page_index
                snapshot = await self._page_index_orchestrator.index_page_snapshot(
                    page_id=page_id,
                    request_workflow_id=request_workflow_id,
                )
                indexed_pages.append(self._to_indexed_page(snapshot))

            self._workflow_run_service.mark_workflow_succeeded(
                workflow_run_id,
                metadata_json=json.dumps(
                    {
                        "operation": "index_full",
                        "discovered_page_count": len(page_ids),
                        "processed_page_count": len(indexed_pages),
                        "page_ids": page_ids,
                    },
                    sort_keys=True,
                ),
            )
        except InfrastructureExecutionTimeout:
            remaining_page_ids = (
                page_ids[failed_page_index + 1 :]
                if failed_page_index is not None
                else page_ids
            )
            self._workflow_run_service.mark_workflow_failed(
                workflow_run_id,
                failure_reason="QUEUE_JOB_TIMEOUT",
                metadata_json=json.dumps(
                    {
                        "operation": "index_full",
                        "discovered_page_count": len(page_ids),
                        "processed_page_count": len(indexed_pages),
                        "succeeded_page_ids": [
                            page.page_id for page in indexed_pages
                        ],
                        "failed_page_id": current_page_id or None,
                        "failed_page_index": failed_page_index,
                        "remaining_page_ids": remaining_page_ids,
                        "error_code": "QUEUE_JOB_TIMEOUT",
                    },
                    sort_keys=True,
                ),
            )
            raise
        except NotionPageIndexError as exc:
            remaining_page_ids = (
                page_ids[failed_page_index + 1 :]
                if failed_page_index is not None
                else page_ids
            )
            self._workflow_run_service.mark_workflow_failed(
                workflow_run_id,
                failure_reason=exc.failure_reason,
                metadata_json=json.dumps(
                    {
                        "operation": "index_full",
                        "discovered_page_count": len(page_ids),
                        "processed_page_count": len(indexed_pages),
                        "succeeded_page_ids": [
                            page.page_id for page in indexed_pages
                        ],
                        "failed_page_id": current_page_id or None,
                        "failed_page_index": failed_page_index,
                        "remaining_page_ids": remaining_page_ids,
                        "error_code": exc.error_code,
                    },
                    sort_keys=True,
                ),
            )
            raise NotionPageIndexError(
                error_code=exc.error_code,
                message=exc.message,
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
                workflow_run_id=workflow_run_id,
            ) from exc

        return NotionFullIndexResult(
            workflow_run_id=workflow_run_id,
            status="succeeded",
            discovered_page_count=len(page_ids),
            processed_page_count=len(indexed_pages),
            indexed_pages=indexed_pages,
        )

    async def _discover_page_ids(self, *, request_workflow_id: str) -> List[str]:
        tool_result = await self._tool_registry.call_tool(
            NOTION_READER_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={
                    "operation": "discover_notion_pages",
                },
            ),
            arguments={"action": "list_pages"},
        )
        if tool_result.is_error:
            error_code = "UNKNOWN_ERROR"
            message = "Notion page discovery failed"
            if tool_result.error is not None:
                error_code = tool_result.error.code
                message = tool_result.error.message
            raise NotionPageIndexError(
                error_code=error_code,
                message=message,
                http_status_code=self._http_status_for_error(error_code),
                failure_reason=self._failure_reason_for_error(error_code),
            )

        try:
            raw_pages = (tool_result.structured_content or {}).get("pages")
            if not isinstance(raw_pages, list):
                raise ValueError("pages is missing")
            page_ids: List[str] = []
            seen_page_ids = set()
            for raw_page in raw_pages:
                page = _DiscoveredPagePayload.model_validate(raw_page)
                normalized_page_id = page.page_id.strip()
                if not normalized_page_id:
                    raise ValueError("page_id must not be empty")
                if normalized_page_id in seen_page_ids:
                    continue
                seen_page_ids.add(normalized_page_id)
                page_ids.append(normalized_page_id)
            return page_ids
        except (TypeError, ValueError, ValidationError) as exc:
            raise NotionPageIndexError(
                error_code="TOOL_OUTPUT_INVALID",
                message=f"Notion page discovery output is invalid: {exc}",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            ) from exc

    @staticmethod
    def _to_indexed_page(
        snapshot: NotionIndexedPageSnapshot,
    ) -> NotionFullIndexedPageResult:
        return NotionFullIndexedPageResult(
            page_id=snapshot.notion_page_id,
            page_title=snapshot.page_title,
            notion_path=snapshot.notion_path,
            indexed_block_count=snapshot.indexed_block_count,
        )

    @staticmethod
    def _http_status_for_error(error_code: str) -> int:
        if error_code == "INVALID_ARGUMENT":
            return HTTPStatus.BAD_REQUEST
        if error_code == "NOTION_PAGE_NOT_FOUND":
            return HTTPStatus.NOT_FOUND
        if error_code in {"NOTION_AUTH_FAILED", "NOTION_BLOCK_FETCH_FAILED"}:
            return HTTPStatus.BAD_GATEWAY
        return HTTPStatus.INTERNAL_SERVER_ERROR

    @staticmethod
    def _failure_reason_for_error(error_code: str) -> str:
        normalized = error_code.strip().upper()
        if normalized in STANDARD_FAILURE_REASONS:
            return normalized
        return "UNKNOWN_ERROR"
