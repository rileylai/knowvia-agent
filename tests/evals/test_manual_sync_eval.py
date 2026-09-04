from __future__ import annotations

import asyncio

from .manual_sync_eval import (
    ManualSyncCheckResult,
    ManualSyncEvalResult,
    evaluate_manual_sync_reconciliation,
    format_manual_sync_eval_result,
)


def test_manual_sync_eval_removes_deleted_ai_chunk_after_sync() -> None:
    result = asyncio.run(evaluate_manual_sync_reconciliation())

    assert result.passed is True
    assert result.total_checks == 4
    assert result.passed_count == 4
    assert {check.check_id for check in result.check_results} == {
        "initial_ai_chunk_indexed",
        "deleted_ai_chunk_removed",
        "manual_note_chunk_retained",
        "manual_sync_metadata",
    }


def test_manual_sync_eval_output_includes_summary_and_each_check() -> None:
    result = ManualSyncEvalResult(
        total_checks=2,
        passed_count=1,
        passed=False,
        check_results=[
            ManualSyncCheckResult(
                check_id="deleted_ai_chunk_removed",
                passed=True,
                message="deleted AI supplement chunk absent",
            ),
            ManualSyncCheckResult(
                check_id="manual_note_chunk_retained",
                passed=False,
                message="manual note chunk missing",
            ),
        ],
    )

    output = format_manual_sync_eval_result(result)

    assert "manual_sync_reconciliation: fail (1/2)" in output
    assert "- deleted_ai_chunk_removed: pass;" in output
    assert "- manual_note_chunk_retained: fail;" in output
