from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.evals import context_aware_embedding_input_eval as v1
from tests.evals.context_aware_embedding_input_eval import canonical_digest, file_digest
from tests.evals.step98_experiment_v2 import (
    EXPERIMENT_ID,
    plan_capture_unfrozen,
    validate_public_safe_sources,
)


EXP001_DIR = REPO_ROOT / "tests" / "evals" / "fixtures" / "step_98" / "step98-exp-001"
DEFAULT_OUTPUT = REPO_ROOT / "tests" / "evals" / "fixtures" / "step_98" / EXPERIMENT_ID


def _dump_yaml(path: Path, payload: Any) -> None:
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def generate(output: Path) -> str:
    if output.exists():
        if (output / "manifest.sha256").exists():
            raise RuntimeError("Refusing to overwrite a frozen exp-002 fixture directory")
        allowed = {"manifest.yaml", "source_records.yaml", "chunks.yaml", "queries.yaml"}
        if any(path.name not in allowed for path in output.iterdir()):
            raise RuntimeError("Refusing to replace an unexpected unfrozen fixture directory")
    else:
        output.mkdir(parents=True)
    for filename in ("source_records.yaml", "chunks.yaml", "queries.yaml"):
        (output / filename).write_bytes((EXP001_DIR / filename).read_bytes())

    original = yaml.safe_load((EXP001_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    if not isinstance(original, dict):
        raise RuntimeError("Invalid exp-001 manifest")
    manifest = copy.deepcopy(original)
    manifest["experiment_id"] = EXPERIMENT_ID
    manifest["schema_version"] = "step98_manifest_v2"
    manifest["fixture_class"] = "public_safe"
    implementation_paths = {
        "builder": "src/rag/embedding_input_builder.py",
        "contract": "tests/evals/step98_experiment_v2.py",
        "scoring": "tests/evals/step98_experiment_v2.py",
        "capture": "tests/evals/step98_phase_b_capture_v2.py",
        "phase_c": "tests/evals/step98_phase_c.py",
        "safety": "tests/evals/step98_repository_safety_eval.py",
        "citation": "tests/evals/step98_citation_eval.py",
        "pgvector_gate": "tests/evals/step98_pgvector_gate.py",
    }
    manifest["implementation"] = {
        key: value
        for role, path in implementation_paths.items()
        for key, value in (
            (f"{role}_source_path", path),
            (f"{role}_source_digest", file_digest(REPO_ROOT / path)),
        )
    }
    dependency_paths = (
        "tests/evals/context_aware_embedding_input_eval.py",
        "src/providers/embedding.py",
        "tests/evals/citation_accuracy_eval.py",
        "tests/evals/golden_questions.py",
        "tests/evals/retrieval_eval.py",
        "tests/evals/golden_questions.yaml",
        "src/services/embedding_batch_service.py",
        "src/services/cost_tracker.py",
        "tests/test_chunk_repository_pgvector_live.py",
        "src/observability/external_error.py",
        "src/rag/__init__.py",
        "src/rag/retriever.py",
        "src/repositories/__init__.py",
        "src/repositories/chunk_repository.py",
        "src/db/base.py",
        "src/db/models.py",
    )
    manifest["implementation_dependencies"] = [
        {"path": path, "digest": file_digest(REPO_ROOT / path)}
        for path in dependency_paths
    ]
    capture = manifest["capture"]
    capture.update(
        {
            "logical_request_count": 15,
            "total_inputs": 396,
            "max_external_attempts": 16,
            "request_plan_digest": "pending-generation",
            "artifact_contract": "immutable_capture_directory_v2",
            "output_root": "dev_state/artifacts/step_98",
            "provider_request_timeout_seconds": 60,
            "artifact_finalize_reserve_seconds": 5,
            "approval_id": "step98_exp002_public_safe_capture_v1",
            "budget_id": "step98_exp002_bounded_capture_v1",
        }
    )
    manifest["artifacts"] = {
        "capture_run_id": "step98-exp-002-capture-001",
        "capture_directory": "dev_state/artifacts/step_98/step98-exp-002-capture-001",
        "result_path": "dev_state/artifacts/step_98/step98-exp-002-result.json",
        "pgvector_evidence_path": "dev_state/artifacts/step_98/step98-exp-002-pgvector-evidence.json",
    }
    manifest["pgvector_gate_contract"] = {
        "version": "step98_pgvector_adapter_gate_v1",
        "gate_source_digest": manifest["implementation"]["pgvector_gate_source_digest"],
        "repository_test_source_digest": file_digest(
            REPO_ROOT / "tests" / "test_chunk_repository_pgvector_live.py"
        ),
        "target_class": "disposable_non_production_postgresql",
        "disposable_database_prefix": "learnloop_step98_",
        "production_database_name_must_be_distinct": True,
        "production_database_used": False,
        "filter_before_top_k_required": True,
        "cleanup_required": True,
        "case_count": 3,
    }
    _dump_yaml(output / "manifest.yaml", manifest)
    sources = tuple(v1._load_yaml_list(output / "source_records.yaml", "sources"))
    chunks = tuple(v1._load_yaml_list(output / "chunks.yaml", "chunks"))
    queries = tuple(v1._load_yaml_list(output / "queries.yaml", "queries"))
    v1._validate_fixture(manifest, sources, chunks, queries)
    validate_public_safe_sources(sources)
    preregistration = v1.Preregistration(
        fixture_dir=output,
        manifest=manifest,
        manifest_digest=canonical_digest(manifest),
        sources=sources,
        chunks=chunks,
        queries=queries,
    )
    capture["request_plan_digest"] = plan_capture_unfrozen(preregistration).request_plan_digest
    _dump_yaml(output / "manifest.yaml", manifest)
    return canonical_digest(manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Step 98 exp-002 preregistration artifacts")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    digest = generate(args.output)
    print(json.dumps({"status": "generated_unfrozen", "experiment_id": EXPERIMENT_ID, "manifest_digest": digest}, sort_keys=True))


if __name__ == "__main__":
    main()
