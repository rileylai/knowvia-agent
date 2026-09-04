#!/usr/bin/env python3
"""Check local LearnLoop dependencies and configuration without exposing secrets."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable, Iterable, Mapping, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESSERACT_REQUIRED_LANGUAGES: Tuple[str, ...] = (
    "eng",
    "chi_tra",
    "chi_sim",
)
TESSERACT_LANGUAGE_CHECK_TIMEOUT_SECONDS = 5.0

RUNTIME_DEPENDENCIES: Tuple[Tuple[str, str], ...] = (
    ("alembic", "alembic"),
    ("fastapi", "fastapi"),
    ("pillow", "PIL"),
    ("pypdf", "pypdf"),
    ("pytesseract", "pytesseract"),
    ("python-multipart", "multipart"),
    ("psycopg", "psycopg"),
    ("rq", "rq"),
    ("sqlalchemy", "sqlalchemy"),
    ("trafilatura", "trafilatura"),
    ("youtube-transcript-api", "youtube_transcript_api"),
    ("uvicorn", "uvicorn"),
)

DEV_DEPENDENCIES: Tuple[Tuple[str, str], ...] = (
    ("fakeredis", "fakeredis"),
    ("httpx", "httpx"),
    ("pytest", "pytest"),
    ("pyyaml", "yaml"),
)

PROFILE_DEPENDENCIES: Mapping[str, Tuple[Tuple[str, str], ...]] = {
    "api": RUNTIME_DEPENDENCIES,
    "ocr": RUNTIME_DEPENDENCIES,
    "test": RUNTIME_DEPENDENCIES + DEV_DEPENDENCIES,
}

CONFIGURATION_KEYS: Tuple[str, ...] = (
    "APP_ENV",
    "LOG_LEVEL",
    "DATABASE_URL",
    "REDIS_URL",
    "MOCK_NOTION_DATA_DIR",
    "NOTION_BACKEND",
    "OPENAI_API_KEY",
    "NOTION_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "API_BEARER_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "MAX_WORKFLOW_COST_USD",
    "MAX_DAILY_COST_USD",
    "WORKFLOW_STALE_AFTER_SECONDS",
)


@dataclass(frozen=True)
class CheckResult:
    key: str
    status: str
    detail: str
    required: bool


@dataclass(frozen=True)
class PreflightReport:
    profile: str
    checks: Tuple[CheckResult, ...]

    @property
    def failed(self) -> bool:
        return any(check.required and check.status == "fail" for check in self.checks)


ModuleFinder = Callable[[str], object]
CommandFinder = Callable[[str], Optional[str]]
CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _default_module_finder(module_name: str) -> object:
    return importlib.util.find_spec(module_name)


def _default_command_finder(command: str) -> Optional[str]:
    return shutil.which(command)


def _default_command_runner(
    command: Sequence[str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        shell=False,
    )


def _check_project_files(project_root: Path) -> Iterable[CheckResult]:
    for file_name in ("pyproject.toml", "uv.lock", ".env.example", "alembic.ini"):
        path = project_root / file_name
        yield CheckResult(
            key=f"file:{file_name}",
            status="pass" if path.is_file() else "fail",
            detail="present" if path.is_file() else "missing",
            required=True,
        )


def _check_python(python_version: Optional[Tuple[int, ...]] = None) -> CheckResult:
    version = python_version or sys.version_info[:3]
    version_text = ".".join(str(part) for part in version[:3])
    supported = version[:2] >= (3, 9)
    return CheckResult(
        key="runtime:python",
        status="pass" if supported else "fail",
        detail=f"Python {version_text}" if supported else "Python 3.9+ required",
        required=True,
    )


def _check_dependencies(
    profile: str,
    module_finder: ModuleFinder,
) -> Iterable[CheckResult]:
    for distribution_name, module_name in PROFILE_DEPENDENCIES[profile]:
        try:
            installed = module_finder(module_name) is not None
        except Exception:
            installed = False
        yield CheckResult(
            key=f"dependency:{distribution_name}",
            status="pass" if installed else "fail",
            detail="installed" if installed else "missing",
            required=True,
        )


def _check_commands(
    commands: Iterable[str],
    command_finder: CommandFinder,
) -> Iterable[CheckResult]:
    for command in commands:
        available = command_finder(command) is not None
        yield CheckResult(
            key=f"command:{command}",
            status="pass" if available else "fail",
            detail="available" if available else "missing",
            required=True,
        )


def _check_tesseract_languages(
    command_runner: CommandRunner,
) -> Iterable[CheckResult]:
    try:
        completed = command_runner(
            ("tesseract", "--list-langs"),
            TESSERACT_LANGUAGE_CHECK_TIMEOUT_SECONDS,
        )
    except Exception:
        for language in TESSERACT_REQUIRED_LANGUAGES:
            yield CheckResult(
                key=f"tesseract-language:{language}",
                status="fail",
                detail="error",
                required=True,
            )
        return

    if completed.returncode != 0 or not isinstance(completed.stdout, str):
        for language in TESSERACT_REQUIRED_LANGUAGES:
            yield CheckResult(
                key=f"tesseract-language:{language}",
                status="fail",
                detail="error",
                required=True,
            )
        return

    available_languages = {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip() in TESSERACT_REQUIRED_LANGUAGES
    }
    for language in TESSERACT_REQUIRED_LANGUAGES:
        available = language in available_languages
        yield CheckResult(
            key=f"tesseract-language:{language}",
            status="pass" if available else "fail",
            detail="available" if available else "missing",
            required=True,
        )


def _check_configuration(
    environ: Mapping[str, str],
    project_root: Path,
) -> Iterable[CheckResult]:
    for key in CONFIGURATION_KEYS:
        configured = bool(environ.get(key, "").strip())
        if key in {"APP_ENV", "LOG_LEVEL"}:
            status = "pass"
            detail = "configured" if configured else "using application default"
            required = False
        elif key == "DATABASE_URL":
            status = "pass" if configured else "warn"
            detail = "configured" if configured else "using local default database URL"
            required = False
        elif key == "REDIS_URL":
            status = "pass" if configured else "warn"
            detail = (
                "configured"
                if configured
                else "not configured; async Telegram and local readiness are disabled"
            )
            required = False
        elif key == "MOCK_NOTION_DATA_DIR":
            if not configured:
                status = "pass"
                detail = "using bundled mock data directory"
            else:
                configured_path = Path(environ[key])
                if not configured_path.is_absolute():
                    configured_path = project_root / configured_path
                path_exists = configured_path.is_dir()
                status = "pass" if path_exists else "fail"
                detail = "configured directory exists" if path_exists else "configured directory is missing"
            required = True
        elif key == "NOTION_BACKEND":
            backend = environ.get(key, "mock").strip().lower() or "mock"
            if backend not in {"mock", "live"}:
                status = "fail"
                detail = "must be mock or live"
                required = True
            elif backend == "live" and not environ.get("NOTION_TOKEN", "").strip():
                status = "fail"
                detail = "live backend requires NOTION_TOKEN"
                required = True
            else:
                status = "pass"
                detail = f"using {backend} backend"
                required = True
        elif key == "OPENAI_API_KEY":
            status = "pass" if configured else "warn"
            detail = "configured" if configured else "missing; server-backed indexing/QA/proposals will fail closed"
            required = False
        elif key == "NOTION_TOKEN":
            backend = environ.get("NOTION_BACKEND", "mock").strip().lower() or "mock"
            if backend == "live":
                status = "pass" if configured else "fail"
                detail = "configured for live backend" if configured else "required by live backend"
                required = True
            else:
                status = "pass" if not configured else "warn"
                detail = "not required by mock backend" if not configured else "configured but unused by mock backend"
                required = False
        elif key == "API_BEARER_TOKEN":
            status = "pass" if configured else "warn"
            detail = (
                "configured"
                if configured
                else "missing; API routes remain in local compatibility mode"
            )
            required = False
        elif key == "TELEGRAM_WEBHOOK_SECRET":
            status = "pass" if configured else "warn"
            detail = (
                "configured"
                if configured
                else "missing; Telegram webhook secret validation is disabled"
            )
            required = False
        elif key == "TELEGRAM_ALLOWED_CHAT_IDS":
            status = "pass" if configured else "warn"
            detail = (
                "configured"
                if configured
                else "missing; Telegram accepts all chats after webhook authentication"
            )
            required = False
        elif key in {"MAX_WORKFLOW_COST_USD", "MAX_DAILY_COST_USD"}:
            status = "pass" if configured else "warn"
            detail = (
                "configured"
                if configured
                else "missing; cost budget alert is disabled"
            )
            required = False
        elif key == "WORKFLOW_STALE_AFTER_SECONDS":
            status = "pass"
            detail = "configured" if configured else "using 3600 second default"
            required = False
        else:
            status = "pass" if configured else "warn"
            detail = "configured" if configured else "missing; Telegram live transport is disabled"
            required = False

        yield CheckResult(
            key=f"config:{key}",
            status=status,
            detail=detail,
            required=required,
        )


def run_preflight(
    *,
    profile: str = "api",
    environ: Optional[Mapping[str, str]] = None,
    module_finder: ModuleFinder = _default_module_finder,
    command_finder: CommandFinder = _default_command_finder,
    command_runner: CommandRunner = _default_command_runner,
    required_commands: Sequence[str] = (),
    project_root: Path = PROJECT_ROOT,
    python_version: Optional[Tuple[int, ...]] = None,
) -> PreflightReport:
    if profile not in PROFILE_DEPENDENCIES:
        raise ValueError(f"unknown preflight profile: {profile}")

    environment = os.environ if environ is None else environ
    checks = [
        _check_python(python_version),
        *_check_project_files(project_root),
        *_check_dependencies(profile, module_finder),
        *_check_commands(required_commands, command_finder),
        *_check_configuration(environment, project_root),
    ]

    if profile == "ocr":
        tesseract_available = command_finder("tesseract") is not None
        checks.extend(_check_commands(("tesseract",), command_finder))
        if tesseract_available:
            checks.extend(_check_tesseract_languages(command_runner))

    return PreflightReport(profile=profile, checks=tuple(checks))


def _report_as_json(report: PreflightReport) -> str:
    payload = {
        "profile": report.profile,
        "result": "fail" if report.failed else "pass",
        "checks": [asdict(check) for check in report.checks],
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _render_human(report: PreflightReport) -> str:
    lines = ["LearnLoop preflight", f"profile={report.profile}"]
    for check in report.checks:
        lines.append(
            f"[{check.status.upper()}] {check.key}: {check.detail}"
        )
    status_counts = {
        status: sum(check.status == status for check in report.checks)
        for status in ("pass", "warn", "fail")
    }
    lines.append(
        "summary="
        f"pass:{status_counts['pass']} "
        f"warn:{status_counts['warn']} "
        f"fail:{status_counts['fail']}"
    )
    lines.append(f"result={'fail' if report.failed else 'pass'}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check LearnLoop local dependencies and configuration safely."
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_DEPENDENCIES),
        default="api",
        help="dependency profile to check (default: api)",
    )
    parser.add_argument(
        "--require-command",
        action="append",
        default=[],
        metavar="COMMAND",
        help="require an executable command; may be repeated",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable status without configuration values",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_preflight(
        profile=args.profile,
        required_commands=args.require_command,
    )
    print(_report_as_json(report) if args.json else _render_human(report))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
