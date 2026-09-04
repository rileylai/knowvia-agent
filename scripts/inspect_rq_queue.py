#!/usr/bin/env python3
"""Inspect RQ queue and scheduled jobs without mutating Redis state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Optional, Sequence

from redis import Redis

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.queue import RQQueueClient


def inspect_queue(
    *,
    queue_client: RQQueueClient,
    queue_name: str = "telegram",
    limit: int = 50,
):
    """Return safe queue metadata through the QueueClient adapter."""

    return queue_client.inspect_state(queue_name=queue_name, limit=limit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue",
        default="telegram",
        help="RQ queue name to inspect (default: telegram)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum scheduled jobs to list (default: 50)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        raise SystemExit("--limit must be positive")
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        raise SystemExit("REDIS_URL is required")
    try:
        connection = Redis.from_url(redis_url)
        connection.ping()
        queue_client = RQQueueClient(connection=connection)
        report = inspect_queue(
            queue_client=queue_client,
            queue_name=args.queue,
            limit=args.limit,
        )
        report["status"] = "ok"
    except Exception:
        report = {
            "status": "failed",
            "failure_reason": "REDIS_UNAVAILABLE",
            "queue_name": args.queue,
            "inspection_mutates_redis": False,
        }
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        for key, value in report.items():
            if key == "scheduled_jobs":
                continue
            print(f"{key}={value}")
        for job in report.get("scheduled_jobs", []):
            print(json.dumps(job, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
