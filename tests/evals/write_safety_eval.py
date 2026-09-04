from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.tools import (  # noqa: E402
    InMemoryNotionPageSnapshot,
    InMemoryNotionWriterClient,
    NotionWriterTool,
    ToolContext,
)


@dataclass(frozen=True)
class WriteSafetyCheckResult:
    check_id: str
    passed: bool
    message: str


@dataclass(frozen=True)
class WriteSafetyEvalResult:
    total_checks: int
    passed_count: int
    passed: bool
    check_results: List[WriteSafetyCheckResult]


async def evaluate_write_safety() -> WriteSafetyEvalResult:
    check_results = [
        await _check_append_preserves_original_blocks(),
        await _check_append_targets_ai_supplement_zone_only(),
        await _check_idempotent_retry_does_not_duplicate_append(),
        await _check_write_policy_violation_performs_no_write(),
    ]
    passed_count = sum(1 for result in check_results if result.passed)
    return WriteSafetyEvalResult(
        total_checks=len(check_results),
        passed_count=passed_count,
        passed=passed_count == len(check_results),
        check_results=check_results,
    )


def format_write_safety_eval_result(result: WriteSafetyEvalResult) -> str:
    status = "pass" if result.passed else "fail"
    lines = [
        f"write_safety: {status} ({result.passed_count}/{result.total_checks})",
        "check_results:",
    ]
    for check_result in result.check_results:
        check_status = "pass" if check_result.passed else "fail"
        lines.append(
            f"- {check_result.check_id}: {check_status}; {check_result.message}"
        )
    return "\n".join(lines)


async def _check_append_preserves_original_blocks() -> WriteSafetyCheckResult:
    tool, client = _build_safe_writer_tool()
    before_blocks = _original_blocks(client, "page-write-safety")
    result = await _run_standard_append(tool, change_request_id=3901)
    after_blocks = _original_blocks(client, "page-write-safety")

    passed = (
        result.is_error is False
        and before_blocks == after_blocks
        and len(before_blocks) == 2
    )
    message = (
        "original blocks unchanged after accepted append"
        if passed
        else "original blocks changed or append failed"
    )
    return WriteSafetyCheckResult(
        check_id="original_blocks_unchanged",
        passed=passed,
        message=message,
    )


async def _check_append_targets_ai_supplement_zone_only() -> WriteSafetyCheckResult:
    tool, client = _build_safe_writer_tool()
    result = await _run_standard_append(tool, change_request_id=3902)
    page = client.get_page_snapshot("page-write-safety")
    operations = client.list_operations(page_id="page-write-safety")

    target_path = ""
    if result.structured_content is not None:
        target_path = str(result.structured_content.get("target_path", ""))

    passed = (
        result.is_error is False
        and "/AI Supplement Zone/" in target_path
        and page is not None
        and len(page.ai_supplement_entries) == 1
        and page.ai_supplement_entries[0].target_path == target_path
        and len(operations) == 1
        and operations[0].operation == "append_ai_supplement_zone"
        and operations[0].target_path == target_path
    )
    message = (
        f"append target stays under AI Supplement Zone: {target_path}"
        if passed
        else "append target or operation escaped AI Supplement Zone"
    )
    return WriteSafetyCheckResult(
        check_id="append_under_ai_supplement_zone_only",
        passed=passed,
        message=message,
    )


async def _check_idempotent_retry_does_not_duplicate_append() -> WriteSafetyCheckResult:
    tool, client = _build_safe_writer_tool()
    arguments = _standard_append_arguments(change_request_id=3903)
    first = await tool.run(
        context=ToolContext(workflow_id="wf-write-safety-retry"),
        arguments=arguments,
    )
    second = await tool.run(
        context=ToolContext(workflow_id="wf-write-safety-retry"),
        arguments=arguments,
    )
    page = client.get_page_snapshot("page-write-safety")
    operations = client.list_operations(page_id="page-write-safety")

    passed = (
        first.is_error is False
        and second.is_error is False
        and second.structured_content is not None
        and second.structured_content.get("idempotent_replay") is True
        and page is not None
        and len(page.ai_supplement_entries) == 1
        and len(operations) == 1
    )
    message = (
        "retry replayed idempotently without duplicate append"
        if passed
        else "retry created duplicate append or failed idempotency"
    )
    return WriteSafetyCheckResult(
        check_id="idempotent_retry_no_duplicate_append",
        passed=passed,
        message=message,
    )


async def _check_write_policy_violation_performs_no_write() -> WriteSafetyCheckResult:
    client = InMemoryNotionWriterClient(
        pages={
            "page-invalid-target": InMemoryNotionPageSnapshot(
                page_id="page-invalid-target",
                title="Invalid Target",
                notion_path="",
                original_blocks=["Original content must remain unchanged."],
            )
        }
    )
    tool = NotionWriterTool(client)
    before_blocks = _original_blocks(client, "page-invalid-target")

    result = await tool.run(
        context=ToolContext(workflow_id="wf-write-safety-violation"),
        arguments=_standard_append_arguments(
            page_id="page-invalid-target",
            change_request_id=3904,
        ),
    )
    after_blocks = _original_blocks(client, "page-invalid-target")
    page = client.get_page_snapshot("page-invalid-target")
    operations = client.list_operations(page_id="page-invalid-target")

    passed = (
        result.is_error is True
        and result.error is not None
        and result.error.code == "WRITE_POLICY_VIOLATION"
        and before_blocks == after_blocks
        and page is not None
        and page.ai_supplement_entries == []
        and operations == []
    )
    message = (
        "write-policy violation failed closed with no append operation"
        if passed
        else "write-policy violation performed a write or returned wrong error"
    )
    return WriteSafetyCheckResult(
        check_id="write_policy_violation_no_write",
        passed=passed,
        message=message,
    )


def _build_safe_writer_tool() -> tuple[NotionWriterTool, InMemoryNotionWriterClient]:
    client = InMemoryNotionWriterClient(
        pages={
            "page-write-safety": InMemoryNotionPageSnapshot(
                page_id="page-write-safety",
                title="NLP Week 5",
                notion_path="Knowledge/NLP/Week5",
                original_blocks=[
                    "Original attention note remains unchanged.",
                    "Manual transformer note remains unchanged.",
                ],
            )
        }
    )
    return NotionWriterTool(client), client


async def _run_standard_append(
    tool: NotionWriterTool,
    *,
    change_request_id: int,
) -> object:
    return await tool.run(
        context=ToolContext(workflow_id=f"wf-write-safety-{change_request_id}"),
        arguments=_standard_append_arguments(change_request_id=change_request_id),
    )


def _standard_append_arguments(
    *,
    page_id: str = "page-write-safety",
    change_request_id: int,
) -> dict[str, object]:
    return {
        "page_id": page_id,
        "change_request_id": change_request_id,
        "topic_title": "Write Safety Supplement",
        "source_display_name": "synthetic-write-safety-source",
        "summary": "Synthetic accepted content for write safety evaluation.",
        "concepts": ["append-only", "AI Supplement Zone"],
        "notes": ["Original blocks must remain unchanged."],
        "append_date": "2026-06-16",
    }


def _original_blocks(
    client: InMemoryNotionWriterClient,
    page_id: str,
) -> List[str]:
    page = client.get_page_snapshot(page_id)
    if page is None:
        return []
    return list(page.original_blocks)


def main() -> None:
    _ = argparse.ArgumentParser(
        description="Evaluate deterministic Notion write-safety invariants."
    ).parse_args()

    result = asyncio.run(evaluate_write_safety())
    print(format_write_safety_eval_result(result))
    if not result.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
