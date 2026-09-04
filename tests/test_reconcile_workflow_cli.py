from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


def _load_cli_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "reconcile_workflow.py"
    spec = importlib.util.spec_from_file_location("learnloop_reconcile_workflow", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class _FakeWorkflow:
    workflow_run_id: int = 7
    workflow_type: str = "qa"
    status: str = "running"
    failure_reason: Optional[str] = None
    started_at: str = "2026-07-29T00:00:00+00:00"
    finished_at: Optional[str] = None
    age_seconds: float = 7200
    stale: bool = True
    estimated_cost_usd: Optional[float] = None
    metadata: Optional[Dict[str, str]] = None

    def __post_init__(self) -> None:
        self.metadata = self.metadata or {"operation": "qa_answer"}


def test_cli_is_dry_run_by_default(monkeypatch, capsys) -> None:
    module = _load_cli_module()
    fake_workflow = _FakeWorkflow()

    class FakeObservability:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        def list_stale_workflows(self, *, limit):
            assert limit == 100
            return [fake_workflow]

    monkeypatch.setattr(module, "WorkflowObservabilityService", FakeObservability)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "workflow_stale_after_seconds": 3600,
                "max_daily_cost_usd": None,
                "max_workflow_cost_usd": None,
            },
        )(),
    )
    monkeypatch.setattr(module, "get_db_session_factory", lambda: object())

    assert module.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["workflow_count"] == 1


def test_cli_requires_apply_status_and_failure_reason(monkeypatch, capsys) -> None:
    module = _load_cli_module()

    assert module.main(["--apply", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "INVALID_ARGUMENT"

    assert module.main(["--apply", "--status", "failed", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "INVALID_ARGUMENT"
