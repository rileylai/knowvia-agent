from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import generate_step99_preregistration as v1
from tests.evals.step99_hybrid_eval import file_digest


EXPERIMENT_ID = "step99-exp-003"
OUTPUT_DIR = v1._REPO_ROOT / "tests/evals/fixtures/step_99" / EXPERIMENT_ID


def build_manifest() -> dict:
    v1.EXPERIMENT_ID = EXPERIMENT_ID
    manifest = v1.build_manifest()
    manifest["experiment_id"] = EXPERIMENT_ID
    manifest["supersedes"] = {
        "experiment_id": "step99-exp-002",
        "terminal_status": "aborted_pre_scoring_source_vector_digest_algorithm",
        "tuning_scored": False,
        "decision_scored": False,
        "canonical_result_created": False,
    }
    manifest["source_vector_digest_contract"] = "step98_raw_float_canonical_json_v1"
    manifest["result_canonicalization_contract"] = "step99_canonical_semantic_json_v1"
    manifest["artifacts"] = {
        "result_path": "dev_state/artifacts/step_99/step99-exp-003-result.json",
        "pgvector_evidence_path": "dev_state/artifacts/step_99/step99-exp-003-pgvector-evidence.json",
    }
    extra_paths = (
        "scripts/generate_step99_exp003_preregistration.py",
        "tests/evals/step99_hybrid_eval_v3.py",
        "tests/evals/step99_pgvector_gate_v3.py",
        "tests/evals/fixtures/step_99/step99-exp-001/manifest.yaml",
        "tests/evals/fixtures/step_99/step99-exp-001/manifest.sha256",
        "tests/evals/fixtures/step_99/step99-exp-002/manifest.yaml",
        "tests/evals/fixtures/step_99/step99-exp-002/manifest.sha256",
    )
    manifest["managed_sources"].extend(
        {"path": path, "sha256": file_digest(v1._REPO_ROOT / path)}
        for path in extra_paths
    )
    return manifest


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / "manifest.yaml"
    if output.exists() or (OUTPUT_DIR / "manifest.sha256").exists():
        raise SystemExit("Step 99 exp-003 preregistration already exists")
    with output.open("x", encoding="utf-8") as manifest_file:
        yaml.safe_dump(build_manifest(), manifest_file, allow_unicode=True, sort_keys=False, width=1000)
    print(json.dumps({"status": "generated", "experiment_id": EXPERIMENT_ID}, sort_keys=True))


if __name__ == "__main__":
    main()
