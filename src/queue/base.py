from __future__ import annotations

from abc import ABC, abstractmethod
import inspect
from typing import Any, Callable, Dict, Optional, Tuple

from src.queue.models import EnqueuedJob, QueueRetryPolicy


def get_callable_import_path(function: Callable[..., Any]) -> str:
    """Return the import path RQ will persist for a module-level function."""

    if not inspect.isfunction(function):
        raise TypeError("queued functions must be module-level functions")
    module_name = getattr(function, "__module__", None)
    qualified_name = getattr(function, "__qualname__", None)
    if (
        not module_name
        or not qualified_name
        or "<locals>" in qualified_name
    ):
        raise ValueError(
            "queued functions must have a fresh-process importable module path"
        )
    return f"{module_name}.{qualified_name}"


class QueueClient(ABC):
    @abstractmethod
    def enqueue(
        self,
        *,
        queue_name: str,
        function: Callable[..., Any],
        args: Tuple[Any, ...] = (),
        kwargs: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        retry_policy: Optional[QueueRetryPolicy] = None,
        timeout_seconds: Optional[int] = None,
    ) -> EnqueuedJob:
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether the queue backend can accept work now."""
        raise NotImplementedError

    def is_scheduler_available(self, *, queue_name: str) -> bool:
        """Return whether delayed jobs for a queue have an active scheduler.

        Queue fakes may keep the default false value. Production adapters must
        report the scheduler's durable liveness signal rather than assuming
        that a reachable Redis server also promotes scheduled jobs.
        """
        _ = queue_name
        return False

    def inspect_state(
        self,
        *,
        queue_name: str,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return safe operator state without mutating queue registries."""
        _ = (queue_name, limit)
        raise NotImplementedError

    def enqueue_in(
        self,
        *,
        queue_name: str,
        function: Callable[..., Any],
        seconds: int,
        args: Tuple[Any, ...] = (),
        kwargs: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        retry_policy: Optional[QueueRetryPolicy] = None,
        timeout_seconds: Optional[int] = None,
    ) -> EnqueuedJob:
        """Enqueue a delayed job through the queue adapter.

        The default keeps simple queue fakes backwards compatible. Production
        adapters override this method so media-group settling remains queued.
        """
        _ = seconds
        return self.enqueue(
            queue_name=queue_name,
            function=function,
            args=args,
            kwargs=kwargs,
            description=description,
            retry_policy=retry_policy,
            timeout_seconds=timeout_seconds,
        )
