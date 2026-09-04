#!/usr/bin/env python3
"""Run the fail-closed synthetic-data release gate."""

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


def run_release_gate(*, database_url: str) -> Dict[str, Any]:
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        session = session_factory()
        try:
            counts = SyntheticDataRepository(session).inspect()
            payload = {
                "status": "passed" if counts.is_clean else "failed",
                "check_id": "synthetic_database_data",
                "counts": asdict(counts),
            }
            if not counts.is_clean:
                payload["error_code"] = (
                    "SYNTHETIC_PRODUCTION_CHUNKS_PRESENT"
                    if counts.production_chunk_count
                    else "SYNTHETIC_DATA_PRESENT"
                )
            return payload
        finally:
            session.close()
    except Exception as exc:
        _ = exc
        return {
            "status": "failed",
            "check_id": "synthetic_database_data",
            "error_code": "DATABASE_INSPECTION_FAILED",
        }
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail closed when known synthetic data is in the live DB."
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL
    payload = run_release_gate(database_url=database_url)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"release gate {payload['status']}")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
