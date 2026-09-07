from __future__ import annotations

from eval.golden_set import load_golden_set
from eval.runner import run_golden_set


def test_7_0_golden_set_has_all_core_contract_scenarios() -> None:
    golden_set = load_golden_set()

    assert golden_set.version == 1
    assert len(golden_set.scenarios) == 18
    assert {scenario.category for scenario in golden_set.scenarios} == {
        "knowledge",
        "grounding",
        "citation",
        "conversation",
        "session_isolation",
        "memory",
        "memory_authority",
        "agent",
        "tool_safety",
        "mcp",
        "sse",
    }
    for scenario in golden_set.scenarios:
        assert scenario.setup
        assert scenario.user_input
        assert scenario.expected_behavior
        assert scenario.forbidden_behavior
        assert scenario.verification


def test_7_0_deterministic_runner_passes_every_core_scenario() -> None:
    report = run_golden_set()

    assert report["total"] == 18
    assert report["passed"] == 18
    assert report["failed"] == 0
    assert report["pass_rate"] == 1.0
    assert all(result["passed"] for result in report["results"])
    assert all(result["failure_reason"] is None for result in report["results"])

