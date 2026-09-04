from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from src.orchestrators.notion_incremental_index_orchestrator import (
    NotionIncrementalIndexOrchestrator,
)
from src.orchestrators.notion_page_index_orchestrator import NotionPageIndexError
from src.services import (
    NotionHierarchyPage,
    NotionPageHierarchy,
    STANDARD_FAILURE_REASONS,
    TelegramSyncPage,
    TelegramSyncSession,
    TelegramSyncSessionStore,
    WorkflowRunService,
)
from src.services.telegram_sync_session_store import (
    TELEGRAM_SYNC_MAX_DISCOVERED_PAGES,
    TELEGRAM_SYNC_MAX_SELECTED_PAGES,
    new_telegram_sync_session_id,
)
from src.tools import ToolContext, ToolRegistry


NOTION_READER_TOOL_NAME = "notion_reader"


class _DiscoveredPage(BaseModel):
    page_id: str
    title: str = ""
    parent_notion_page_id: Optional[str] = None


@dataclass(frozen=True)
class TelegramSyncView:
    session_id: str
    pages: tuple[TelegramSyncPage, ...]
    selected_page_ids: tuple[str, ...]
    state: str
    discovered_page_count: int
    selected_page_count: int


@dataclass(frozen=True)
class TelegramSyncResult:
    status: str
    reply_text: str
    workflow_run_id: Optional[int]
    discovered_page_count: int
    selected_page_count: int
    succeeded_page_count: int
    failed_page_count: int


class TelegramSyncError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason
        self.metadata = metadata or {}


