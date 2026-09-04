from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from . import context_aware_embedding_input_eval as v1
    from .step98_citation_eval import evaluate_citation_gates, evaluate_step98_decision_citations
    from .step98_experiment_v2 import DEFAULT_FIXTURE_DIR, EXPERIMENT_ID, implementation_bundle_digest, load_preregistration, plan_capture, score_rankings
    from .step98_repository_safety_eval import evaluate_production_repository_safety
except ImportError:
    import context_aware_embedding_input_eval as v1  # type: ignore[no-redef]
    from step98_citation_eval import evaluate_citation_gates, evaluate_step98_decision_citations  # type: ignore[no-redef]
    from step98_experiment_v2 import DEFAULT_FIXTURE_DIR, EXPERIMENT_ID, implementation_bundle_digest, load_preregistration, plan_capture, score_rankings  # type: ignore[no-redef]
    from step98_repository_safety_eval import evaluate_production_repository_safety  # type: ignore[no-redef]


def validate_capture_bundle(capture_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    receipt_path = capture_dir / "receipt.json"
    if not receipt_path.exists():
        raise v1.Step98ContractError("successful complete capture required")
    receipt = _load_json_mapping(receipt_path)
    if receipt.get("status") != "captured" or receipt.get("vectors_artifact_created") is not True:
        raise v1.Step98ContractError("successful complete capture required")
    expected_receipt_digest = receipt.get("receipt_digest")
    receipt_body = dict(receipt)
    receipt_body.pop("receipt_digest", None)
    if expected_receipt_digest != v1.canonical_digest(receipt_body):
        raise v1.Step98ContractError("capture receipt digest mismatch")
    vectors_path = capture_dir / "vectors.json"
    if not vectors_path.exists() or receipt.get("vectors_file_digest") != v1.file_digest(vectors_path):
        raise v1.Step98ContractError("capture vector artifact digest mismatch")
    vectors = _load_json_mapping(vectors_path)
    metadata = vectors.get("metadata")
    if not isinstance(metadata, dict):
        raise v1.Step98ContractError("capture vector metadata missing")
    if metadata.get("capture_run_digest") != receipt.get("capture_run_digest"):
        raise v1.Step98ContractError("capture run digest mismatch")
    capture_core_keys = (
        "experiment_id",
        "capture_run_id",
        "manifest_digest",
        "request_plan_digest",
        "capture_source_digest",
        "implementation_source_digest",
        "requested_provider",
        "requested_model_alias",
        "dimensions",
        "provider_revision_id",
        "approval_id",
        "budget_id",
        "query_vector_set_digest",
        "document_vector_set_digests",
        "logical_execution_order",
        "completed_logical_request_count",
        "actual_external_attempt_count",
        "retry_count",
        "provider_token_usage",
        "bounded_cost_estimate_usd",
    )
    if receipt.get("capture_run_digest") != v1.canonical_digest(
        {key: receipt.get(key) for key in capture_core_keys}
    ):
        raise v1.Step98ContractError("capture run digest mismatch")
    if v1.canonical_digest(vectors.get("query_vectors")) != receipt.get("query_vector_set_digest"):
        raise v1.Step98ContractError("query vector-set digest mismatch")
    document_vectors = vectors.get("document_vectors")
    if not isinstance(document_vectors, dict):
        raise v1.Step98ContractError("document vector sets missing")
    for variant in v1.VARIANTS:
        if v1.canonical_digest(document_vectors.get(variant)) != receipt.get("document_vector_set_digests", {}).get(variant):
            raise v1.Step98ContractError("document vector-set digest mismatch")
    return receipt, vectors


def evaluate_capture(
    *,
    fixture_dir: Path,
    capture_dir: Path,
    pgvector_evidence_path: Optional[Path] = None,
) -> Dict[str, Any]:
    preregistration = load_preregistration(fixture_dir)
    plan = plan_capture(preregistration)
    expected_capture_dir = _REPO_ROOT / preregistration.manifest["artifacts"]["capture_directory"]
    if capture_dir.resolve() != expected_capture_dir.resolve():
        raise v1.Step98ContractError("canonical capture directory mismatch")
    receipt, vectors = validate_capture_bundle(capture_dir)
    _validate_capture_contract(preregistration, plan, receipt)
    rankings = v1.rank_captured_vectors(
        preregistration,
        query_vectors=vectors["query_vectors"],
        document_vectors=vectors["document_vectors"],
    )
    citation = evaluate_citation_gates()
    decision_citations = evaluate_step98_decision_citations(
        preregistration=preregistration,
        rankings_by_variant=rankings,
    )
    pgvector_passed = _load_pgvector_evidence(
        preregistration,
        pgvector_evidence_path,
    )
    evidence = v1.IndependentGateEvidence(
        citation_recall=citation.citation_recall,
        citation_precision=citation.citation_precision,
        invalid_citation_count=citation.invalid_citation_count,
        derived_header_citation_count=citation.derived_header_citation_count,
        golden_citation_recall=citation.golden_citation_recall,
        golden_citation_precision=citation.golden_citation_precision,
        golden_invalid_citation_count=citation.golden_invalid_citation_count,
        production_repository_safety_passed=evaluate_production_repository_safety(),
        pgvector_adapter_integration_passed=pgvector_passed,
    )
    scoring_bundle_digest = implementation_bundle_digest(preregistration.manifest)
    evaluation = score_rankings(
        preregistration,
        capture_digest=receipt["capture_run_digest"],
        rankings_by_variant=rankings,
        implementation_source_digest=scoring_bundle_digest,
        independent_evidence=evidence,
        citation_evidence_by_variant=decision_citations,
    )
    status = {
        "pass_candidate_identified": "adopt_candidate",
        "no_adoption": "no_adoption",
        "inconclusive": "inconclusive",
    }[evaluation.gate.status]
    return {
        "experiment_id": EXPERIMENT_ID,
        "manifest_digest": preregistration.manifest_digest,
        "capture_run_digest": receipt["capture_run_digest"],
        "scoring_version": evaluation.scoring_version,
        "implementation_source_digest": scoring_bundle_digest,
        "status": status,
        "selected_variant": evaluation.gate.selected_variant,
        "gate_reasons": list(evaluation.gate.reasons),
        "variant_scores": {key: asdict(value) for key, value in evaluation.variant_scores.items()},
        "independent_evidence": asdict(evidence),
        "decision_set_citation_evidence": {
            key: asdict(value) for key, value in decision_citations.items()
        },
        "pgvector_adapter_integration": "passed" if pgvector_passed else "pending_independent_gate",
        "result_digest": evaluation.result_digest,
    }


def write_or_replay_result(result_path: Path, payload: Mapping[str, Any]) -> str:
    if not result_path.exists():
        _write_json_atomic_create_only(result_path, payload)
        return "created"
    canonical = _load_json_mapping(result_path)
    contract = ("manifest_digest", "capture_run_digest", "scoring_version", "implementation_source_digest")
    if any(canonical.get(key) != payload.get(key) for key in contract):
        raise v1.Step98ContractError("replay contract mismatch")
    if canonical.get("result_digest") != payload.get("result_digest") or canonical != dict(payload):
        raise v1.Step98ContractError("non_deterministic_result")
    return "deterministic_replay"


def _validate_capture_contract(preregistration: Any, plan: Any, receipt: Mapping[str, Any]) -> None:
    if receipt.get("experiment_id") != EXPERIMENT_ID:
        raise v1.Step98ContractError("capture experiment id mismatch")
    if receipt.get("manifest_digest") != preregistration.manifest_digest:
        raise v1.Step98ContractError("capture manifest digest mismatch")
    if receipt.get("request_plan_digest") != plan.request_plan_digest:
        raise v1.Step98ContractError("capture request-plan digest mismatch")
    implementation = preregistration.manifest["implementation"]
    if receipt.get("capture_source_digest") != implementation["capture_source_digest"]:
        raise v1.Step98ContractError("capture source digest mismatch")
    expected_implementation_digest = implementation_bundle_digest(preregistration.manifest)
    if receipt.get("implementation_source_digest") != expected_implementation_digest:
        raise v1.Step98ContractError("capture implementation digest mismatch")
    if (
        receipt.get("requested_provider") != plan.provider
        or receipt.get("requested_model_alias") != plan.model
        or receipt.get("dimensions") != plan.dimensions
    ):
        raise v1.Step98ContractError("capture embedding identity mismatch")
    capture = preregistration.manifest["capture"]
    if int(receipt.get("actual_external_attempt_count", 0)) > int(capture["max_external_attempts"]):
        raise v1.Step98ContractError("capture external-attempt budget exceeded")
    execution = receipt.get("logical_execution_order")
    if not isinstance(execution, list) or len(execution) != len(plan.requests):
        raise v1.Step98ContractError("capture execution order incomplete")
    expected = [
        {
            "logical_request_ordinal": request.ordinal,
            "role": request.role,
            "variant": request.variant_id,
            "batch_ordinal": request.batch_ordinal,
            "input_count": len(request.item_ids),
        }
        for request in plan.requests
    ]
    actual = [
        {key: item.get(key) for key in expected_item}
        for item, expected_item in zip(execution, expected)
    ]
    if actual != expected:
        raise v1.Step98ContractError("capture execution order mismatch")
    attempt_total = sum(int(item.get("attempt_count", 0)) for item in execution)
    if attempt_total != int(receipt.get("actual_external_attempt_count", -1)):
        raise v1.Step98ContractError("capture external-attempt count mismatch")
    retry_total = sum(max(0, int(item.get("attempt_count", 0)) - 1) for item in execution)
    if retry_total != int(receipt.get("retry_count", -1)):
        raise v1.Step98ContractError("capture retry count mismatch")
    identities = {
        (item.get("provider_reported_provider"), item.get("provider_reported_model"), item.get("provider_reported_dimensions"))
        for item in execution
    }
    expected_identity = {
        (plan.provider, plan.model, plan.dimensions)
    }
    if identities != expected_identity:
        raise v1.Step98ContractError("provider-reported identity mismatch")


def _load_pgvector_evidence(
    preregistration: Any,
    evidence_path: Optional[Path],
) -> bool:
    if evidence_path is None:
        return False
    expected = _REPO_ROOT / preregistration.manifest["artifacts"]["pgvector_evidence_path"]
    if evidence_path.resolve() != expected.resolve():
        raise v1.Step98ContractError("pgvector evidence path mismatch")
    evidence = _load_json_mapping(evidence_path)
    digest = evidence.get("receipt_digest")
    body = dict(evidence)
    body.pop("receipt_digest", None)
    if digest != v1.canonical_digest(body):
        raise v1.Step98ContractError("pgvector evidence digest mismatch")
    if (
        evidence.get("experiment_id") != EXPERIMENT_ID
        or evidence.get("manifest_digest") != preregistration.manifest_digest
        or evidence.get("adapter") != "postgresql_pgvector"
        or evidence.get("status") != "passed"
    ):
        raise v1.Step98ContractError("pgvector evidence contract mismatch")
    contract = preregistration.manifest["pgvector_gate_contract"]
    if (
        evidence.get("gate_version") != contract["version"]
        or evidence.get("gate_source_digest") != contract["gate_source_digest"]
        or evidence.get("repository_test_source_digest")
        != contract["repository_test_source_digest"]
        or evidence.get("target_class") != contract["target_class"]
        or evidence.get("disposable_database_prefix")
        != contract["disposable_database_prefix"]
        or evidence.get("production_database_name_was_distinct") is not True
        or evidence.get("production_database_used") is not False
        or evidence.get("disposable_database_created") is not True
        or evidence.get("filter_before_top_k_passed") is not True
        or evidence.get("cleanup_status") != "passed"
        or int(evidence.get("case_count", 0)) != int(contract["case_count"])
    ):
        raise v1.Step98ContractError("pgvector evidence provenance mismatch")
    return True


def _load_json_mapping(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise v1.Step98ContractError(f"invalid JSON artifact: {path.name}") from None
    if not isinstance(value, dict):
        raise v1.Step98ContractError(f"invalid JSON artifact: {path.name}")
    return value


def _write_json_atomic_create_only(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.pending")
    try:
        with temporary.open("x", encoding="utf-8") as result_file:
            json.dump(payload, result_file, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            result_file.write("\n")
            result_file.flush()
            os.fsync(result_file.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="No-network Step 98 exp-002 Phase C evaluator")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--pgvector-evidence", type=Path)
    args = parser.parse_args()
    try:
        preregistration = load_preregistration(args.fixture_dir)
        expected_result = _REPO_ROOT / preregistration.manifest["artifacts"]["result_path"]
        if args.result.resolve() != expected_result.resolve():
            raise v1.Step98ContractError("canonical result path mismatch")
        payload = evaluate_capture(
            fixture_dir=args.fixture_dir,
            capture_dir=args.capture_dir,
            pgvector_evidence_path=args.pgvector_evidence,
        )
        replay_status = write_or_replay_result(args.result, payload)
    except v1.Step98ContractError as exc:
        print(json.dumps({"status": "inconclusive", "safe_failure_category": str(exc)}, sort_keys=True))
        raise SystemExit(2)
    print(json.dumps({"status": payload["status"], "selected_variant": payload["selected_variant"], "result_digest": payload["result_digest"], "replay_status": replay_status}, sort_keys=True))


if __name__ == "__main__":
    main()
