from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Optional, Protocol

if TYPE_CHECKING:
    from src.queue import QueueClient


READINESS_OK = "ok"
READINESS_FAILED = "failed"
READINESS_NOT_REQUIRED = "not_required"
TELEGRAM_QUEUE_NAME = "telegram"


@dataclass(frozen=True)
class ReadinessCheckResult:
    status: str
    detail: str
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class ReadinessReport:
    mode: str
    checks: Dict[str, ReadinessCheckResult]

    @property
    def is_ready(self) -> bool:
        return all(
            check.status in {READINESS_OK, READINESS_NOT_REQUIRED}
            for check in self.checks.values()
        )


@dataclass(frozen=True)
class ReadinessStatusReport:
    liveness: ReadinessCheckResult
    readiness: ReadinessReport
    checks: Dict[str, ReadinessCheckResult]

    @property
    def is_ready(self) -> bool:
        return self.readiness.is_ready


class ReadinessProbe(Protocol):
    def check_database(self) -> bool:
        ...

    def check_migration(self) -> bool:
        ...

    def check_vector_extension(self) -> bool:
        ...


class ReadinessService:
    def __init__(
        self,
        *,
        probe: ReadinessProbe,
        mode: str,
        openai_configured: bool,
        queue_client: Optional["QueueClient"] = None,
        queue_required: bool = False,
        notion_backend: str = "mock",
        notion_configured: Optional[bool] = None,
    ) -> None:
        self._probe = probe
        self._mode = mode
        self._openai_configured = openai_configured
        self._queue_client = queue_client
        self._queue_required = queue_required
        self._notion_backend = notion_backend.strip().lower() or "mock"
        self._notion_configured = notion_configured

    def check(self) -> ReadinessReport:
        checks = {
            "database": self._run_probe(
                self._probe.check_database,
                detail="database connection is available",
                failure_reason="DATABASE_UNAVAILABLE",
            ),
            "migration": self._run_probe(
                self._probe.check_migration,
                detail="database migration is current",
                failure_reason="MIGRATION_NOT_CURRENT",
            ),
            "vector": self._run_probe(
                self._probe.check_vector_extension,
                detail="pgvector extension is available",
                failure_reason="VECTOR_EXTENSION_UNAVAILABLE",
            ),
            "mode": self._check_mode_dependency(),
        }
        if self._queue_required:
            checks["queue"] = self._check_queue_dependency()
        return ReadinessReport(mode=self._mode, checks=checks)

    def status(self) -> ReadinessStatusReport:
        """Return liveness plus safe, separately named dependency states."""
        readiness = self.check()
        redis_check = self._check_redis_dependency()
        checks = {
            "database": readiness.checks["database"],
            "migration": readiness.checks["migration"],
            "vector": readiness.checks["vector"],
            "provider": readiness.checks["mode"],
            "notion": self._check_notion_dependency(),
            "redis": redis_check,
            "scheduler": self._check_scheduler_dependency(redis_check),
        }
        return ReadinessStatusReport(
            liveness=ReadinessCheckResult(
                status=READINESS_OK,
                detail="process is running",
            ),
            readiness=ReadinessReport(mode=self._mode, checks=checks),
            checks=checks,
        )

    def _check_queue_dependency(self) -> ReadinessCheckResult:
        if self._queue_client is None:
            return ReadinessCheckResult(
                status=READINESS_FAILED,
                detail="Redis queue configuration is missing",
                failure_reason="REDIS_URL_NOT_CONFIGURED",
            )
        if self._queue_client.is_available():
            try:
                scheduler_available = self._queue_client.is_scheduler_available(
                    queue_name=TELEGRAM_QUEUE_NAME
                )
            except Exception:
                scheduler_available = False
            if not scheduler_available:
                return ReadinessCheckResult(
                    status=READINESS_FAILED,
                    detail="Redis is available but the RQ scheduler is not running",
                    failure_reason="RQ_SCHEDULER_NOT_RUNNING",
                )
            return ReadinessCheckResult(
                status=READINESS_OK,
                detail="Redis queue and RQ scheduler are available",
            )
        return ReadinessCheckResult(
            status=READINESS_FAILED,
            detail="Redis queue is unavailable",
            failure_reason="REDIS_UNAVAILABLE",
        )

    def _check_redis_dependency(self) -> ReadinessCheckResult:
        if not self._queue_required:
            return ReadinessCheckResult(
                status=READINESS_NOT_REQUIRED,
                detail="Redis is not required in this mode",
            )
        if self._queue_client is None:
            return ReadinessCheckResult(
                status=READINESS_FAILED,
                detail="Redis configuration is missing",
                failure_reason="REDIS_URL_NOT_CONFIGURED",
            )
        try:
            available = bool(self._queue_client.is_available())
        except Exception:
            available = False
        if available:
            return ReadinessCheckResult(
                status=READINESS_OK,
                detail="Redis is available",
            )
        return ReadinessCheckResult(
            status=READINESS_FAILED,
            detail="Redis is unavailable",
            failure_reason="REDIS_UNAVAILABLE",
        )

    def _check_scheduler_dependency(
        self,
        redis_check: ReadinessCheckResult,
    ) -> ReadinessCheckResult:
        if not self._queue_required:
            return ReadinessCheckResult(
                status=READINESS_NOT_REQUIRED,
                detail="RQ scheduler is not required in this mode",
            )
        if self._queue_client is None:
            return ReadinessCheckResult(
                status=READINESS_FAILED,
                detail="RQ scheduler configuration is missing",
                failure_reason="RQ_SCHEDULER_NOT_CONFIGURED",
            )
        if redis_check.status != READINESS_OK:
            return ReadinessCheckResult(
                status=READINESS_FAILED,
                detail="RQ scheduler cannot be checked while Redis is unavailable",
                failure_reason="RQ_SCHEDULER_UNAVAILABLE",
            )
        try:
            scheduler_available = bool(
                self._queue_client.is_scheduler_available(
                    queue_name=TELEGRAM_QUEUE_NAME
                )
            )
        except Exception:
            scheduler_available = False
        if scheduler_available:
            return ReadinessCheckResult(
                status=READINESS_OK,
                detail="RQ scheduler is available",
            )
        return ReadinessCheckResult(
            status=READINESS_FAILED,
            detail="RQ scheduler is not running",
            failure_reason="RQ_SCHEDULER_NOT_RUNNING",
        )

    def _check_notion_dependency(self) -> ReadinessCheckResult:
        if self._notion_backend == "mock":
            return ReadinessCheckResult(
                status=READINESS_OK,
                detail="mock Notion configuration is available",
            )
        if self._notion_backend == "live" and self._notion_configured is True:
            return ReadinessCheckResult(
                status=READINESS_OK,
                detail="live Notion configuration is present",
            )
        return ReadinessCheckResult(
            status=READINESS_FAILED,
            detail="Notion configuration is missing",
            failure_reason="NOTION_TOKEN_NOT_CONFIGURED",
        )

    def _check_mode_dependency(self) -> ReadinessCheckResult:
        if self._mode in {"test", "demo", "mock"}:
            return ReadinessCheckResult(
                status=READINESS_OK,
                detail="live provider dependency is not required in this mode",
            )
        if self._openai_configured:
            return ReadinessCheckResult(
                status=READINESS_OK,
                detail="OpenAI embedding configuration is present",
            )
        return ReadinessCheckResult(
            status=READINESS_FAILED,
            detail="OpenAI embedding configuration is missing",
            failure_reason="OPENAI_API_KEY_NOT_CONFIGURED",
        )

    @staticmethod
    def _run_probe(
        probe,
        *,
        detail: str,
        failure_reason: str,
    ) -> ReadinessCheckResult:
        try:
            passed = bool(probe())
        except Exception:
            passed = False
        if passed:
            return ReadinessCheckResult(status=READINESS_OK, detail=detail)
        return ReadinessCheckResult(
            status=READINESS_FAILED,
            detail=failure_reason.lower().replace("_", " "),
            failure_reason=failure_reason,
        )