class TelegramSyncOrchestrator:
    """Coordinate live page discovery, bounded selection, and safe re-indexing."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        session_store: TelegramSyncSessionStore,
        incremental_index_orchestrator: NotionIncrementalIndexOrchestrator,
        workflow_run_service: WorkflowRunService,
    ) -> None:
        self._tool_registry = tool_registry
        self._session_store = session_store
        self._incremental_index_orchestrator = incremental_index_orchestrator
        self._workflow_run_service = workflow_run_service

    async def start_session(
        self,
        *,
        chat_id: str,
        user_id: str,
        request_workflow_id: str,
    ) -> TelegramSyncView:
        pages = await self._discover_pages(request_workflow_id=request_workflow_id)
        if not pages:
            raise TelegramSyncError(
                error_code="NOTION_PAGES_EMPTY",
                message="No accessible Notion pages were found.",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="NOTION_PAGE_NOT_FOUND",
            )
        session = self._session_store.create_session(
            session_id=new_telegram_sync_session_id(),
            chat_id=chat_id,
            user_id=user_id,
            pages=pages,
        )
        return self._to_view(session)

    def get_view(self, *, session_id: str, chat_id: str, user_id: str) -> TelegramSyncView:
        session = self._get_session(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        return self._to_view(session)

    def toggle_page(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        page_id: str,
    ) -> TelegramSyncView:
        status, session = self._session_store.toggle_page(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            page_id=page_id,
        )
        if status == "missing" or session is None:
            raise self._session_error()
        if status == "limit":
            raise TelegramSyncError(
                error_code="INVALID_ARGUMENT",
                message=(
                    f"You can select at most {TELEGRAM_SYNC_MAX_SELECTED_PAGES} pages per sync."
                ),
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            )
        if status == "invalid":
            raise self._session_error()
        return self._to_view(session)

    def cancel_session(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
    ) -> TelegramSyncResult:
        session = self._session_store.cancel(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        if session is None:
            raise self._session_error()
        return TelegramSyncResult(
            status="cancelled",
            reply_text="Notion sync cancelled. No pages were changed.",
            workflow_run_id=session.workflow_run_id,
            discovered_page_count=len(session.pages),
            selected_page_count=len(session.selected_page_ids),
            succeeded_page_count=0,
            failed_page_count=0,
        )

    async def confirm_session(
        self,
        *,
        session_id: str,
        chat_id: str,
        user_id: str,
        request_workflow_id: str,
    ) -> TelegramSyncResult:
        claim_status, session = self._session_store.claim_confirm(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
        )
        if session is None or claim_status == "missing":
            raise self._session_error()
        if claim_status == "empty":
            raise TelegramSyncError(
                error_code="INVALID_ARGUMENT",
                message="Select at least one page before confirming sync.",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            )
        if claim_status != "claimed":
            return TelegramSyncResult(
                status=session.state,
                reply_text="This sync request is already being processed or has finished.",
                workflow_run_id=session.workflow_run_id,
                discovered_page_count=len(session.pages),
                selected_page_count=len(session.selected_page_ids),
                succeeded_page_count=session.succeeded_page_count,
                failed_page_count=session.failed_page_count,
            )

        try:
            index_result = await self._incremental_index_orchestrator.sync_pages(
                page_ids=list(session.selected_page_ids),
                request_workflow_id=request_workflow_id,
            )
        except NotionPageIndexError as exc:
            succeeded_count, failed_count = self._failure_counts(exc)
            state = "partially_failed" if succeeded_count else "failed"
            self._session_store.complete(
                session_id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                state=state,
                workflow_run_id=int(exc.workflow_run_id or 0),
                succeeded_page_count=succeeded_count,
                failed_page_count=failed_count,
                failure_reason=exc.failure_reason,
            )
            return TelegramSyncResult(
                status=state,
                reply_text=self._failure_reply(
                    state=state,
                    succeeded_count=succeeded_count,
                    failed_count=failed_count,
                ),
                workflow_run_id=exc.workflow_run_id,
                discovered_page_count=len(session.pages),
                selected_page_count=len(session.selected_page_ids),
                succeeded_page_count=succeeded_count,
                failed_page_count=failed_count,
            )

        succeeded_count = len(index_result.indexed_pages)
        self._session_store.complete(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            state="succeeded",
            workflow_run_id=index_result.workflow_run_id,
            succeeded_page_count=succeeded_count,
            failed_page_count=0,
        )
        return TelegramSyncResult(
            status="succeeded",
            reply_text=(
                f"Notion sync completed. Re-indexed {succeeded_count} selected "
                "page(s); no Notion content was written."
            ),
            workflow_run_id=index_result.workflow_run_id,
            discovered_page_count=len(session.pages),
            selected_page_count=len(session.selected_page_ids),
            succeeded_page_count=succeeded_count,
            failed_page_count=0,
        )

    async def _discover_pages(self, *, request_workflow_id: str) -> list[TelegramSyncPage]:
        tool_result = await self._tool_registry.call_tool(
            NOTION_READER_TOOL_NAME,
            context=ToolContext(
                workflow_id=request_workflow_id,
                metadata={"operation": "telegram_sync_discovery"},
            ),
            arguments={"action": "list_pages"},
        )
        if tool_result.is_error:
            error_code = tool_result.error.code if tool_result.error else "UNKNOWN_ERROR"
            raise TelegramSyncError(
                error_code=error_code,
                message="Notion page discovery failed.",
                http_status_code=self._http_status_for_error(error_code),
                failure_reason=self._failure_reason_for_error(error_code),
            )
        try:
            raw_pages = (tool_result.structured_content or {}).get("pages")
            if not isinstance(raw_pages, list):
                raise ValueError("pages is missing")
            discovered: list[_DiscoveredPage] = []
            seen: set[str] = set()
            for raw_page in raw_pages:
                page = _DiscoveredPage.model_validate(raw_page)
                page_id = page.page_id.strip()
                if not page_id or page_id in seen:
                    continue
                seen.add(page_id)
                discovered.append(page)
            if len(discovered) > TELEGRAM_SYNC_MAX_DISCOVERED_PAGES:
                raise ValueError(
                    f"at most {TELEGRAM_SYNC_MAX_DISCOVERED_PAGES} pages may be displayed"
                )
        except (TypeError, ValueError, ValidationError) as exc:
            raise TelegramSyncError(
                error_code="UNKNOWN_ERROR",
                message="Notion page discovery returned invalid data.",
                http_status_code=HTTPStatus.BAD_GATEWAY,
                failure_reason="UNKNOWN_ERROR",
            ) from exc

        hierarchy = NotionPageHierarchy.from_pages(
            NotionHierarchyPage(
                page_id=page.page_id.strip(),
                title=self._clean_title(page.title),
                notion_path=f"Knowledge/{self._clean_title(page.title)}",
                parent_page_id=page.parent_notion_page_id,
            )
            for page in discovered
        )
        rendered: list[TelegramSyncPage] = []
        used_paths: dict[str, int] = {}

        def walk(node, parent_path: str) -> None:
            title = self._clean_title(node.page.title)
            display_path = f"{parent_path}/{title}" if parent_path else f"Knowledge/{title}"
            occurrence = used_paths.get(display_path, 0) + 1
            used_paths[display_path] = occurrence
            if occurrence > 1:
                display_path = f"{display_path} [{occurrence}]"
            rendered.append(
                TelegramSyncPage(
                    page_id=node.page.page_id,
                    title=title,
                    display_path=display_path,
                )
            )
            for child in node.children:
                walk(child, display_path)

        for root in hierarchy.roots:
            walk(root, "")
        return rendered

    def _get_session(self, **kwargs) -> TelegramSyncSession:
        session = self._session_store.get_session(**kwargs)
        if session is None:
            raise self._session_error()
        return session

    @staticmethod
    def _to_view(session: TelegramSyncSession) -> TelegramSyncView:
        return TelegramSyncView(
            session_id=session.session_id,
            pages=tuple(session.pages),
            selected_page_ids=tuple(session.selected_page_ids),
            state=session.state,
            discovered_page_count=len(session.pages),
            selected_page_count=len(session.selected_page_ids),
        )

    @staticmethod
    def _clean_title(title: str) -> str:
        normalized = " ".join(str(title or "Untitled Notion Page").split())
        return normalized[:160] or "Untitled Notion Page"

    def _failure_counts(self, exc: NotionPageIndexError) -> tuple[int, int]:
        succeeded_count = 0
        failed_count = 1
        if exc.workflow_run_id is not None:
            workflow_run = self._workflow_run_service.get_workflow_run(exc.workflow_run_id)
            if workflow_run is not None:
                try:
                    metadata = json.loads(workflow_run.metadata_json or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    metadata = {}
                succeeded_count = int(
                    metadata.get("succeeded_page_count", metadata.get("processed_page_count", 0))
                    or 0
                )
                failed_count = 1 if metadata.get("failed_page_id") else 0
        return max(0, succeeded_count), max(0, failed_count)

    @staticmethod
    def _failure_reply(*, state: str, succeeded_count: int, failed_count: int) -> str:
        if state == "partially_failed":
            return (
                f"Notion sync partially completed: {succeeded_count} page(s) were "
                f"saved before {failed_count} page failed. Existing indexed pages "
                "were preserved; retry the failed page later."
            )
        return "Notion sync failed before any selected page was committed. No Notion content was written."

    @staticmethod
    def _session_error() -> TelegramSyncError:
        return TelegramSyncError(
            error_code="INVALID_CALLBACK",
            message="This sync session is invalid or expired. Use /sync to start again.",
            http_status_code=HTTPStatus.GONE,
            failure_reason="INVALID_CALLBACK",
        )

    @staticmethod
    def _http_status_for_error(error_code: str) -> int:
        if error_code == "NOTION_AUTH_FAILED":
            return HTTPStatus.BAD_GATEWAY
        if error_code == "NOTION_PAGE_NOT_FOUND":
            return HTTPStatus.NOT_FOUND
        return HTTPStatus.BAD_GATEWAY

    @staticmethod
    def _failure_reason_for_error(error_code: str) -> str:
        normalized = error_code.strip().upper()
        return normalized if normalized in STANDARD_FAILURE_REASONS else "UNKNOWN_ERROR"
