from __future__ import annotations

import asyncio

from .prompt_injection_eval import (
    evaluate_prompt_injection,
    format_prompt_injection_eval_result,
)


def test_prompt_injection_eval_passes_all_deterministic_checks() -> None:
    result = asyncio.run(evaluate_prompt_injection())

    assert result.passed is True
    assert result.passed_count == result.total_checks == 5
    output = format_prompt_injection_eval_result(result)
    assert "prompt_injection: pass (5/5)" in output
    assert "english_source_injection_is_data" in output
    assert "traditional_chinese_source_injection_is_data" in output
    assert "citation_accuracy_and_production_rag_exclusion" in output
    assert "write_policy_fail_closed" in output
