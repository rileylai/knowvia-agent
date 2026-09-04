from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typing import Optional

from src.app.dependencies import get_readiness_service
from src.app.main import app
from src.services import ReadinessService


class _FakeReadinessProbe:
    def __init__(self, *, failed_check: Optional[str] = None) -> None:
        self._failed_check = failed_check

    def check_database(self) -> bool:
        return self._failed_check != "database"

    def check_migration(self) -> bool:
        return self._failed_check != "migration"

    def check_vector_extension(self) -> bool:
        return self._failed_check != "vector"


class _FakeQueueClient:
    def __init__(self, available: bool, scheduler_available: bool = True) -> None:
        self._available = available
        self._scheduler_available = scheduler_available

    def is_available(self) -> bool:
        return self._available

    def is_scheduler_available(self, *, queue_name: str) -> bool:
        _ = queue_name
        return self._scheduler_available


def _override_readiness_service(
    *,
    failed_check: Optional[str] = None,
    mode: str = "test",
    openai_configured: bool = False,
) -> ReadinessService:
    return ReadinessService(
        probe=_FakeReadinessProbe(failed_check=failed_check),
        mode=mode,
        openai_configured=openai_configured,
    )


def test_ready_returns_200_when_all_dependencies_are_available() -> None:
    app.dependency_overrides[get_readiness_service] = _override_readiness_service

    try:
        response = TestClient(app).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["mode"] == "test"
    assert {name: check["status"] for name, check in payload["checks"].items()} == {
        "database": "ok",
        "migration": "ok",
        "vector": "ok",
        "mode": "ok",
    }


@pytest.mark.parametrize(
    ("failed_check", "failure_reason"),
    [
        ("database", "DATABASE_UNAVAILABLE"),
        ("migration", "MIGRATION_NOT_CURRENT"),
        ("vector", "VECTOR_EXTENSION_UNAVAILABLE"),
    ],
)
def test_ready_returns_503_for_dependency_failures(
    failed_check: str,
    failure_reason: str,
) -> None:
    app.dependency_overrides[get_readiness_service] = lambda: _override_readiness_service(
        failed_check=failed_check
    )

    try:
        response = TestClient(app).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["checks"][failed_check]["status"] == "failed"
    assert payload["checks"][failed_check]["failure_reason"] == failure_reason


def test_ready_returns_503_when_local_mode_lacks_openai_configuration() -> None:
    app.dependency_overrides[get_readiness_service] = lambda: _override_readiness_service(
        mode="local",
        openai_configured=False,
    )

    try:
        response = TestClient(app).get("/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["checks"]["mode"]["failure_reason"] == (
        "OPENAI_API_KEY_NOT_CONFIGURED"
    )


def test_health_remains_liveness_when_readiness_is_unavailable() -> None:
    app.dependency_overrides[get_readiness_service] = lambda: _override_readiness_service(
        failed_check="database"
    )

    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_redis_failure_when_queue_is_required() -> None:
    service = ReadinessService(
        probe=_FakeReadinessProbe(),
        mode="local",
        openai_configured=True,
        queue_client=_FakeQueueClient(available=False),
        queue_required=True,
    )

    report = service.check()

    assert report.is_ready is False
    assert report.checks["queue"].failure_reason == "REDIS_UNAVAILABLE"


def test_ready_reports_scheduler_failure_when_redis_is_available() -> None:
    service = ReadinessService(
        probe=_FakeReadinessProbe(),
        mode="local",
        openai_configured=True,
        queue_client=_FakeQueueClient(
            available=True,
            scheduler_available=False,
        ),
        queue_required=True,
    )

    report = service.check()

    assert report.is_ready is False
    assert report.checks["queue"].failure_reason == "RQ_SCHEDULER_NOT_RUNNING"


def test_status_exposes_liveness_and_separate_safe_dependency_states() -> None:
    service = ReadinessService(
        probe=_FakeReadinessProbe(failed_check="database"),
        mode="local",
        openai_configured=True,
        queue_client=_FakeQueueClient(available=False),
        queue_required=True,
        notion_backend="live",
        notion_configured=False,
    )

    report = service.status()

    assert report.liveness.status == "ok"
    assert report.is_ready is False
    assert report.checks["database"].failure_reason == "DATABASE_UNAVAILABLE"
    assert report.checks["provider"].status == "ok"
    assert report.checks["notion"].failure_reason == "NOTION_TOKEN_NOT_CONFIGURED"
    assert report.checks["redis"].failure_reason == "REDIS_UNAVAILABLE"
    assert report.checks["scheduler"].failure_reason == "RQ_SCHEDULER_UNAVAILABLE"
