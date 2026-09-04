"""Guarded, target-scoped Step 97 single-page live verification.

The default path performs no external request. The live path reads one
explicitly configured Notion page, preflights the complete production batch
plan, executes embedding requests sequentially, and replaces only that page's
derived snapshot in the configured local database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUN_FLAG_ENV = "LEARNLOOP_RUN_LARGE_PAGE_RELIABILITY"
PAGE_ID_ENV = "LEARNLOOP_NOTION_RELIABILITY_PAGE_ID"
NOTION_TOKEN_ENV = "NOTION_TOKEN"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
DATABASE_URL_ENV = "DATABASE_URL"
DEFAULT_MAX_REQUEST_COUNT = 8
DEFAULT_TOTAL_TOKEN_ESTIMATE_BUDGET = 1_000_000

SafeReport = dict[str, Any]
LiveRunner = Callable[[Mapping[str, str], int, int], SafeReport]


def run_guarded_verification(
    *,
    include_live: bool,
    approved: bool,
    environment: Mapping[str, str],
    max_request_count: int = DEFAULT_MAX_REQUEST_COUNT,
    total_token_estimate_budget: int = DEFAULT_TOTAL_TOKEN_ESTIMATE_BUDGET,
    live_runner: Optional[LiveRunner] = None,
) -> SafeReport:
    if not include_live:
        return {
            "status": "skipped",
            "message": "live single-page reliability verification is disabled",
        }
    if not approved or environment.get(RUN_FLAG_ENV, "").strip() != "1":
        return {
            "status": "failed",
            "message": "live single-page reliability verification requires approval",
        }
    if max_request_count < 1 or total_token_estimate_budget < 1:
        return {
            "status": "failed",
            "message": "live verification request budget is invalid",
        }
    required = (
        NOTION_TOKEN_ENV,
        OPENAI_API_KEY_ENV,
        PAGE_ID_ENV,
        DATABASE_URL_ENV,
    )
    if any(not environment.get(name, "").strip() for name in required):
        return {
            "status": "failed",
            "message": "live single-page reliability configuration is incomplete",
        }

    selected_runner = live_runner or _run_live
    try:
        return selected_runner(
            environment,
            max_request_count,
            total_token_estimate_budget,
        )
    except Exception:
        return {
            "status": "failed",
            "message": "single-page reliability verification failed",
        }


def _run_live(
    environment: Mapping[str, str],
    max_request_count: int,
    total_token_estimate_budget: int,
) -> SafeReport:
    return asyncio.run(
        _run_live_async(
            environment=environment,
            max_request_count=max_request_count,
            total_token_estimate_budget=total_token_estimate_budget,
        )
    )


async def _run_live_async(
    *,
    environment: Mapping[str, str],
    max_request_count: int,
    total_token_estimate_budget: int,
) -> SafeReport:
    from src.app.config import get_settings
    from src.app.dependencies import build_embedding_batch_service
    from src.db.models import KnowledgeChunk, NotionBlock, NotionPage
    from src.db.session import SessionLocal
    from src.db.unit_of_work import SqlAlchemyUnitOfWork
    from src.orchestrators import NotionPageIndexError, NotionPageIndexOrchestrator
    from src.providers import OpenAIEmbeddingClient
    from src.services import CostTracker, WorkflowRunService
    from src.tools import (
        InMemoryNotionReaderClient,
        NotionAPIReaderClient,
        NotionReaderTool,
        ToolContext,
        ToolRegistry,
    )

    from tests.evals.large_page_failure_diagnostic import _build_all_chunk_inputs

    settings = get_settings()
    target_page_id = environment[PAGE_ID_ENV].strip()
    reader = NotionAPIReaderClient(
        token=environment[NOTION_TOKEN_ENV].strip(),
        timeout_seconds=settings.notion_request_timeout_seconds,
        max_attempts=settings.notion_read_max_attempts,
        retry_base_seconds=settings.notion_read_retry_base_seconds,
        retry_max_seconds=settings.notion_read_retry_max_seconds,
    )
    try:
        page_tree = reader.fetch_page_tree(target_page_id)
    except Exception:
        return {"status": "failed", "message": "Notion preflight failed"}
    if page_tree is None:
        return {"status": "failed", "message": "Notion preflight failed"}

    replay_reader = InMemoryNotionReaderClient({page_tree.page_id: page_tree})
    registry = ToolRegistry()
    registry.register_tool(NotionReaderTool(replay_reader))
    tool_result = await registry.call_tool(
        "notion_reader",
        context=ToolContext(workflow_id="step-97-live-preflight"),
        arguments={"page_id": page_tree.page_id},
    )
    if tool_result.is_error or tool_result.structured_content is None:
        return {"status": "failed", "message": "Notion preflight failed"}
    inputs = _build_all_chunk_inputs(tool_result.structured_content)

    embedding_client = OpenAIEmbeddingClient(
        api_key=environment[OPENAI_API_KEY_ENV].strip()
    )
    service = build_embedding_batch_service(embedding_client, settings=settings)
    if service is None:
        return {"status": "failed", "message": "embedding service is unavailable"}
    try:
        plan = service.plan(inputs)
    except Exception:
        return {"status": "failed", "message": "embedding preflight failed"}
    if len(plan.batches) <= 1:
        return {
            "status": "failed",
            "message": "live verification requires a multi-batch page",
        }
    maximum_attempts = settings.embedding_request_max_attempts
    maximum_request_count = len(plan.batches) * maximum_attempts
    maximum_token_estimate = plan.aggregate_tokens * maximum_attempts
    if (
        maximum_request_count > max_request_count
        or maximum_token_estimate > total_token_estimate_budget
    ):
        return {
            "status": "failed",
            "message": "complete batch plan exceeds the approved request budget",
        }

    started = time.monotonic()
    orchestrator = NotionPageIndexOrchestrator(
        tool_registry=registry,
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(SessionLocal),
        workflow_run_service=WorkflowRunService(SessionLocal),
        embedding_batch_service=service,
        cost_tracker=CostTracker(),
    )
    try:
        snapshot = await orchestrator.index_page_snapshot(
            page_id=page_tree.page_id,
            request_workflow_id="step-97-live-verification",
        )
    except NotionPageIndexError as exc:
        return {
            "status": "failed",
            "message": "single-page reliability verification failed",
            "failure_reason": exc.failure_reason,
        }

    session = SessionLocal()
    try:
        persisted_page = (
            session.query(NotionPage)
            .filter(NotionPage.notion_page_id == page_tree.page_id)
            .one_or_none()
        )
        if persisted_page is None:
            return {
                "status": "failed",
                "message": "persisted page verification failed",
            }
        persisted_block_count = (
            session.query(NotionBlock)
            .filter(NotionBlock.notion_page_id == persisted_page.id)
            .count()
        )
        page_chunks = (
            session.query(KnowledgeChunk)
            .join(NotionBlock, KnowledgeChunk.notion_block_id == NotionBlock.id)
            .filter(NotionBlock.notion_page_id == persisted_page.id)
        )
        persisted_chunk_count = page_chunks.count()
        persisted_vector_count = page_chunks.filter(
            KnowledgeChunk.embedding.is_not(None)
        ).count()
    except Exception:
        return {
            "status": "failed",
            "message": "persisted page verification failed",
        }
    finally:
        session.close()

    expected_chunk_count = plan.input_count
    if (
        persisted_block_count != snapshot.indexed_block_count
        or persisted_chunk_count != expected_chunk_count
        or persisted_vector_count != expected_chunk_count
    ):
        return {
            "status": "failed",
            "message": "persisted page counts do not match the completed plan",
        }

    return {
        "status": "passed",
        "provider": snapshot.embedding_provider,
        "model": snapshot.embedding_model,
        "dimensions": snapshot.embedding_dimensions,
        "input_count": plan.input_count,
        "batch_count": len(plan.batches),
        "batch_input_counts": [batch.input_count for batch in plan.batches],
        "retry_count": snapshot.embedding_retry_count,
        "aggregate_input_bytes": plan.aggregate_bytes,
        "aggregate_input_token_estimate": plan.aggregate_tokens,
        "token_estimator_version": plan.tokenizer_version,
        "provider_token_input": snapshot.embedding_token_input,
        "estimated_cost_usd": snapshot.embedding_estimated_cost,
        "indexed_block_count": persisted_block_count,
        "indexed_chunk_count": persisted_chunk_count,
        "indexed_vector_count": persisted_vector_count,
        "duration_seconds": round(time.monotonic() - started, 3),
        "page_replacement_committed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--max-request-count",
        type=int,
        default=DEFAULT_MAX_REQUEST_COUNT,
    )
    parser.add_argument(
        "--total-token-estimate-budget",
        type=int,
        default=DEFAULT_TOTAL_TOKEN_ESTIMATE_BUDGET,
    )
    args = parser.parse_args()
    report = run_guarded_verification(
        include_live=args.live,
        approved=args.approve,
        environment=os.environ,
        max_request_count=args.max_request_count,
        total_token_estimate_budget=args.total_token_estimate_budget,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"status={report['status']} message={report.get('message', '')}")
    return 0 if report["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
