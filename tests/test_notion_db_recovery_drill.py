from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "notion_db_recovery_drill.py"
    spec = importlib.util.spec_from_file_location("learnloop_notion_db_recovery_drill", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_restore_and_manual_change_recovery_rebuilds_from_notion(capsys) -> None:
    module = _load_module()

    assert module.main(["--database-restored", "--notion-changed", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["actions"][:3] == [
        "PAUSE_MUTATIONS",
        "VERIFY_MIGRATION_HEAD",
        "RUN_FULL_NOTION_INDEX",
    ]
    assert "DIRECT_NOTION_EDIT" in payload["forbidden_actions"]
    assert "NOTION_REMAINS_SOURCE_OF_TRUTH" in payload["completion_checks"]


def test_unknown_append_identity_stops_before_any_retry() -> None:
    module = _load_module()

    plan = module.build_recovery_plan(
        module.RecoveryEvidence(
            append_outcome_unknown=True,
            append_identity_present=None,
        )
    )

    assert "READ_TARGET_PAGE_APPEND_IDENTITY" in plan.actions
    assert "STOP_UNTIL_APPEND_IDENTITY_IS_VERIFIED" in plan.actions
    assert "RETRY_ONLY_THROUGH_HUMAN_ACCEPT_FLOW" not in plan.actions


def test_present_append_identity_reindexes_and_reconciles_workflow() -> None:
    module = _load_module()

    plan = module.build_recovery_plan(
        module.RecoveryEvidence(
            append_outcome_unknown=True,
            append_identity_present=True,
            stale_workflow=True,
        )
    )

    assert "KEEP_NOTION_APPEND_AS_AUTHORITATIVE" in plan.actions
    assert "RUN_INCREMENTAL_NOTION_INDEX" in plan.actions
    assert "RECONCILE_ACCEPT_WORKFLOW_AFTER_INDEX" in plan.actions
    assert "CONFIRM_BUSINESS_OUTCOME_BEFORE_WORKFLOW_RECONCILIATION" in plan.actions


def test_absent_append_identity_keeps_change_request_unresolved() -> None:
    module = _load_module()

    plan = module.build_recovery_plan(
        module.RecoveryEvidence(
            append_outcome_unknown=True,
            append_identity_present=False,
        )
    )

    assert "KEEP_CHANGE_REQUEST_UNRESOLVED" in plan.actions
    assert "RETRY_ONLY_THROUGH_HUMAN_ACCEPT_FLOW" in plan.actions
