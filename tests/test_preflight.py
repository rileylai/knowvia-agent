from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Optional

import pytest


def _load_preflight_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "preflight.py"
    spec = importlib.util.spec_from_file_location("learnloop_preflight", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _all_modules_present(module_name: str) -> object:
    _ = module_name
    return object()


def _all_commands_present(command: str) -> Optional[str]:
    return f"/usr/bin/{command}"


def _completed_tesseract_languages(
    languages: tuple[str, ...],
    *,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    stdout = "List of available languages (3):\n" + "\n".join(languages)
    return subprocess.CompletedProcess(
        args=["tesseract", "--list-langs"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _run_report(
    module,
    *,
    profile: str = "api",
    environ: Optional[Mapping[str, str]] = None,
):
    return module.run_preflight(
        profile=profile,
        environ=environ or {},
        module_finder=_all_modules_present,
        command_finder=_all_commands_present,
        required_commands=("uv",),
        project_root=Path(__file__).resolve().parents[1],
        python_version=(3, 11, 0),
    )


def test_preflight_reports_missing_dependency_matrix() -> None:
    module = _load_preflight_module()

    report = module.run_preflight(
        profile="api",
        environ={},
        module_finder=lambda module_name: None,
        command_finder=lambda command: None,
        required_commands=("uv",),
        project_root=Path(__file__).resolve().parents[1],
        python_version=(3, 11, 0),
    )

    failed_keys = {check.key for check in report.checks if check.status == "fail"}
    assert report.failed is True
    assert "dependency:fastapi" in failed_keys
    assert "dependency:sqlalchemy" in failed_keys
    assert "command:uv" in failed_keys
    assert "config:OPENAI_API_KEY" not in failed_keys


def test_preflight_ocr_profile_requires_tesseract() -> None:
    module = _load_preflight_module()

    report = module.run_preflight(
        profile="ocr",
        environ={},
        module_finder=_all_modules_present,
        command_finder=lambda command: None if command == "tesseract" else "/usr/bin/tool",
        project_root=Path(__file__).resolve().parents[1],
        python_version=(3, 11, 0),
    )

    tesseract_check = next(
        check for check in report.checks if check.key == "command:tesseract"
    )
    assert tesseract_check.status == "fail"
    assert tesseract_check.required is True


def test_preflight_ocr_profile_requires_all_tesseract_languages() -> None:
    module = _load_preflight_module()
    calls = []

    def command_runner(command, timeout_seconds):
        calls.append((tuple(command), timeout_seconds))
        return _completed_tesseract_languages(("eng", "chi_tra", "chi_sim"))

    report = module.run_preflight(
        profile="ocr",
        environ={},
        module_finder=_all_modules_present,
        command_finder=_all_commands_present,
        command_runner=command_runner,
        project_root=Path(__file__).resolve().parents[1],
        python_version=(3, 11, 0),
    )

    language_checks = {
        check.key: check for check in report.checks if check.key.startswith("tesseract-language:")
    }
    assert report.failed is False
    assert set(language_checks) == {
        "tesseract-language:eng",
        "tesseract-language:chi_tra",
        "tesseract-language:chi_sim",
    }
    assert all(check.status == "pass" for check in language_checks.values())
    assert calls == [(('tesseract', '--list-langs'), 5.0)]


@pytest.mark.parametrize("missing_language", ("eng", "chi_tra", "chi_sim"))
def test_preflight_ocr_profile_fails_when_language_is_missing(
    missing_language: str,
) -> None:
    module = _load_preflight_module()
    available = tuple(
        language
        for language in ("eng", "chi_tra", "chi_sim")
        if language != missing_language
    )

    report = module.run_preflight(
        profile="ocr",
        environ={},
        module_finder=_all_modules_present,
        command_finder=_all_commands_present,
        command_runner=lambda command, timeout: _completed_tesseract_languages(
            available
        ),
        project_root=Path(__file__).resolve().parents[1],
        python_version=(3, 11, 0),
    )

    check = next(
        item
        for item in report.checks
        if item.key == f"tesseract-language:{missing_language}"
    )
    assert report.failed is True
    assert check.status == "fail"
    assert check.detail == "missing"


@pytest.mark.parametrize("failure_mode", ("timeout", "nonzero", "malformed"))
def test_preflight_ocr_language_check_fails_safely(failure_mode: str) -> None:
    module = _load_preflight_module()
    private_error = "private-ocr-output-must-not-appear"

    def command_runner(command, timeout_seconds):
        _ = command
        _ = timeout_seconds
        if failure_mode == "timeout":
            raise subprocess.TimeoutExpired(["tesseract", "--list-langs"], 5.0)
        if failure_mode == "nonzero":
            return _completed_tesseract_languages(
                (),
                returncode=1,
                stderr=private_error,
            )
        return subprocess.CompletedProcess(
            args=["tesseract", "--list-langs"],
            returncode=0,
            stdout=private_error,
            stderr=private_error,
        )

    report = module.run_preflight(
        profile="ocr",
        environ={},
        module_finder=_all_modules_present,
        command_finder=_all_commands_present,
        command_runner=command_runner,
        project_root=Path(__file__).resolve().parents[1],
        python_version=(3, 11, 0),
    )
    rendered = module._render_human(report)
    rendered_json = module._report_as_json(report)

    assert report.failed is True
    assert private_error not in rendered
    assert private_error not in rendered_json
    assert all(
        check.status == "fail"
        for check in report.checks
        if check.key.startswith("tesseract-language:")
    )


def test_preflight_script_remains_stdlib_only_for_tesseract_languages() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "preflight.py"
    source = script_path.read_text(encoding="utf-8")

    assert "import pytesseract" not in source
    assert "from pytesseract" not in source
    assert "subprocess.run" in source


def test_preflight_never_prints_secret_values(capsys) -> None:
    module = _load_preflight_module()
    secret = "sk-live-secret-that-must-not-appear"
    report = _run_report(module, environ={"OPENAI_API_KEY": secret})

    output = module._render_human(report)
    json_output = module._report_as_json(report)
    print(output)
    print(json_output)
    captured = capsys.readouterr()

    assert secret not in captured.out
    assert "config:OPENAI_API_KEY" in captured.out
    assert '"detail": "configured"' in captured.out


def test_run_live_entrypoint_is_repo_relative() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "run_live.sh"
    ).read_text()

    assert "/Users/" not in script
    assert "REPO_ROOT" in script
    assert "scripts/preflight.py" in script
    assert '"$UV_BIN" run --no-env-file --frozen uvicorn' in script
