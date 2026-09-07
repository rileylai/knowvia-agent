from __future__ import annotations

from scripts.demo_preflight import DemoCheck, build_demo_report


def test_demo_preflight_reports_bounded_checks_without_private_details() -> None:
    report = build_demo_report(
        health_status=200,
        readiness_status=200,
        source_names=["Best Practices for Building AI Agents That Work in Production.pdf"],
        required_source_name="Best Practices for Building AI Agents That Work in Production.pdf",
        frontend_status=200,
        frontend_build_status="pass",
        migration_status="head",
        tool_names=["search_knowledge", "search_memory", "save_memory"],
        mcp_tool_names=["search_knowledge", "search_memory", "save_memory"],
    )

    assert report["result"] == "pass"
    assert report["checks"]
    assert all(set(check) == {"key", "status", "detail", "required"} for check in report["checks"])
    assert "Best Practices" not in report["checks"][0]["detail"]


def test_demo_preflight_fails_when_required_source_is_missing() -> None:
    report = build_demo_report(
        health_status=200,
        readiness_status=200,
        source_names=[],
        required_source_name="Project Atlas Deployment Guide.pdf",
        frontend_status=200,
        frontend_build_status="pass",
        migration_status="head",
        tool_names=["search_knowledge", "search_memory", "save_memory"],
        mcp_tool_names=["search_knowledge", "search_memory", "save_memory"],
    )

    source_check = next(check for check in report["checks"] if check["key"] == "source:indexed")
    assert report["result"] == "fail"
    assert source_check == {
        "key": "source:indexed",
        "status": "fail",
        "detail": "required demo source is not indexed",
        "required": True,
    }
