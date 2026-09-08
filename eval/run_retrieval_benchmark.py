from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from eval.retrieval.runner import (
    DEFAULT_ADMIN_DATABASE_URL,
    RetrievalBenchmarkError,
    run_live_benchmark,
    write_report,
)
from eval.retrieval.schema import BenchmarkSchemaError, load_benchmark


RUN_FLAG_ENV = "KNOWVIA_RUN_RETRIEVAL_BENCHMARK"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
ADMIN_DATABASE_URL_ENV = "KNOWVIA_RETRIEVAL_ADMIN_DATABASE_URL"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the isolated Knowvia Retrieval Quality Benchmark pilot."
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("eval/retrieval/benchmark.yaml"),
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=Path("mock_data"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("/tmp/knowvia-retrieval-pilot.json"),
    )
    parser.add_argument(
        "--admin-database-url",
        default=os.getenv(ADMIN_DATABASE_URL_ENV, DEFAULT_ADMIN_DATABASE_URL),
    )
    parser.add_argument(
        "--keep-database",
        action="store_true",
        help="Keep the disposable database for follow-up inspection.",
    )
    args = parser.parse_args()

    if os.getenv(RUN_FLAG_ENV) != "1":
        parser.error(f"Set {RUN_FLAG_ENV}=1 to confirm live embedding spend.")
    api_key = os.getenv(OPENAI_API_KEY_ENV, "").strip()
    if not api_key:
        parser.error(f"{OPENAI_API_KEY_ENV} is required for the live pilot.")

    try:
        benchmark = load_benchmark(args.benchmark)
        report = asyncio.run(
            run_live_benchmark(
                benchmark,
                corpus_root=args.corpus_root,
                openai_api_key=api_key,
                admin_database_url=args.admin_database_url,
                keep_database=args.keep_database,
            )
        )
    except (BenchmarkSchemaError, RetrievalBenchmarkError) as exc:
        parser.error(str(exc))

    write_report(report, args.report)
    print(f"report={args.report}")
    print(f"benchmark={report['benchmark_id']} version={report['benchmark_version']}")
    metrics = report["metrics"]
    for key in (
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "mrr",
        "full_case_success_at_5",
        "source_recall_at_5",
        "page_coverage_at_5",
        "negative_rejection_rate",
        "false_positive_retrieval_rate",
    ):
        print(f"{key}={metrics[key]}")
    print(f"passed_count={metrics['passed_count']}/{metrics['case_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
