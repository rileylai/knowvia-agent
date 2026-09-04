#!/usr/bin/env python3
"""Inspect or explicitly remove known synthetic data from PostgreSQL."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.repositories import SyntheticDataRepository  # noqa: E402

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://knowvia:knowvia@localhost:5433/knowvia"
)
CONFIRMATION_TEXT = "CLEAN_SYNTHETIC_DATA"


class SyntheticCleanupError(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or safely remove the fixed synthetic data allowlist."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default=None)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def run_cleanup(*, database_url: str, apply: bool) -> Dict[str, Any]:
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        session = session_factory()
        try:
            repository = SyntheticDataRepository(session)
            if apply:
                with session.begin():
                    before = repository.inspect()
                    removed = repository.delete_synthetic_data()
                after = repository.inspect()
                return {
                    "status": "applied",
                    "before": asdict(before),
                    "removed": asdict(removed),
                    "after": asdict(after),
                }
            before = repository.inspect()
            return {
                "status": "dry_run",
                "would_remove": asdict(before),
            }
        finally:
            session.close()
    except Exception as exc:
        _ = exc
        raise SyntheticCleanupError("DATABASE_INSPECTION_FAILED") from None
    finally:
        engine.dispose()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.apply and args.confirm != CONFIRMATION_TEXT:
        return _print_error(args, "SYNTHETIC_CLEANUP_CONFIRMATION_REQUIRED")

    database_url = args.database_url or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL
    try:
        payload = run_cleanup(database_url=database_url, apply=args.apply)
    except SyntheticCleanupError as exc:
        return _print_error(args, exc.error_code)
    return _print_payload(args, payload)


def _print_payload(args: argparse.Namespace, payload: Dict[str, Any]) -> int:
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"synthetic data cleanup {payload['status']}")
    return 0


def _print_error(args: argparse.Namespace, error_code: str) -> int:
    payload = {"status": "failed", "error_code": error_code}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"synthetic data cleanup failed: {error_code}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
