from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .runner import run_golden_set, write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the deterministic Knowvia Golden Set.")
    parser.add_argument("--scenario", action="append", default=[], help="run one scenario id; repeatable")
    parser.add_argument("--report", type=Path, default=None, help="write the bounded JSON report to this path")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_golden_set(scenario_ids=args.scenario or None)
    if args.report is not None:
        write_report(report, args.report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

