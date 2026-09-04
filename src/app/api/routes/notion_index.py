from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.app.config import get_settings
from src.app.dependencies import (
    build_embedding_batch_service,
    get_cost_tracker,
    get_embedding_client,
    get_tool_registry,
)
from src.app.schemas import (
    NotionFullIndexResponse,
    NotionIncrementalIndexRequest,
    NotionIncrementalIndexResponse,
    NotionIncrementalIndexedPage,
    NotionPageIndexRequest,
    NotionPageIndexResponse,
    NotionIndexStatusResponse,
)
from src.db.session import (
    SessionFactory,
    UnitOfWorkFactory,
    get_db_session_factory,
    get_unit_of_work_factory,
)
from src.orchestrators import (
    NotionIncrementalIndexOrchestrator,
    NotionFullIndexOrchestrator,
    NotionPageIndexError,
    NotionPageIndexOrchestrator,
)
from src.providers import EmbeddingClient
from src.services import CostTracker, WorkflowRunService
from src.tools import ToolRegistry

router = APIRouter()


def _build_index_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    tool_registry: ToolRegistry,
    embedding_client: Optional[EmbeddingClient],
    cost_tracker: CostTracker,
) -> NotionPageIndexOrchestrator:
    return NotionPageIndexOrchestrator(
        tool_registry=tool_registry,
        unit_of_work_factory=unit_of_work_factory,
        workflow_run_service=WorkflowRunService(db_session_factory),
        embedding_batch_service=build_embedding_batch_service(embedding_client),
        cost_tracker=cost_tracker,
        source_is_synthetic=get_settings().notion_backend == "mock",
    )


def _build_incremental_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    tool_registry: ToolRegistry,
    embedding_client: Optional[EmbeddingClient],
    cost_tracker: CostTracker,
) -> NotionIncrementalIndexOrchestrator:
    return NotionIncrementalIndexOrchestrator(
        page_index_orchestrator=_build_index_orchestrator(
            db_session_factory=db_session_factory,
            unit_of_work_factory=unit_of_work_factory,
            tool_registry=tool_registry,
            embedding_client=embedding_client,
            cost_tracker=cost_tracker,
        ),
        workflow_run_service=WorkflowRunService(db_session_factory),
    )


def _build_full_orchestrator(
    *,
    db_session_factory: SessionFactory,
    unit_of_work_factory: UnitOfWorkFactory,
    tool_registry: ToolRegistry,
    embedding_client: Optional[EmbeddingClient],
    cost_tracker: CostTracker,
) -> NotionFullIndexOrchestrator:
    return NotionFullIndexOrchestrator(
        tool_registry=tool_registry,
        page_index_orchestrator=_build_index_orchestrator(
            db_session_factory=db_session_factory,
            unit_of_work_factory=unit_of_work_factory,
            tool_registry=tool_registry,
            embedding_client=embedding_client,
            cost_tracker=cost_tracker,
        ),
        workflow_run_service=WorkflowRunService(db_session_factory),
    )


def _raise_index_error(exc: NotionPageIndexError) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail={
            "error_code": exc.error_code,
            "message": exc.message,
            "failure_reason": exc.failure_reason,
            "workflow_run_id": exc.workflow_run_id,
        },
    ) from exc


def _decode_index_metadata(metadata_json: Optional[str]) -> dict[str, object]:
    if not metadata_json:
        return {}
    try:
        parsed = json.loads(metadata_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


@router.post("/api/notion/index/page", response_model=NotionPageIndexResponse)
async def index_notion_page(
    payload: NotionPageIndexRequest,
    request: Request,
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_unit_of_work_factory),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
    cost_tracker: CostTracker = Depends(get_cost_tracker),
) -> NotionPageIndexResponse:
    orchestrator = _build_index_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        tool_registry=tool_registry,
        embedding_client=embedding_client,
        cost_tracker=cost_tracker,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        result = await orchestrator.index_page(
            page_id=payload.page_id,
            request_workflow_id=request_workflow_id,
        )
    except NotionPageIndexError as exc:
        _raise_index_error(exc)

    return NotionPageIndexResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        page_id=result.notion_page_id,
        page_title=result.page_title,
        notion_path=result.notion_path,
        indexed_block_count=result.indexed_block_count,
    )


