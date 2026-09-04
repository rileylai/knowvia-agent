from __future__ import annotations

import fakeredis

from scripts.inspect_rq_queue import inspect_queue
from src.queue import RQQueueClient


def inspection_task() -> str:
    return "ok"


def test_rq_inspection_is_read_only_and_redacted() -> None:
    connection = fakeredis.FakeRedis()
    RQQueueClient(connection=connection).enqueue_in(
        queue_name="telegram",
        function=inspection_task,
        seconds=60,
        args=("private-argument-that-must-not-be-inspected",),
    )

    report = inspect_queue(
        queue_client=RQQueueClient(connection=connection),
        queue_name="telegram",
    )

    assert report["queue_name"] == "telegram"
    assert report["scheduler_running"] is False
    assert report["scheduled"] == 1
    assert report["inspection_mutates_redis"] is False
    assert report["scheduled_jobs"][0]["function"].endswith("inspection_task")
    assert "private-argument-that-must-not-be-inspected" not in str(report)
