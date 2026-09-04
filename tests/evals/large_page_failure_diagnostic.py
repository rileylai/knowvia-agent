"""Opt-in, read-only large-page embedding failure diagnostic.

The command keeps Notion content and embedding payloads in memory. Its report
contains only fixed status values and allowlisted request-shape diagnostics.
It does not persist pages, blocks, chunks, vectors, or provider responses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.observability.external_error import ExternalErrorCategory  # noqa: E402
from src.providers import (  # noqa: E402
    EmbeddingClient,
    EmbeddingClientError,
    EmbeddingRequest,
    OpenAIEmbeddingClient,
    build_embedding_request_diagnostics,
)
from src.rag import ChunkerBlock, ChunkerPage, chunk_notion_page  # noqa: E402
from src.tools import (  # noqa: E402
    NotionAPIReaderClient,
    NotionReaderClient,
    NotionReaderTool,
    ToolContext,
    ToolRegistry,
)

RUN_FLAG_ENV = "LEARNLOOP_RUN_LARGE_PAGE_DIAGNOSTIC"
NOTION_TOKEN_ENV = "NOTION_TOKEN"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
PAGE_ID_ENV = "LEARNLOOP_NOTION_DIAGNOSTIC_PAGE_ID"
DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 1536
DEFAULT_SMALL_BATCH_COUNT = 4
DEFAULT_BOUNDED_BATCH_COUNT = 64
MAX_BOUNDED_BATCH_COUNT = 512
DEFAULT_MAX_AGGREGATE_BYTES = 1_000_000
DEFAULT_MAX_AGGREGATE_TOKEN_ESTIMATE = 250_000
DEFAULT_MAX_REQUEST_COUNT = 8
DEFAULT_TOTAL_TOKEN_ESTIMATE_BUDGET = 500_000
DIAGNOSTIC_NOTION_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class DiagnosticCaseResult:
    diagnostic_case: str
    status: str
    endpoint_class: str
    provider_name: str
    model: str
    dimensions: int
    input_count: int
    empty_input_count: int
    max_single_input_chars: int
    max_single_input_bytes: int
    max_single_input_token_estimate: int
    aggregate_input_bytes: int
    aggregate_input_token_estimate: int
    input_size_estimator_version: str
    duration_ms: int
    http_status: Optional[int] = None
    provider_error_category: Optional[str] = None
    retryable: Optional[bool] = None
    retry_after_seconds: Optional[int] = None


@dataclass(frozen=True)
class LargePageDiagnosticReport:
    status: str
    diagnosis: str
    message: str
    cases: List[DiagnosticCaseResult] = field(default_factory=list)

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "diagnosis": self.diagnosis,
            "message": self.message,
            "cases": [asdict(case) for case in self.cases],
        }


@dataclass(frozen=True)
class FullRequestShape:
    total_input_count: int
    empty_input_count: int
    aggregate_input_bytes: int
    aggregate_input_token_estimate: int
    max_single_input_bytes: int
    max_single_input_chars: int
    max_single_input_token_estimate: int
    p50_input_bytes: int
    p95_input_bytes: int
    p99_input_bytes: int
    p50_input_chars: int
    p95_input_chars: int
    p99_input_chars: int
    p50_input_token_estimate: int
    p95_input_token_estimate: int
    p99_input_token_estimate: int
    largest_input_ordinal: Optional[int]
    input_size_estimator_version: str

    def to_safe_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ShapeInspectionError(Exception):
    pass


async def run_shape_inspection_workflow(
    *,
    reader_client: NotionReaderClient,
    target_page_id: str,
) -> FullRequestShape:
    registry = ToolRegistry()
    registry.register_tool(NotionReaderTool(reader_client))
    tool_result = await registry.call_tool(
        "notion_reader",
        context=ToolContext(workflow_id="large-page-shape-inspection"),
        arguments={"page_id": target_page_id},
    )
    if tool_result.is_error or tool_result.structured_content is None:
        raise ShapeInspectionError("read-only Notion shape inspection failed")

    chunk_inputs = _build_all_chunk_inputs(tool_result.structured_content)
    if not chunk_inputs:
        raise ShapeInspectionError("no embedding inputs were produced")
    return build_full_request_shape(chunk_inputs)


def build_full_request_shape(inputs: List[str]) -> FullRequestShape:
    diagnostics = build_embedding_request_diagnostics(
        inputs=inputs,
        provider_name="openai",
        model=DEFAULT_MODEL,
        dimensions=DEFAULT_DIMENSIONS,
        endpoint_class="openai_embeddings",
    )
    byte_sizes = [len(value.encode("utf-8")) for value in inputs]
    char_sizes = [len(value) for value in inputs]
    token_estimates = [
        build_embedding_request_diagnostics(
            inputs=[value],
            provider_name="openai",
            model=DEFAULT_MODEL,
            dimensions=DEFAULT_DIMENSIONS,
            endpoint_class="openai_embeddings",
        ).max_single_input_token_estimate
        for value in inputs
    ]
    largest_input_ordinal = (
        byte_sizes.index(max(byte_sizes)) + 1 if byte_sizes else None
    )
    return FullRequestShape(
        total_input_count=diagnostics.input_count,
        empty_input_count=diagnostics.empty_input_count,
        aggregate_input_bytes=diagnostics.aggregate_input_bytes,
        aggregate_input_token_estimate=(
            diagnostics.aggregate_input_token_estimate
        ),
        max_single_input_bytes=diagnostics.max_single_input_bytes,
        max_single_input_chars=diagnostics.max_single_input_chars,
        max_single_input_token_estimate=(
            diagnostics.max_single_input_token_estimate
        ),
        p50_input_bytes=_nearest_rank(byte_sizes, 50),
        p95_input_bytes=_nearest_rank(byte_sizes, 95),
        p99_input_bytes=_nearest_rank(byte_sizes, 99),
        p50_input_chars=_nearest_rank(char_sizes, 50),
        p95_input_chars=_nearest_rank(char_sizes, 95),
        p99_input_chars=_nearest_rank(char_sizes, 99),
        p50_input_token_estimate=_nearest_rank(token_estimates, 50),
        p95_input_token_estimate=_nearest_rank(token_estimates, 95),
        p99_input_token_estimate=_nearest_rank(token_estimates, 99),
        largest_input_ordinal=largest_input_ordinal,
        input_size_estimator_version=(
            diagnostics.input_size_estimator_version
        ),
    )


def _nearest_rank(values: List[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100) * len(ordered)))
    return ordered[rank - 1]


async def run_diagnostic_workflow(
    *,
    reader_client: NotionReaderClient,
    embedding_client: EmbeddingClient,
    target_page_id: str,
    bounded_batch_count: int,
    max_aggregate_bytes: int = DEFAULT_MAX_AGGREGATE_BYTES,
    max_aggregate_token_estimate: int = DEFAULT_MAX_AGGREGATE_TOKEN_ESTIMATE,
    max_request_count: int = DEFAULT_MAX_REQUEST_COUNT,
    total_token_estimate_budget: int = DEFAULT_TOTAL_TOKEN_ESTIMATE_BUDGET,
) -> LargePageDiagnosticReport:
    registry = ToolRegistry()
    registry.register_tool(NotionReaderTool(reader_client))
    tool_result = await registry.call_tool(
        "notion_reader",
        context=ToolContext(workflow_id="large-page-failure-diagnostic"),
        arguments={"page_id": target_page_id},
    )
    if tool_result.is_error or tool_result.structured_content is None:
        return LargePageDiagnosticReport(
            status="failed",
            diagnosis="unresolved",
            message="read-only Notion diagnostic failed",
        )

    chunk_inputs = _build_chunk_inputs(tool_result.structured_content)
    if not chunk_inputs:
        return LargePageDiagnosticReport(
            status="failed",
            diagnosis="established",
            message="no non-empty embedding inputs were produced",
        )

    cases = _build_case_counts(
        available_count=len(chunk_inputs),
        bounded_batch_count=bounded_batch_count,
    )
    results: List[DiagnosticCaseResult] = []
    consumed_token_estimate = 0
    for case_name, input_count in cases:
        selected_inputs = chunk_inputs[:input_count]
        shape = build_embedding_request_diagnostics(
            inputs=selected_inputs,
            provider_name="openai",
            model=DEFAULT_MODEL,
            dimensions=DEFAULT_DIMENSIONS,
            endpoint_class="openai_embeddings",
        )
        next_token_total = (
            consumed_token_estimate + shape.aggregate_input_token_estimate
        )
        if (
            len(results) >= max_request_count
            or shape.aggregate_input_bytes > max_aggregate_bytes
            or shape.aggregate_input_token_estimate
            > max_aggregate_token_estimate
            or next_token_total > total_token_estimate_budget
        ):
            return LargePageDiagnosticReport(
                status="inconclusive",
                diagnosis="unresolved",
                message="diagnostic request budget exhausted",
                cases=results,
            )
        result = await _run_embedding_case(
            embedding_client=embedding_client,
            case_name=case_name,
            inputs=selected_inputs,
        )
        results.append(result)
        consumed_token_estimate = next_token_total
        if result.status == "failed":
            diagnosis = (
                "established"
                if result.provider_error_category
                in {
                    ExternalErrorCategory.REQUEST_TOO_LARGE.value,
                    ExternalErrorCategory.VALIDATION_FAILED.value,
                }
                else "unresolved"
            )
            return LargePageDiagnosticReport(
                status="failed",
                diagnosis=diagnosis,
                message="embedding diagnostic stopped after first failure",
                cases=results,
            )

    return LargePageDiagnosticReport(
        status="passed",
        diagnosis="unresolved",
        message="bounded cases passed without reproducing the HTTP 400",
        cases=results,
    )


def run_large_page_failure_diagnostic(
    *,
    include_live: bool,
    approved: bool,
    environment: Mapping[str, str],
    bounded_batch_count: int = DEFAULT_BOUNDED_BATCH_COUNT,
    max_aggregate_bytes: int = DEFAULT_MAX_AGGREGATE_BYTES,
    max_aggregate_token_estimate: int = DEFAULT_MAX_AGGREGATE_TOKEN_ESTIMATE,
    max_request_count: int = DEFAULT_MAX_REQUEST_COUNT,
    total_token_estimate_budget: int = DEFAULT_TOTAL_TOKEN_ESTIMATE_BUDGET,
) -> LargePageDiagnosticReport:
    if not include_live:
        return LargePageDiagnosticReport(
            status="skipped",
            diagnosis="unresolved",
            message="live large-page diagnostic is disabled",
        )
    if not approved or environment.get(RUN_FLAG_ENV, "").strip() != "1":
        return LargePageDiagnosticReport(
            status="failed",
            diagnosis="unresolved",
            message="live large-page diagnostic requires explicit approval",
        )
    notion_token = environment.get(NOTION_TOKEN_ENV, "").strip()
    openai_api_key = environment.get(OPENAI_API_KEY_ENV, "").strip()
    target_page_id = environment.get(PAGE_ID_ENV, "").strip()
    if not notion_token or not openai_api_key or not target_page_id:
        return LargePageDiagnosticReport(
            status="failed",
            diagnosis="unresolved",
            message="live large-page diagnostic configuration is incomplete",
        )
    if not 1 <= bounded_batch_count <= MAX_BOUNDED_BATCH_COUNT:
        return LargePageDiagnosticReport(
            status="failed",
            diagnosis="unresolved",
            message="bounded batch count is outside the diagnostic limit",
        )
    if min(
        max_aggregate_bytes,
        max_aggregate_token_estimate,
        max_request_count,
        total_token_estimate_budget,
    ) < 1:
        return LargePageDiagnosticReport(
            status="failed",
            diagnosis="unresolved",
            message="diagnostic request budget is invalid",
        )

    reader_client, embedding_client = _build_live_clients(
        notion_token=notion_token,
        openai_api_key=openai_api_key,
    )
    return asyncio.run(
        run_diagnostic_workflow(
            reader_client=reader_client,
            embedding_client=embedding_client,
            target_page_id=target_page_id,
            bounded_batch_count=bounded_batch_count,
            max_aggregate_bytes=max_aggregate_bytes,
            max_aggregate_token_estimate=max_aggregate_token_estimate,
            max_request_count=max_request_count,
            total_token_estimate_budget=total_token_estimate_budget,
        )
    )


def run_live_shape_inspection(
    *,
    include_live: bool,
    approved: bool,
    environment: Mapping[str, str],
    reader_client_override: Optional[NotionReaderClient] = None,
) -> Dict[str, Any]:
    if not include_live:
        return {
            "status": "skipped",
            "message": "live full request-shape inspection is disabled",
        }
    if not approved or environment.get(RUN_FLAG_ENV, "").strip() != "1":
        return {
            "status": "failed",
            "message": "live full request-shape inspection requires approval",
        }
    notion_token = environment.get(NOTION_TOKEN_ENV, "").strip()
    target_page_id = environment.get(PAGE_ID_ENV, "").strip()
    if not notion_token or not target_page_id:
        return {
            "status": "failed",
            "message": "live full request-shape configuration is incomplete",
        }

    reader_client = reader_client_override or NotionAPIReaderClient(
        token=notion_token,
        timeout_seconds=DIAGNOSTIC_NOTION_TIMEOUT_SECONDS,
    )
    try:
        shape = asyncio.run(
            run_shape_inspection_workflow(
                reader_client=reader_client,
                target_page_id=target_page_id,
            )
        )
    except ShapeInspectionError as exc:
        return {"status": "failed", "message": str(exc)}
    return shape.to_safe_dict()


def _build_live_clients(
    *,
    notion_token: str,
    openai_api_key: str,
) -> Tuple[NotionReaderClient, EmbeddingClient]:
    return (
        NotionAPIReaderClient(
            token=notion_token,
            timeout_seconds=DIAGNOSTIC_NOTION_TIMEOUT_SECONDS,
        ),
        OpenAIEmbeddingClient(api_key=openai_api_key),
    )


def _build_case_counts(
    *,
    available_count: int,
    bounded_batch_count: int,
) -> List[Tuple[str, int]]:
    counts = [
        ("single_input", min(1, available_count)),
        ("small_batch", min(DEFAULT_SMALL_BATCH_COUNT, available_count)),
    ]
    next_count = DEFAULT_SMALL_BATCH_COUNT * 2
    bounded_limit = min(bounded_batch_count, available_count)
    while next_count < bounded_limit:
        counts.append(("bounded_batch", next_count))
        next_count *= 2
    if bounded_limit > DEFAULT_SMALL_BATCH_COUNT:
        counts.append(("bounded_batch", bounded_limit))

    unique_counts = []
    seen = set()
    for case_name, input_count in counts:
        if input_count > 0 and input_count not in seen:
            unique_counts.append((case_name, input_count))
            seen.add(input_count)
    return unique_counts


async def _run_embedding_case(
    *,
    embedding_client: EmbeddingClient,
    case_name: str,
    inputs: List[str],
) -> DiagnosticCaseResult:
    shape = build_embedding_request_diagnostics(
        inputs=inputs,
        provider_name="openai",
        model=DEFAULT_MODEL,
        dimensions=DEFAULT_DIMENSIONS,
        endpoint_class="openai_embeddings",
    )
    started = time.monotonic()
    try:
        await embedding_client.embed(
            EmbeddingRequest(
                inputs=inputs,
                model=DEFAULT_MODEL,
                dimensions=DEFAULT_DIMENSIONS,
            )
        )
    except EmbeddingClientError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return _case_result(
            shape=shape.to_safe_dict(),
            case_name=case_name,
            status="failed",
            duration_ms=duration_ms,
            error=exc,
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    return _case_result(
        shape=shape.to_safe_dict(),
        case_name=case_name,
        status="passed",
        duration_ms=duration_ms,
        error=None,
    )


def _case_result(
    *,
    shape: Dict[str, Any],
    case_name: str,
    status: str,
    duration_ms: int,
    error: Optional[EmbeddingClientError],
) -> DiagnosticCaseResult:
    category = error.category.value if error and error.category else None
    return DiagnosticCaseResult(
        diagnostic_case=case_name,
        status=status,
        endpoint_class=str(shape["endpoint_class"]),
        provider_name=str(shape["provider_name"]),
        model=str(shape["model"]),
        dimensions=int(shape["dimensions"]),
        input_count=int(shape["input_count"]),
        empty_input_count=int(shape["empty_input_count"]),
        max_single_input_chars=int(shape["max_single_input_chars"]),
        max_single_input_bytes=int(shape["max_single_input_bytes"]),
        max_single_input_token_estimate=int(
            shape["max_single_input_token_estimate"]
        ),
        aggregate_input_bytes=int(shape["aggregate_input_bytes"]),
        aggregate_input_token_estimate=int(
            shape["aggregate_input_token_estimate"]
        ),
        input_size_estimator_version=str(shape["input_size_estimator_version"]),
        duration_ms=duration_ms,
        http_status=error.http_status if error else None,
        provider_error_category=category,
        retryable=error.retryable if error else None,
        retry_after_seconds=error.retry_after_seconds if error else None,
    )


def _build_chunk_inputs(structured_content: Dict[str, Any]) -> List[str]:
    return [
        chunk_text
        for chunk_text in _build_all_chunk_inputs(structured_content)
        if chunk_text.strip()
    ]


def _build_all_chunk_inputs(structured_content: Dict[str, Any]) -> List[str]:
    page = structured_content.get("page")
    raw_blocks = structured_content.get("blocks")
    if not isinstance(page, dict) or not isinstance(raw_blocks, list):
        return []
    drafts = chunk_notion_page(
        ChunkerPage(
            notion_page_id=str(page.get("page_id") or "diagnostic-page"),
            title=str(page.get("title") or "Diagnostic Page"),
            notion_path=str(page.get("notion_path") or "Knowledge/Diagnostic"),
            blocks=[
                block
                for raw_block in raw_blocks
                if (block := _to_chunker_block(raw_block)) is not None
            ],
        )
    )
    return [draft.chunk_text for draft in drafts]


def _to_chunker_block(value: Any) -> Optional[ChunkerBlock]:
    if not isinstance(value, dict):
        return None
    block_id = value.get("block_id")
    block_type = value.get("block_type")
    if not isinstance(block_id, str) or not isinstance(block_type, str):
        return None
    raw_children = value.get("children")
    children = raw_children if isinstance(raw_children, list) else []
    return ChunkerBlock(
        notion_block_id=block_id,
        block_type=block_type,
        content_text=str(value.get("content_text") or ""),
        block_path=str(value.get("block_path") or ""),
        children=[
            child
            for raw_child in children
            if (child := _to_chunker_block(raw_child)) is not None
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--shape-only", action="store_true")
    parser.add_argument(
        "--bounded-count",
        type=int,
        default=DEFAULT_BOUNDED_BATCH_COUNT,
    )
    parser.add_argument(
        "--max-aggregate-bytes",
        type=int,
        default=DEFAULT_MAX_AGGREGATE_BYTES,
    )
    parser.add_argument(
        "--max-aggregate-token-estimate",
        type=int,
        default=DEFAULT_MAX_AGGREGATE_TOKEN_ESTIMATE,
    )
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
    if args.shape_only:
        safe_report = run_live_shape_inspection(
            include_live=args.live,
            approved=args.approve,
            environment=os.environ,
        )
        report_status = safe_report.get("status")
        report_message = safe_report.get(
            "message", "full request-shape inspection completed"
        )
    else:
        report = run_large_page_failure_diagnostic(
            include_live=args.live,
            approved=args.approve,
            environment=os.environ,
            bounded_batch_count=args.bounded_count,
            max_aggregate_bytes=args.max_aggregate_bytes,
            max_aggregate_token_estimate=args.max_aggregate_token_estimate,
            max_request_count=args.max_request_count,
            total_token_estimate_budget=args.total_token_estimate_budget,
        )
        safe_report = report.to_safe_dict()
        report_status = report.status
        report_message = report.message
    if args.json:
        print(json.dumps(safe_report, sort_keys=True))
    else:
        print(
            f"status={report_status or 'passed'} message={report_message}"
        )
    return 0 if report_status in {None, "passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