@router.post(
    "/api/notion/index/incremental",
    response_model=NotionIncrementalIndexResponse,
)
async def index_notion_incremental(
    payload: NotionIncrementalIndexRequest,
    request: Request,
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_unit_of_work_factory),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
    cost_tracker: CostTracker = Depends(get_cost_tracker),
) -> NotionIncrementalIndexResponse:
    orchestrator = _build_incremental_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        tool_registry=tool_registry,
        embedding_client=embedding_client,
        cost_tracker=cost_tracker,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        result = await orchestrator.sync_pages(
            page_ids=payload.page_ids,
            request_workflow_id=request_workflow_id,
        )
    except NotionPageIndexError as exc:
        _raise_index_error(exc)

    return NotionIncrementalIndexResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        sync_mode=result.sync_mode,
        processed_page_count=result.processed_page_count,
        indexed_pages=[
            NotionIncrementalIndexedPage(
                page_id=page.page_id,
                page_title=page.page_title,
                notion_path=page.notion_path,
                indexed_block_count=page.indexed_block_count,
            )
            for page in result.indexed_pages
        ],
    )


@router.post("/api/notion/index/full", response_model=NotionFullIndexResponse)
async def index_notion_full(
    request: Request,
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
    unit_of_work_factory: UnitOfWorkFactory = Depends(get_unit_of_work_factory),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    embedding_client: Optional[EmbeddingClient] = Depends(get_embedding_client),
    cost_tracker: CostTracker = Depends(get_cost_tracker),
) -> NotionFullIndexResponse:
    orchestrator = _build_full_orchestrator(
        db_session_factory=db_session_factory,
        unit_of_work_factory=unit_of_work_factory,
        tool_registry=tool_registry,
        embedding_client=embedding_client,
        cost_tracker=cost_tracker,
    )
    request_workflow_id = str(getattr(request.state, "workflow_id", ""))

    try:
        result = await orchestrator.index_all(
            request_workflow_id=request_workflow_id,
        )
    except NotionPageIndexError as exc:
        _raise_index_error(exc)

    return NotionFullIndexResponse(
        workflow_run_id=result.workflow_run_id,
        status=result.status,
        discovered_page_count=result.discovered_page_count,
        processed_page_count=result.processed_page_count,
        indexed_pages=[
            NotionIncrementalIndexedPage(
                page_id=page.page_id,
                page_title=page.page_title,
                notion_path=page.notion_path,
                indexed_block_count=page.indexed_block_count,
            )
            for page in result.indexed_pages
        ],
    )


@router.get("/api/notion/index/status", response_model=NotionIndexStatusResponse)
def get_notion_index_status(
    workflow_run_id: Optional[int] = Query(default=None, gt=0),
    db_session_factory: SessionFactory = Depends(get_db_session_factory),
) -> NotionIndexStatusResponse:
    workflow_run_service = WorkflowRunService(db_session_factory)
    workflow_run = (
        workflow_run_service.get_workflow_run(workflow_run_id)
        if workflow_run_id is not None
        else workflow_run_service.get_latest_workflow_run(
            workflow_type="indexing"
        )
    )
    if workflow_run is None or workflow_run.workflow_type != "indexing":
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "NOTION_INDEX_STATUS_NOT_FOUND",
                "message": "Notion index workflow run is not found",
            },
        )
    return NotionIndexStatusResponse(
        workflow_run_id=int(workflow_run.id),
        workflow_type=workflow_run.workflow_type,
        status=workflow_run.status,
        failure_reason=workflow_run.failure_reason,
        started_at=workflow_run.started_at,
        finished_at=workflow_run.finished_at,
        metadata=_decode_index_metadata(workflow_run.metadata_json),
    )
