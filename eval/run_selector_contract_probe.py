from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError

from eval.user_facing_diagnostic import (
    DiagnosticReportError,
    DiagnosticSetError,
    RecordingProvider,
    build_safe_contract_fingerprint,
    load_diagnostic_set,
    write_json_report,
)
from src.agent import BoundedAgentRuntime
from src.agent.models import ContextRequirementDecision
from src.orchestrators.qa_orchestrator import DEFAULT_QA_MODEL, DEFAULT_QA_PROVIDER_NAME
from src.providers import LLMRequest, OpenAIClient, ProviderRouter


RUN_FLAG_ENV = "KNOWVIA_RUN_SELECTOR_CONTRACT_PROBE"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_PRIMARY_REPORT = Path(
    "eval/retrieval/reports/8.5-user-facing-answer-quality-20260909.json"
)
DEFAULT_DIAGNOSTIC_SET = Path("eval/user_facing_diagnostic.yaml")
DEFAULT_REPORT = Path(
    "eval/retrieval/reports/8.6-selector-contract-probe-20260909.json"
)
FAILURE_CASE_REPEATS = 3
CONTROL_REPEATS = 1


def _safe_event(event: Mapping[str, object]) -> dict[str, object]:
    return {
        "operation": event.get("operation"),
        "status": event.get("status"),
        "error_type": event.get("error_type"),
        "provider_error_kind": event.get("provider_error_kind"),
        "structured_output_contract": event.get("structured_output_contract"),
        "failure_fingerprint": event.get("failure_fingerprint"),
        "shape_metadata": event.get("shape_metadata"),
    }


async def _probe_one(
    *,
    query_id: str,
    query: str,
    api_key: str,
    probe_index: int,
) -> dict[str, object]:
    provider = RecordingProvider(OpenAIClient(api_key=api_key))
    router = ProviderRouter()
    router.register_provider(provider)
    runtime = BoundedAgentRuntime(
        provider_router=router,
        tool_registry=object(),
        max_tool_calls=3,
        max_iterations=6,
        tool_timeout_seconds=8.0,
        context_char_budget=16000,
        workflow_run_service=None,
    )
    request = LLMRequest(
        model=DEFAULT_QA_MODEL,
        messages=runtime._context_selector_messages(
            query=query,
            reference_bindings=[],
        ),
        temperature=0.0,
        max_tokens=200,
        response_format=ContextRequirementDecision.response_format(),
        metadata={
            "workflow_id": f"8.6-selector-{query_id}-{probe_index}",
            "operation": "context_requirement_selection",
            "session_id": 0,
            "owner_id": "local",
        },
    )
    termination = "completed"
    try:
        response = await router.route(DEFAULT_QA_PROVIDER_NAME, request)
        try:
            ContextRequirementDecision.model_validate(response.structured_output)
        except ValidationError:
            termination = "provider_contract_error"
    except Exception:
        termination = "provider_error"

    event = provider.events[-1] if provider.events else {
        "operation": "context_requirement_selection",
        "status": "unknown",
    }
    safe_event = _safe_event(event)
    if safe_event.get("status") == "contract_error":
        termination = "provider_contract_error"
    elif safe_event.get("status") == "provider_error":
        termination = "provider_error"
    return {
        "query_id": query_id,
        "probe_index": probe_index,
        "operation": "context_requirement_selection",
        "termination_reason": termination,
        "outcome": (
            "contract_failure"
            if safe_event.get("status") == "contract_error"
            else "provider_failure"
            if safe_event.get("status") == "provider_error"
            else "completed"
        ),
        "provider_operation": safe_event,
    }


