#!/usr/bin/env python3
"""Inspect or explicitly reconcile stale running workflow runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from dataclasses import asdict
from typing import Any, Dict, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.app.config import get_settings
from src.db.session import get_db_session_factory
from src.services import (
    CostBudgetService,
    WorkflowObservabilityService,
    WorkflowRunNotFoundError,
    WorkflowRunValidationError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or reconcile stale LearnLoop workflow runs safely."
    )
    parser.add_argument(
        "--workflow-id",
        type=int,
        default=None,
        help="inspect/reconcile one workflow; otherwise list stale workflows",
    )
    parser.add_argument(
        "--status",
        choices=("succeeded", "failed"),
        default=None,
        help="terminal status to apply when --apply is supplied",
    )
    parser.add_argument(
        "--failure-reason",
        default=None,
        help="standard failure reason required for a failed reconciliation",
    )
    parser.add_argument(
        "--older-than-seconds",
        type=int,
        default=None,
        help="override the configured stale threshold for this inspection",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum stale workflows to inspect (default: 100)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit reconciliation; without this flag the command is dry-run",
    )
    parser.add_argument("--json", action="store_true", help="emit redacted JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    stale_after_seconds = args.older_than_seconds or settings.workflow_stale_after_seconds
    if stale_after_seconds <= 0 or args.limit <= 0:
        return _print_error(
            args,
            code="INVALID_ARGUMENT",
            message="stale threshold and limit must be positive",
        )
    if args.apply and args.status is None:
        return _print_error(
            args,
            code="INVALID_ARGUMENT",
            message="--status is required with --apply",
        )
    if args.apply and args.status == "failed" and not args.failure_reason:
        return _print_error(
            args,
            code="INVALID_ARGUMENT",
            message="--failure-reason is required for failed reconciliation",
        )
    if args.apply and args.status == "succeeded" and args.failure_reason:
        return _print_error(
            args,
            code="INVALID_ARGUMENT",
            message="--failure-reason is not allowed for succeeded reconciliation",
        )

    observability_service = WorkflowObservabilityService(
        get_db_session_factory(),
        cost_budget_service=CostBudgetService(
            daily_budget_usd=settings.max_daily_cost_usd,
            workflow_budget_usd=settings.max_workflow_cost_usd,
        ),
        stale_after_seconds=stale_after_seconds,
    )
    try:
        if args.workflow_id is not None:
            workflow = observability_service.get_workflow(args.workflow_id)
            if workflow is None:
                return _print_error(
                    args,
                    code="WORKFLOW_NOT_FOUND",
                    message="Workflow run is not found",
                )
            workflows = [workflow] if workflow.stale else []
        else:
            workflows = observability_service.list_stale_workflows(limit=args.limit)

        if args.apply:
            reconciled = []
            for workflow in workflows:
                reconciled.append(
                    observability_service.reconcile_workflow(
                        workflow.workflow_run_id,
                        status=args.status,
                        failure_reason=args.failure_reason,
                    )
                )
            workflows = reconciled
    except WorkflowRunNotFoundError:
        return _print_error(
            args,
            code="WORKFLOW_NOT_FOUND",
            message="Workflow run is not found",
        )
    except (ValueError, WorkflowRunValidationError):
        return _print_error(
            args,
            code="WORKFLOW_RECONCILIATION_CONFLICT",
            message="Workflow run cannot be reconciled in its current state",
        )

    payload: Dict[str, Any] = {
        "status": "reconciled" if args.apply else "dry_run",
        "workflow_count": len(workflows),
        "workflows": [asdict(workflow) for workflow in workflows],
    }
    if args.json:
        print(json.dumps(payload, default=str, sort_keys=True))
    else:
        print(
            f"workflow reconciliation {payload['status']}: "
            f"{payload['workflow_count']} workflow(s)"
        )
    return 0


def _print_error(args: argparse.Namespace, *, code: str, message: str) -> int:
    payload = {"status": "failed", "error_code": code, "message": message}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"workflow reconciliation failed: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
