#!/usr/bin/env python3
"""Build a deterministic, read-first Notion/PostgreSQL recovery checklist."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class RecoveryEvidence:
    database_restored: bool = False
    notion_changed: bool = False
    append_outcome_unknown: bool = False
    append_identity_present: Optional[bool] = None
    stale_workflow: bool = False


@dataclass(frozen=True)
class RecoveryPlan:
    actions: Tuple[str, ...]
    forbidden_actions: Tuple[str, ...]
    completion_checks: Tuple[str, ...]


def build_recovery_plan(evidence: RecoveryEvidence) -> RecoveryPlan:
    actions: List[str] = ["PAUSE_MUTATIONS"]

    if evidence.database_restored:
        actions.extend(("VERIFY_MIGRATION_HEAD", "RUN_FULL_NOTION_INDEX"))
    elif evidence.notion_changed:
        actions.append("RUN_INCREMENTAL_NOTION_INDEX")

    if evidence.append_outcome_unknown:
        actions.append("READ_TARGET_PAGE_APPEND_IDENTITY")
        if evidence.append_identity_present is True:
            actions.extend(
                (
                    "KEEP_NOTION_APPEND_AS_AUTHORITATIVE",
                    "RUN_INCREMENTAL_NOTION_INDEX",
                    "RECONCILE_ACCEPT_WORKFLOW_AFTER_INDEX",
                )
            )
        elif evidence.append_identity_present is False:
            actions.extend(
                (
                    "KEEP_CHANGE_REQUEST_UNRESOLVED",
                    "RETRY_ONLY_THROUGH_HUMAN_ACCEPT_FLOW",
                )
            )
        else:
            actions.append("STOP_UNTIL_APPEND_IDENTITY_IS_VERIFIED")

    if evidence.stale_workflow:
        actions.append("CONFIRM_BUSINESS_OUTCOME_BEFORE_WORKFLOW_RECONCILIATION")

    actions.extend(("VERIFY_READINESS", "VERIFY_SCOPED_QA_CITATION"))
    return RecoveryPlan(
        actions=tuple(actions),
        forbidden_actions=(
            "DIRECT_NOTION_EDIT",
            "DIRECT_NOTION_DELETE",
            "MANUAL_NOTION_APPEND",
            "INCLUDE_PENDING_OR_REJECTED_IN_PRODUCTION_RAG",
        ),
        completion_checks=(
            "NOTION_REMAINS_SOURCE_OF_TRUTH",
            "PAGE_LEVEL_REPLACEMENT_COMPLETED",
            "NO_SECRET_OR_PRIVATE_SOURCE_LOGGED",
            "OPERATOR_SIGNOFF_BEFORE_RESUME",
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a safe Notion/PostgreSQL divergence recovery checklist."
    )
    parser.add_argument("--database-restored", action="store_true")
    parser.add_argument("--notion-changed", action="store_true")
    parser.add_argument("--append-outcome-unknown", action="store_true")
    parser.add_argument(
        "--append-identity",
        choices=("present", "absent", "unknown"),
        default=None,
    )
    parser.add_argument("--stale-workflow", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    identity_present = {
        "present": True,
        "absent": False,
        "unknown": None,
    }.get(args.append_identity)
    plan = build_recovery_plan(
        RecoveryEvidence(
            database_restored=args.database_restored,
            notion_changed=args.notion_changed,
            append_outcome_unknown=args.append_outcome_unknown,
            append_identity_present=identity_present,
            stale_workflow=args.stale_workflow,
        )
    )
    payload: Dict[str, Any] = {"status": "plan", **asdict(plan)}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("notion/db recovery plan")
        for action in plan.actions:
            print(f"- {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
