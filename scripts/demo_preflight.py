#!/usr/bin/env python3
"""Run safe, bounded checks before the Knowvia browser demo."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.shared.memory import create_connected_server_and_client_session

from src.agent import AGENT_TOOL_NAMES, build_agent_tool_registry
from src.mcp.server import NativeMCPServer

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:5173"
DEFAULT_SOURCE_NAME = "Best Practices for Building AI Agents That Work in Production.pdf"


@dataclass(frozen=True)
class DemoCheck:
    key: str
    status: str
    detail: str
    required: bool


def build_demo_report(
    *,
    health_status: Optional[int],
    readiness_status: Optional[int],
    source_names: Iterable[str],
    required_source_name: str,
    frontend_status: Optional[int],
    frontend_build_status: str,
    migration_status: str,
    tool_names: Iterable[str],
    mcp_tool_names: Iterable[str],
) -> Dict[str, Any]:
    source_match = required_source_name in set(source_names)
    expected_tools = sorted(AGENT_TOOL_NAMES)
    checks = [
        DemoCheck("backend:health", _status(health_status == 200), _http_detail(health_status), True),
        DemoCheck("backend:readiness", _status(readiness_status == 200), _http_detail(readiness_status), True),
        DemoCheck(
            "database:migration",
            _status(migration_status == "head"),
            "migration is at head" if migration_status == "head" else "migration is not at head",
            True,
        ),
        DemoCheck("frontend:reachable", _status(frontend_status == 200), _http_detail(frontend_status), True),
        DemoCheck(
            "frontend:build",
            _status(frontend_build_status == "pass"),
            "production build artifact present"
            if frontend_build_status == "pass"
            else "production build artifact is missing",
            True,
        ),
        DemoCheck(
            "source:indexed",
            _status(source_match),
            "required demo source is indexed" if source_match else "required demo source is not indexed",
            True,
        ),
        DemoCheck(
            "agent:tools",
            _status(sorted(tool_names) == expected_tools),
            "three bounded tools available" if sorted(tool_names) == expected_tools else "bounded tool allowlist is incomplete",
            True,
        ),
        DemoCheck(
            "mcp:tools",
            _status(sorted(mcp_tool_names) == expected_tools),
            "native MCP allowlist available" if sorted(mcp_tool_names) == expected_tools else "native MCP allowlist is incomplete",
            True,
        ),
    ]
    serialized = [asdict(check) for check in checks]
    return {
        "result": "pass" if not any(check["required"] and check["status"] == "fail" for check in serialized) else "fail",
        "checks": serialized,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    source_names = _fetch_source_names(f"{args.api_url.rstrip('/')}/api/knowledge/sources")
    report = build_demo_report(
        health_status=_fetch_status(f"{args.api_url.rstrip('/')}/health"),
        readiness_status=_fetch_status(f"{args.api_url.rstrip('/')}/ready"),
        source_names=source_names,
        required_source_name=args.source_name,
        frontend_status=_fetch_status(args.frontend_url),
        frontend_build_status=_frontend_build_status(),
        migration_status=_migration_status(),
        tool_names=sorted(AGENT_TOOL_NAMES),
        mcp_tool_names=_native_mcp_tool_names(),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["result"] == "pass" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the Knowvia demo environment without mutating data.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    parser.add_argument("--source-name", default=DEFAULT_SOURCE_NAME)
    return parser


def _fetch_status(url: str) -> Optional[int]:
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=3) as response:
            return int(response.status)
    except (OSError, URLError, ValueError):
        return None


def _fetch_source_names(url: str) -> List[str]:
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        item.get("display_name", "")
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("display_name"), str)
    ]


def _migration_status() -> str:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "current"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    output = f"{result.stdout}\n{result.stderr}".casefold()
    return "head" if "head" in output else "not_head"


def _native_mcp_tool_names() -> List[str]:
    from eval.runner import FixtureMemoryService, FixtureRetriever

    registry = build_agent_tool_registry(
        retriever=FixtureRetriever([]),
        embedding_client=None,
        memory_service=FixtureMemoryService(),  # type: ignore[arg-type]
    )
    server = NativeMCPServer(registry=registry, owner_id="demo-preflight")

    async def probe() -> List[str]:
        async with create_connected_server_and_client_session(server.protocol_server) as client:
            await client.initialize()
            response = await client.list_tools()
            return sorted(tool.name for tool in response.tools)

    try:
        return asyncio.run(probe())
    except Exception:
        return []


def _frontend_build_status() -> str:
    dist_path = PROJECT_ROOT / "frontend" / "dist"
    return (
        "pass"
        if (dist_path / "index.html").is_file()
        and (dist_path / "assets").is_dir()
        else "fail"
    )


def _status(passed: bool) -> str:
    return "pass" if passed else "fail"


def _http_detail(status: Optional[int]) -> str:
    return f"HTTP {status}" if status is not None else "endpoint unavailable"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
