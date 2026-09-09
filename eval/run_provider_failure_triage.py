from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from eval.user_facing_diagnostic import (
    DiagnosticReportError,
    DiagnosticSetError,
    load_diagnostic_set,
    run_live_diagnostic,
    write_json_report,
)


RUN_FLAG_ENV = "KNOWVIA_RUN_PROVIDER_FAILURE_TRIAGE"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
DATABASE_URL_ENV = "DATABASE_URL"
PRIMARY_REPORT = Path(
    "eval/retrieval/reports/8.5-user-facing-answer-quality-20260909.json"
)
DEFAULT_REPORT = Path(
    "eval/retrieval/reports/8.5-provider-failure-triage-20260909.json"
)
MAX_REPEATS = 2


def _failure_boundary(operation: object) -> str | None:
    return {
        "reference_binding_resolution": "reference_binding",
        "context_requirement_selection": "context_requirement_selection",
        "evidence_readiness": "evidence_readiness",
        "bounded_agent_final": "final_synthesis",
    }.get(str(operation))


def _operation_summary(trace: Mapping[str, object]) -> dict[str, object]:
    operations = trace.get("provider_operations")
    if not isinstance(operations, list):
        operations = []
    safe_operations = [
        {
            "operation": event.get("operation"),
            "status": event.get("status"),
            "error_type": event.get("error_type"),
            "provider_error_kind": event.get("provider_error_kind"),
            "structured_output_contract": event.get("structured_output_contract"),
        }
        for event in operations
        if isinstance(event, Mapping)
    ]
    failing = next(
        (
            event
            for event in safe_operations
            if event.get("status") in {"provider_error", "contract_error"}
        ),
        None,
    )
    successful = [
        event
        for event in safe_operations
        if event.get("status") == "completed"
        and event.get("structured_output_contract") != "invalid"
    ]
    if failing is None:
        outcome = {
            "failing_operation": None,
            "failure_boundary": None,
            "provider_error_kind": "unresolved_provider_failure",
            "operation_statuses": [
                {"operation": event.get("operation"), "status": event.get("status")}
                for event in safe_operations
            ],
        }
    else:
        operation = failing.get("operation")
        outcome = {
            "failing_operation": operation,
            "failure_boundary": _failure_boundary(operation),
            "provider_error_kind": failing.get(
                "provider_error_kind", "unresolved_provider_failure"
            ),
            "operation_statuses": [
                {"operation": event.get("operation"), "status": event.get("status")}
                for event in safe_operations
            ],
        }
    outcome["last_successful_operation"] = (
        successful[-1].get("operation") if successful else None
    )
    outcome["termination_reason"] = trace.get("final", {}).get("termination_reason")
    return outcome


def _failure_category(outcome: Mapping[str, object]) -> str:
    kind = str(outcome.get("provider_error_kind") or "")
    boundary = str(outcome.get("failure_boundary") or "")
    if kind in {
        "transient_transport_failure",
        "rate_limit_failure",
        "provider_timeout",
        "structured_output_parse_failure",
    }:
        return kind
    if kind == "provider_contract_failure" and boundary:
        return {
            "reference_binding": "reference_binding_provider_failure",
            "context_requirement_selection": "selector_provider_failure",
            "evidence_readiness": "readiness_provider_failure",
            "final_synthesis": "final_synthesis_provider_failure",
        }.get(boundary, "provider_contract_failure")
    if boundary:
        return {
            "reference_binding": "reference_binding_provider_failure",
            "context_requirement_selection": "selector_provider_failure",
            "evidence_readiness": "readiness_provider_failure",
            "final_synthesis": "final_synthesis_provider_failure",
        }.get(boundary, "unresolved_provider_failure")
    return "unresolved_provider_failure"


def classify_repeats(outcomes: Sequence[Mapping[str, object]]) -> tuple[str, str]:
    signatures = {
        (
            outcome.get("failing_operation"),
            outcome.get("failure_boundary"),
            outcome.get("provider_error_kind"),
            outcome.get("termination_reason"),
        )
        for outcome in outcomes
    }
    if len(signatures) != 1:
        return "intermittent_provider_failure", "intermittent"
    return _failure_category(outcomes[0]), "deterministic"