async def run_probe(
    primary_report: Mapping[str, object],
    *,
    api_key: str,
) -> dict[str, object]:
    traces = primary_report.get("per_query_traces")
    if not isinstance(traces, list):
        raise DiagnosticReportError("primary report has no per-query traces")
    by_id = {
        str(trace.get("query_id")): trace
        for trace in traces
        if isinstance(trace, Mapping)
    }
    failures = [
        query_id
        for query_id, trace in by_id.items()
        if trace.get("classification") == "provider_failure"
    ]
    if set(failures) != {"UQ-003", "UQ-007", "UQ-010"}:
        raise DiagnosticReportError("primary provider-failure set changed")
    controls = [
        query_id
        for query_id in ("UQ-004", "UQ-005", "UQ-008")
        if by_id.get(query_id, {}).get("classification") == "completed"
        and by_id.get(query_id, {}).get("expected_outcome") == "SHOULD_ANSWER"
    ]
    if len(controls) != 3:
        raise DiagnosticReportError("expected three successful SHOULD_ANSWER controls")

    results: list[dict[str, object]] = []
    for query_id in failures + controls:
        query = str(by_id[query_id]["user_query"])
        repeats = FAILURE_CASE_REPEATS if query_id in failures else CONTROL_REPEATS
        outcomes = [
            await _probe_one(
                query_id=query_id,
                query=query,
                api_key=api_key,
                probe_index=index,
            )
            for index in range(1, repeats + 1)
        ]
        results.append(
            {
                "query_id": query_id,
                "primary_classification": by_id[query_id].get("classification"),
                "probe_repeat_count": repeats,
                "outcomes": outcomes,
            }
        )

    return {
        "artifact_type": "selector_contract_stability_probe",
        "run_metadata": {
            "diagnostic_id": "knowvia-user-facing-answer-quality-8.6",
            "probe_run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "provider": DEFAULT_QA_PROVIDER_NAME,
            "model": DEFAULT_QA_MODEL,
            "failure_cases": failures,
            "successful_controls": controls,
            "failure_case_repeats": FAILURE_CASE_REPEATS,
            "control_repeats": CONTROL_REPEATS,
            "retrieval_called": False,
            "memory_called": False,
            "readiness_called": False,
            "final_synthesis_called": False,
            "production_behavior_changed": False,
            "raw_provider_response_recorded": False,
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run selector-only contract probes for 8.6."
    )
    parser.add_argument("--diagnostic-set", type=Path, default=DEFAULT_DIAGNOSTIC_SET)
    parser.add_argument("--primary-report", type=Path, default=DEFAULT_PRIMARY_REPORT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if os.getenv(RUN_FLAG_ENV) != "1":
        parser.error(f"Set {RUN_FLAG_ENV}=1 to confirm bounded selector probes.")
    api_key = os.getenv(OPENAI_API_KEY_ENV, "").strip()
    if not api_key:
        parser.error(f"{OPENAI_API_KEY_ENV} is required for selector probes.")
    try:
        diagnostic_set = load_diagnostic_set(args.diagnostic_set)
        primary_report = json.loads(args.primary_report.read_text(encoding="utf-8"))
        fixture_cases = {
            str(case["query_id"]): str(case["user_query"])
            for case in diagnostic_set["cases"]
            if isinstance(case, Mapping)
        }
        for trace in primary_report.get("per_query_traces", []):
            if not isinstance(trace, Mapping):
                continue
            query_id = str(trace.get("query_id"))
            if query_id in fixture_cases and trace.get("user_query") != fixture_cases[query_id]:
                raise DiagnosticSetError(f"frozen query payload mismatch: {query_id}")
        report = asyncio.run(run_probe(primary_report, api_key=api_key))
        write_json_report(report, args.report)
    except (OSError, json.JSONDecodeError, DiagnosticReportError, DiagnosticSetError) as exc:
        parser.error(str(exc))
    print(f"report={args.report}")
    for result in report["results"]:
        counts: dict[str, int] = {}
        for outcome in result["outcomes"]:
            key = str(outcome["outcome"])
            counts[key] = counts.get(key, 0) + 1
        print(f"{result['query_id']} outcomes={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
