from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class QueueRetryPolicy:
    """Bounded retry settings understood by queue adapters."""

    max_retries: int = 0
    retry_intervals: Tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if any(interval < 0 for interval in self.retry_intervals):
            raise ValueError("retry intervals must be non-negative")


def validate_timeout_seconds(timeout_seconds: Optional[int]) -> Optional[int]:
    """Validate the backend-agnostic per-job execution timeout."""

    if timeout_seconds is None:
        return None
    if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    return int(timeout_seconds)


@dataclass(frozen=True)
class EnqueuedJob:
    job_id: str
    queue_name: str
    function_name: str
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]
    retry_policy: Optional[QueueRetryPolicy] = None
    timeout_seconds: Optional[int] = None
