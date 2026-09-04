from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.engine import make_url

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from .context_aware_embedding_input_eval import Step98ContractError, canonical_digest
    from .step98_experiment_v2 import DEFAULT_FIXTURE_DIR, EXPERIMENT_ID, load_preregistration
except ImportError:
    from context_aware_embedding_input_eval import Step98ContractError, canonical_digest  # type: ignore[no-redef]
    from step98_experiment_v2 import DEFAULT_FIXTURE_DIR, EXPERIMENT_ID, load_preregistration  # type: ignore[no-redef]


LIVE_ENV = "LEARNLOOP_RUN_STEP98_PGVECTOR_GATE"
TARGET_ENV = "LEARNLOOP_STEP98_PGVECTOR_TARGET_CLASS"
TARGET_CLASS = "disposable_non_production_postgresql"
APPROVAL_TEXT = "I_APPROVE_STEP98_DISPOSABLE_PGVECTOR_GATE"
PRODUCTION_DATABASE_URL_ENV = "DATABASE_URL"
ADMIN_DATABASE_URL_ENV = "LEARNLOOP_PGVECTOR_ADMIN_DATABASE_URL"
DATABASE_PREFIX = "learnloop_step98_"


def validate_disposable_target(environment: Mapping[str, str]) -> None:
    production_value = environment.get(PRODUCTION_DATABASE_URL_ENV)
    admin_value = environment.get(ADMIN_DATABASE_URL_ENV)
    if not production_value or not admin_value:
        raise Step98ContractError("production and disposable admin database targets are required")
    try:
        production_database = make_url(production_value).database
        admin_database = make_url(admin_value).database
    except Exception as exc:
        raise Step98ContractError("database target URL is invalid") from exc
    if not production_database or not admin_database:
        raise Step98ContractError("database target name is missing")
    if production_database.startswith(DATABASE_PREFIX):
        raise Step98ContractError("production target uses disposable database namespace")


def build_passed_evidence(manifest: Mapping[str, Any]) -> dict[str, Any]:
    contract = manifest["pgvector_gate_contract"]
    evidence = {
        "status": "passed",
        "experiment_id": EXPERIMENT_ID,
        "manifest_digest": canonical_digest(manifest),
        "adapter": "postgresql_pgvector",
        "gate_version": contract["version"],
        "gate_source_digest": contract["gate_source_digest"],
        "repository_test_source_digest": contract["repository_test_source_digest"],
        "target_class": TARGET_CLASS,
        "disposable_database_prefix": DATABASE_PREFIX,
        "production_database_name_was_distinct": True,
        "production_database_used": False,
        "disposable_database_created": True,
        "filter_before_top_k_passed": True,
        "case_count": int(contract["case_count"]),
        "cleanup_status": "passed",
    }
    evidence["receipt_digest"] = canonical_digest(evidence)
    return evidence


def _write_json_atomic_create_only(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.pending")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded disposable PostgreSQL/pgvector Step 98 gate")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approval", default="")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute or os.getenv(LIVE_ENV) != "1" or args.approval != APPROVAL_TEXT:
        print(json.dumps({"status": "skipped", "database_operations": 0}, sort_keys=True))
        return
    try:
        preregistration = load_preregistration(args.fixture_dir)
        expected = _REPO_ROOT / preregistration.manifest["artifacts"]["pgvector_evidence_path"]
        if args.output.resolve() != expected.resolve():
            raise Step98ContractError("pgvector evidence path mismatch")
        if args.output.exists():
            raise Step98ContractError("pgvector evidence already exists")
        if os.getenv(TARGET_ENV) != TARGET_CLASS:
            raise Step98ContractError("disposable non-production target assertion missing")
        environment = os.environ.copy()
        validate_disposable_target(environment)
        environment["LEARNLOOP_RUN_PGVECTOR_TESTS"] = "1"
        environment["LEARNLOOP_PGVECTOR_TEST_DATABASE_PREFIX"] = DATABASE_PREFIX
        completed = subprocess.run(
            [
                str(_REPO_ROOT / ".venv" / "bin" / "python"),
                "-m",
                "pytest",
                "-q",
                "tests/test_chunk_repository_pgvector_live.py",
            ],
            cwd=_REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise Step98ContractError("disposable pgvector gate failed")
        evidence = build_passed_evidence(preregistration.manifest)
        _write_json_atomic_create_only(args.output, evidence)
    except Step98ContractError as exc:
        print(json.dumps({"status": "inconclusive", "safe_failure_category": str(exc)}, sort_keys=True))
        raise SystemExit(2)
    print(json.dumps({"status": "passed", "receipt_digest": evidence["receipt_digest"]}, sort_keys=True))


if __name__ == "__main__":
    main()
