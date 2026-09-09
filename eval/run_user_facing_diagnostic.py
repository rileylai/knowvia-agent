from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from eval.user_facing_diagnostic import (
    DiagnosticReportError,
    DiagnosticSetError,
    load_diagnostic_set,
    run_live_diagnostic,
    write_json_report,
)


RUN_FLAG_ENV = "KNOWVIA_RUN_USER_FACING_DIAGNOSTIC"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
DATABASE_URL_ENV = "DATABASE_URL"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the bounded 8.5 user-facing answer quality diagnostic."
    )
    parser.add_argument(
        "--diagnostic-set",
        type=Path,
        default=Path("eval/user_facing_diagnostic.yaml"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "eval/retrieval/reports/8.5-user-facing-answer-quality-20260909.json"
        ),
    )
    args = parser.parse_args()
    if os.getenv(RUN_FLAG_ENV) != "1":
        parser.error(f"Set {RUN_FLAG_ENV}=1 to confirm bounded live provider execution.")
    api_key = os.getenv(OPENAI_API_KEY_ENV, "").strip()
    database_url = os.getenv(DATABASE_URL_ENV, "").strip()
    if not api_key:
        parser.error(f"{OPENAI_API_KEY_ENV} is required for the live diagnostic.")
    if not database_url:
        parser.error(f"{DATABASE_URL_ENV} is required for the local corpus diagnostic.")
    try:
        diagnostic_set = load_diagnostic_set(args.diagnostic_set)
        report = asyncio.run(
            run_live_diagnostic(
                diagnostic_set,
                database_url=database_url,
                openai_api_key=api_key,
            )
        )
        write_json_report(report, args.report)
    except (DiagnosticReportError, DiagnosticSetError) as exc:
        parser.error(str(exc))
    print(f"report={args.report}")
    print(f"total={report['aggregate']['total']}")
    print(f"false_insufficient={report['aggregate']['false_insufficient_count']}")
    print(
        "classifications="
        f"{report['aggregate']['false_insufficient_root_causes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
