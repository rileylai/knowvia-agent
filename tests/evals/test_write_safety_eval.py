from __future__ import annotations

import asyncio

from .write_safety_eval import (
    WriteSafetyCheckResult,
    WriteSafetyEvalResult,
    evaluate_write_safety,
    format_write_safety_eval_result,
)


def test_write_safety_eval_passes_all_deterministic_checks() -> None:
    result = asyncio.run(evaluate_write_safety())

    assert result.passed is True
    assert result.total_checks == 4
    assert result.passed_count == 4
    assert {check.check_id for check in result.check_results} == {
        "original_blocks_unchanged",
        "append_under_ai_supplement_zone_only",
        "idempotent_retry_no_duplicate_append",
        "write_policy_violation_no_write",
    }


def test_write_safety_eval_output_includes_summary_and_each_check() -> None:
    result = WriteSafetyEvalResult(
        total_checks=2,
        passed_count=1,
        passed=False,
        check_results=[
            WriteSafetyCheckResult(
                check_id="original_blocks_unchanged",
                passed=True,
                message="original blocks unchanged after accepted append",
            ),
            WriteSafetyCheckResult(
                check_id="write_policy_violation_no_write",
                passed=False,
                message="write-policy violation performed a write",
            ),
        ],
    )

    output = format_write_safety_eval_result(result)

    assert "write_safety: fail (1/2)" in output
    assert "- original_blocks_unchanged: pass;" in output
    assert "- write_policy_violation_no_write: fail;" in output
