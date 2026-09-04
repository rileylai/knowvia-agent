from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_preflight_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "preflight.py"
    spec = importlib.util.spec_from_file_location("learnloop_preflight_backend", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _report(module, environ):
    return module.run_preflight(
        profile="api",
        environ=environ,
        module_finder=lambda _: object(),
        command_finder=lambda command: f"/usr/bin/{command}",
        project_root=Path(__file__).resolve().parents[1],
        python_version=(3, 11, 0),
    )


def _check(report, key):
    return next(check for check in report.checks if check.key == key)


def test_preflight_defaults_to_mock_backend() -> None:
    module = _load_preflight_module()
    report = _report(module, {})

    check = _check(report, "config:NOTION_BACKEND")
    assert check.status == "pass"
    assert check.required is True
    assert _check(report, "config:NOTION_TOKEN").status == "pass"


def test_preflight_requires_token_for_live_backend() -> None:
    module = _load_preflight_module()
    report = _report(module, {"NOTION_BACKEND": "live"})

    assert report.failed is True
    assert _check(report, "config:NOTION_BACKEND").status == "fail"
    assert _check(report, "config:NOTION_TOKEN").status == "fail"


def test_preflight_rejects_unknown_backend() -> None:
    module = _load_preflight_module()
    report = _report(module, {"NOTION_BACKEND": "remote"})

    assert report.failed is True
    assert _check(report, "config:NOTION_BACKEND").status == "fail"


def test_preflight_accepts_live_backend_with_token() -> None:
    module = _load_preflight_module()
    report = _report(
        module,
        {"NOTION_BACKEND": "live", "NOTION_TOKEN": "placeholder-token"},
    )

    assert report.failed is False
    assert _check(report, "config:NOTION_BACKEND").status == "pass"
    assert _check(report, "config:NOTION_TOKEN").status == "pass"
