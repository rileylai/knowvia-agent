from __future__ import annotations

from typing import Mapping

from .large_page_indexing_reliability import (
    DATABASE_URL_ENV,
    NOTION_TOKEN_ENV,
    OPENAI_API_KEY_ENV,
    PAGE_ID_ENV,
    RUN_FLAG_ENV,
    run_guarded_verification,
)


def test_default_path_performs_no_live_work() -> None:
    calls = 0

    def runner(
        environment: Mapping[str, str],
        max_request_count: int,
        total_token_estimate_budget: int,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"status": "passed"}

    report = run_guarded_verification(
        include_live=False,
        approved=False,
        environment={},
        live_runner=runner,
    )

    assert report["status"] == "skipped"
    assert calls == 0


def test_live_path_requires_all_three_gates() -> None:
    environment = {
        RUN_FLAG_ENV: "1",
        NOTION_TOKEN_ENV: "configured",
        OPENAI_API_KEY_ENV: "configured",
        PAGE_ID_ENV: "configured",
        DATABASE_URL_ENV: "configured",
    }

    report = run_guarded_verification(
        include_live=True,
        approved=False,
        environment=environment,
        live_runner=lambda *_: {"status": "passed"},
    )

    assert report["status"] == "failed"


def test_live_path_requires_target_and_credentials_without_exposing_values() -> None:
    report = run_guarded_verification(
        include_live=True,
        approved=True,
        environment={RUN_FLAG_ENV: "1", NOTION_TOKEN_ENV: "private-token"},
        live_runner=lambda *_: {"status": "passed"},
    )

    assert report == {
        "status": "failed",
        "message": "live single-page reliability configuration is incomplete",
    }
    assert "private-token" not in str(report)


def test_live_path_passes_only_bounded_controls_to_runner() -> None:
    captured: tuple[int, int] | None = None
    environment = {
        RUN_FLAG_ENV: "1",
        NOTION_TOKEN_ENV: "configured",
        OPENAI_API_KEY_ENV: "configured",
        PAGE_ID_ENV: "configured",
        DATABASE_URL_ENV: "configured",
    }

    def runner(
        selected_environment: Mapping[str, str],
        max_request_count: int,
        total_token_estimate_budget: int,
    ) -> dict[str, object]:
        nonlocal captured
        assert selected_environment is environment
        captured = (max_request_count, total_token_estimate_budget)
        return {"status": "passed"}

    report = run_guarded_verification(
        include_live=True,
        approved=True,
        environment=environment,
        max_request_count=7,
        total_token_estimate_budget=900_000,
        live_runner=runner,
    )

    assert report["status"] == "passed"
    assert captured == (7, 900_000)


def test_live_runner_exception_is_redacted() -> None:
    environment = {
        RUN_FLAG_ENV: "1",
        NOTION_TOKEN_ENV: "private-token",
        OPENAI_API_KEY_ENV: "private-key",
        PAGE_ID_ENV: "private-page",
        DATABASE_URL_ENV: "private-database",
    }

    def runner(*_: object) -> dict[str, object]:
        raise RuntimeError("private upstream response")

    report = run_guarded_verification(
        include_live=True,
        approved=True,
        environment=environment,
        live_runner=runner,
    )

    assert report == {
        "status": "failed",
        "message": "single-page reliability verification failed",
    }
