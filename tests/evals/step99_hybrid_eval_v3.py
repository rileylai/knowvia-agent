from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.evals import context_aware_embedding_input_eval as source_contract
from tests.evals import step99_hybrid_eval as core


EXPERIMENT_ID = "step99-exp-003"
DEFAULT_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "step_99" / EXPERIMENT_ID


def _activate_contract() -> None:
    core.EXPERIMENT_ID = EXPERIMENT_ID
    core.DEFAULT_FIXTURE_DIR = DEFAULT_FIXTURE_DIR
    core.load_source_vectors = load_source_vectors_v3


def load_source_vectors_v3(
    manifest: Mapping[str, Any],
    *,
    chunks: Sequence[Mapping[str, Any]],
    queries: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, list[float]], Dict[str, list[float]]]:
    provenance = manifest["vector_provenance"]
    receipt_path = core._REPO_ROOT / provenance["receipt_path"]
    vectors_path = core._REPO_ROOT / provenance["vectors_path"]
    receipt = core._load_json_mapping(receipt_path)
    body = dict(receipt)
    receipt_digest = body.pop("receipt_digest", None)
    if core.canonical_digest(body) != receipt_digest:
        raise core.Step99ContractError("source capture receipt digest mismatch")
    required = {
        "status": "captured",
        "capture_run_digest": provenance["capture_run_digest"],
        "manifest_digest": provenance["source_manifest_digest"],
        "requested_model_alias": provenance["model"],
        "dimensions": provenance["dimensions"],
        "query_vector_set_digest": provenance["query_vector_set_digest"],
    }
    if any(receipt.get(key) != value for key, value in required.items()):
        raise core.Step99ContractError("source capture provenance mismatch")
    if receipt.get("vectors_artifact_created") is not True:
        raise core.Step99ContractError("complete retained source vectors required")
    if receipt.get("document_vector_set_digests", {}).get("body_only_v1") != provenance["body_vector_set_digest"]:
        raise core.Step99ContractError("body-only vector provenance mismatch")
    if core.file_digest(vectors_path) != provenance["vectors_file_sha256"]:
        raise core.Step99ContractError("source vectors file digest mismatch")
    payload = core._load_json_mapping(vectors_path)
    query_vectors = payload.get("query_vectors")
    document_vectors = payload.get("document_vectors", {}).get("body_only_v1")
    if not isinstance(query_vectors, dict) or not isinstance(document_vectors, dict):
        raise core.Step99ContractError("source vector sets missing")
    if source_contract.canonical_digest(query_vectors) != provenance["query_vector_set_digest"]:
        raise core.Step99ContractError("query vector-set digest mismatch")
    if source_contract.canonical_digest(document_vectors) != provenance["body_vector_set_digest"]:
        raise core.Step99ContractError("body vector-set digest mismatch")
    if set(query_vectors) != {item["query_id"] for item in queries}:
        raise core.Step99ContractError("query vector identities mismatch")
    if set(document_vectors) != {item["chunk_id"] for item in chunks}:
        raise core.Step99ContractError("body vector identities mismatch")
    dimensions = int(provenance["dimensions"])
    if any(len(vector) != dimensions for vector in query_vectors.values()) or any(len(vector) != dimensions for vector in document_vectors.values()):
        raise core.Step99ContractError("source vector dimensions mismatch")
    return query_vectors, document_vectors


def main() -> None:
    _activate_contract()
    parser = argparse.ArgumentParser(description="Offline Step 99 exp-003 hybrid evaluation")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--pgvector-evidence", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    try:
        if args.freeze:
            manifest = core.load_contract(args.fixture_dir, create_receipt=True)
            print(json.dumps({"status": "frozen", "experiment_id": EXPERIMENT_ID, "manifest_digest": core.canonical_digest(manifest)}, sort_keys=True))
            return
        if args.evaluate:
            if args.result is None:
                raise core.Step99ContractError("canonical result path required")
            manifest = core.load_contract(args.fixture_dir)
            expected = core._REPO_ROOT / manifest["artifacts"]["result_path"]
            if args.result.resolve() != expected.resolve():
                raise core.Step99ContractError("canonical result path mismatch")
            payload = core.evaluate_experiment(fixture_dir=args.fixture_dir, pgvector_evidence_path=args.pgvector_evidence)
            replay = core.write_or_replay(args.result, payload)
            print(json.dumps({"status": payload["decision"]["status"], "selected_weight_id": payload["selected_weight_id"], "result_digest": payload["result_digest"], "replay_status": replay}, sort_keys=True))
            return
        manifest = core.load_contract(args.fixture_dir)
        print(json.dumps({"status": "validated", "manifest_digest": core.canonical_digest(manifest)}, sort_keys=True))
    except core.Step99ContractError as exc:
        print(json.dumps({"status": "inconclusive", "safe_failure_category": str(exc)}, sort_keys=True))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
