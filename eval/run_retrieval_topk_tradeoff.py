from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from eval.retrieval.runner import (
    DEFAULT_ADMIN_DATABASE_URL,
    RetrievalBenchmarkError,
)
from eval.retrieval.schema import BenchmarkSchemaError, load_benchmark
from eval.retrieval.topk_tradeoff import (
    TRADEOFF_TOP_KS,
    run_live_tradeoff,
    write_json_report,
)


RUN_FLAG_ENV = "KNOWVIA_RUN_RETRIEVAL_BENCHMARK"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
ADMIN_DATABASE_URL_ENV = "KNOWVIA_RETRIEVAL_ADMIN_DATABASE_URL"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the top-k 5/8/10 retrieval depth tradeoff evaluation."
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
        "--report-dir",
        type=Path,
        default=Path("eval/retrieval/reports"),
    )
    parser.add_argument(
        "--comparison-report",
        type=Path,
        default=Path(
            "eval/retrieval/reports/8.2-top-k-tradeoff-comparison-20260908.json"
        ),
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
        parser.error(f"{OPENAI_API_KEY_ENV} is required for the live evaluation.")

    try:
        benchmark = load_benchmark(args.benchmark)
        comparison, variants = asyncio.run(
            run_live_tradeoff(
                benchmark,
                benchmark_path=args.benchmark,
                corpus_root=args.corpus_root,
                openai_api_key=api_key,
                admin_database_url=args.admin_database_url,
                keep_database=args.keep_database,
            )
        )
    except (BenchmarkSchemaError, RetrievalBenchmarkError) as exc:
        parser.error(str(exc))

    for top_k in TRADEOFF_TOP_KS:
        write_json_report(
            variants[top_k],
            args.report_dir / f"8.2-top-k-{top_k}-20260908.json",
        )
    write_json_report(comparison, args.comparison_report)

    print(f"comparison_report={args.comparison_report}")
    print(f"baseline_reproducibility={comparison['baseline_reproducibility']}")
    for top_k in TRADEOFF_TOP_KS:
        metrics = comparison["positive_metrics_by_top_k"][str(top_k)]
        negative = comparison["negative_safety_by_top_k"][str(top_k)]
        print(
            f"top_k={top_k} recall={metrics['recall_at_requested_top_k']} "
            f"full_case={metrics['full_case_success_at_requested_top_k']} "
            f"negative_rejection={negative['negative_rejection_rate']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

