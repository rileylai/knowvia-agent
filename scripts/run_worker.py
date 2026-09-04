#!/usr/bin/env python3
"""Run the LearnLoop RQ worker for Telegram background jobs."""

from __future__ import annotations

import argparse
import logging
import os
import platform
from pathlib import Path
import sys


def ensure_repo_root_on_sys_path() -> Path:
    """Make imports independent of the worker's cwd or script launch path."""

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)
    return repo_root


ensure_repo_root_on_sys_path()

from redis import Redis
from rq import Queue
from rq.utils import import_attribute
from rq.worker import SpawnWorker, Worker


LOGGER = logging.getLogger("learnloop.worker")


def run_worker(*, worker: Worker, burst: bool) -> bool:
    """Run the Telegram worker with RQ's embedded scheduler enabled."""

    return worker.work(burst=burst, with_scheduler=True)


def select_worker_class(
    *,
    system_name: str | None = None,
    requested: str = "auto",
) -> type[Worker]:
    """Select the RQ work-horse policy for the current operating system."""

    if requested not in {"auto", "spawn", "worker"}:
        raise ValueError(
            "worker class must be one of: auto, spawn, worker"
        )

    resolved_system = system_name or platform.system()
    if requested == "auto":
        return SpawnWorker if resolved_system == "Darwin" else Worker
    if requested == "spawn":
        return SpawnWorker
    if resolved_system == "Darwin":
        raise ValueError(
            "standard RQ Worker is disabled on macOS; use SpawnWorker"
        )
    return Worker


def validate_telegram_job_import() -> None:
    """Fail fast if RQ cannot resolve either Telegram job callable."""

    from src.worker.telegram import (
        TELEGRAM_FULL_INDEX_JOB_PATH,
        TELEGRAM_WEBHOOK_JOB_PATH,
        process_telegram_full_index_job,
        process_telegram_webhook_job,
    )

    for path, function in (
        (TELEGRAM_WEBHOOK_JOB_PATH, process_telegram_webhook_job),
        (TELEGRAM_FULL_INDEX_JOB_PATH, process_telegram_full_index_job),
    ):
        resolved = import_attribute(path)
        if resolved is not function:
            raise RuntimeError("RQ resolved a different Telegram worker callable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue",
        default="telegram",
        help="RQ queue name to consume (default: telegram)",
    )
    parser.add_argument(
        "--worker-class",
        choices=("auto", "spawn", "worker"),
        default="auto",
        help=(
            "Worker policy: auto selects SpawnWorker on macOS and Worker "
            "elsewhere (default: auto)"
        ),
    )
    parser.add_argument(
        "--burst",
        action="store_true",
        help="Exit after the selected queues are empty (safe smoke check)",
    )
    args = parser.parse_args()

    validate_telegram_job_import()

    try:
        worker_class = select_worker_class(requested=args.worker_class)
    except ValueError as exc:
        parser.error(str(exc))

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    LOGGER.info("worker_class=%s", worker_class.__name__)

    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        parser.error("REDIS_URL is required to start the worker")

    connection = Redis.from_url(redis_url)
    connection.ping()
    worker = worker_class(
        [Queue(name=args.queue, connection=connection)],
        connection=connection,
    )
    LOGGER.info(
        "queue=%s worker_started=true scheduler_enabled=true "
        "scheduler_mode=embedded worker_class=%s",
        args.queue,
        worker_class.__name__,
    )
    run_worker(worker=worker, burst=args.burst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