async def run_triage(
    diagnostic_set: Mapping[str, object],
    primary_report: Mapping[str, object],
    *,
    database_url: str,
    openai_api_key: str,
) -> dict[str, object]:
    primary_traces = primary_report.get("per_query_traces")
    if not isinstance(primary_traces, list):
        raise DiagnosticReportError("primary report has no per-query traces")
    failed_ids = [
        str(trace.get("query_id"))
        for trace in primary_traces
        if isinstance(trace, Mapping) and trace.get("classification") == "provider_failure"
    ]
    if len(failed_ids) != 3:
        raise DiagnosticReportError("primary report must contain exactly three provider failures")

    cases = diagnostic_set.get("cases")
    if not isinstance(cases, list):
        raise DiagnosticSetError("diagnostic set has no cases")
    case_by_id = {
        str(case.get("query_id")): case
        for case in cases
        if isinstance(case, Mapping)
    }
    if set(failed_ids) - set(case_by_id):
        raise DiagnosticSetError("primary provider failures are missing from diagnostic set")

    case_results: list[dict[str, object]] = []
    for query_id in failed_ids:
        case = case_by_id[query_id]
        outcomes: list[dict[str, object]] = []
        for repeat_index in range(1, MAX_REPEATS + 1):
            filtered_set = {**dict(diagnostic_set), "cases": [case]}
            report = await run_live_diagnostic(
                filtered_set,
                database_url=database_url,
                openai_api_key=openai_api_key,
                include_provider_operations=True,
            )
            traces = report.get("per_query_traces")
            if not isinstance(traces, list) or len(traces) != 1:
                raise DiagnosticReportError(f"repeat trace missing for {query_id}")
            outcome = _operation_summary(traces[0])
            outcome["repeat_index"] = repeat_index
            outcomes.append(outcome)
        failure_category, stability = classify_repeats(outcomes)
        case_results.append(
            {
                "query_id": query_id,
                "user_query": case.get("user_query"),
                "original_failure_operation": "not_recorded_in_primary_artifact",
                "primary_observation": {
                    "termination_reason": "provider_error",
                    "selector_status": "completed",
                    "retrieval_observed": False,
                    "readiness_called": False,
                    "final_synthesis_called": False,
                },
                "repeat_count": len(outcomes),
                "repeat_outcomes": outcomes,
                "failure_category": failure_category,
                "deterministic_or_intermittent": stability,
            }
        )

    report = {
        "artifact_type": "user_facing_provider_failure_triage",
        "run_metadata": {
            "diagnostic_id": diagnostic_set["diagnostic_id"],
            "primary_report": str(PRIMARY_REPORT),
            "triage_run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "provider": "openai",
            "primary_failures_only": True,
            "repeats_per_case": MAX_REPEATS,
            "production_behavior_changed": False,
            "raw_provider_response_recorded": False,
        },
        "corpus_snapshot": primary_report.get("corpus_snapshot", {}),
        "failed_cases": case_results,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run bounded repeats for the three 8.5 provider failures only."
    )
    parser.add_argument(
        "--diagnostic-set", type=Path, default=Path("eval/user_facing_diagnostic.yaml")
    )
    parser.add_argument("--primary-report", type=Path, default=PRIMARY_REPORT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if os.getenv(RUN_FLAG_ENV) != "1":
        parser.error(f"Set {RUN_FLAG_ENV}=1 to confirm bounded repeat execution.")
    api_key = os.getenv(OPENAI_API_KEY_ENV, "").strip()
    database_url = os.getenv(DATABASE_URL_ENV, "").strip()
    if not api_key:
        parser.error(f"{OPENAI_API_KEY_ENV} is required for triage.")
    if not database_url:
        parser.error(f"{DATABASE_URL_ENV} is required for local triage.")
    try:
        diagnostic_set = load_diagnostic_set(args.diagnostic_set)
        primary_report = json.loads(args.primary_report.read_text(encoding="utf-8"))
        report = asyncio.run(
            run_triage(
                diagnostic_set,
                primary_report,
                database_url=database_url,
                openai_api_key=api_key,
            )
        )
        write_json_report(report, args.report)
    except (OSError, json.JSONDecodeError, DiagnosticReportError, DiagnosticSetError) as exc:
        parser.error(str(exc))
    print(f"report={args.report}")
    for case in report["failed_cases"]:
        print(
            f"{case['query_id']} category={case['failure_category']} "
            f"stability={case['deterministic_or_intermittent']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
