from __future__ import annotations

from .vector_retrieval_eval import (
    evaluate_vector_retrieval_regressions,
    format_vector_retrieval_eval_result,
)


def test_vector_retrieval_eval_reports_full_pass_for_deterministic_fixture() -> None:
    result = evaluate_vector_retrieval_regressions()

    assert result.total_scenarios == 4
    assert result.passed_scenarios == 4
    assert result.passed is True
    assert all(scenario_result.passed for scenario_result in result.scenario_results)

    output = format_vector_retrieval_eval_result(result)
    assert "vector_retrieval_regression: pass (4/4)" in output
    assert "semantic_ranking_and_scope" in output
    assert "vector_query_failure_fallback" in output
    assert "vector_data_unavailable_dedupes_citations" in output
    assert "production_scope_blocks_source_document_only_queries" in output


def test_vector_retrieval_eval_detects_production_scope_regression() -> None:
    result = evaluate_vector_retrieval_regressions(
        enforce_production_scope=False
    )

    assert result.passed is False
    failing_scenarios = [
        scenario_result
        for scenario_result in result.scenario_results
        if not scenario_result.passed
    ]
    assert failing_scenarios
    assert any(
        "Synthetic/External/PDF" in scenario_result.retrieved_paths
        for scenario_result in failing_scenarios
    )
