from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from src.queue.base import QueueClient, get_callable_import_path
from src.queue.models import EnqueuedJob, QueueRetryPolicy, validate_timeout_seconds


class FakeQueueClient(QueueClient):
    def __init__(self) -> None:
        self.enqueued_jobs: List[EnqueuedJob] = []

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
        _ = description
        timeout_seconds = validate_timeout_seconds(timeout_seconds)
        job = EnqueuedJob(
            job_id=str(uuid4()),
            queue_name=queue_name,
            function_name=get_callable_import_path(function),
            args=args,
            kwargs=kwargs or {},
            retry_policy=retry_policy,
            timeout_seconds=timeout_seconds,
        )
        self.enqueued_jobs.append(job)
        return job

    def is_available(self) -> bool:
        return True

    def is_scheduler_available(self, *, queue_name: str) -> bool:
        _ = queue_name
        return True

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
