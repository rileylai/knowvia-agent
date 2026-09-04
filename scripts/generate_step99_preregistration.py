from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.evals.step99_hybrid_eval import canonical_digest, file_digest


EXPERIMENT_ID = "step99-exp-001"
SOURCE_FIXTURE = _REPO_ROOT / "tests/evals/fixtures/step_98/step98-exp-002"
SOURCE_CAPTURE = _REPO_ROOT / "dev_state/artifacts/step_98/step98-exp-002-capture-001"
OUTPUT_DIR = _REPO_ROOT / "tests/evals/fixtures/step_99" / EXPERIMENT_ID


def build_manifest() -> dict[str, Any]:
    sources = _load_yaml_list(SOURCE_FIXTURE / "source_records.yaml", "sources")
    chunks = _load_yaml_list(SOURCE_FIXTURE / "chunks.yaml", "chunks")
    queries = _load_yaml_list(SOURCE_FIXTURE / "queries.yaml", "queries")
    receipt = _load_json(SOURCE_CAPTURE / "receipt.json")
    source_manifest_digest = (SOURCE_FIXTURE / "manifest.sha256").read_text(encoding="utf-8").strip()
    tuning = [item["query_id"] for item in queries if _ordinal(item["query_id"]) in (1, 2)]
    decision = [item["query_id"] for item in queries if _ordinal(item["query_id"]) not in (1, 2)]
    primary_cells: dict[str, dict[str, list[str]]] = {}
    critical: dict[str, list[str]] = {}
    secondary: dict[str, list[str]] = {}
    for query in queries:
        query_id = query["query_id"]
        primary_cells.setdefault(query["primary_cell"], {"tuning": [], "decision": []})[
            "tuning" if query_id in tuning else "decision"
        ].append(query_id)
        for cohort in query["critical_cohorts"]:
            critical.setdefault(cohort, []).append(query_id)
        for tag in [*query["secondary_tags"], query["length_bucket"]]:
            secondary.setdefault(tag, []).append(query_id)
    corpus_identity = [
        {
            "chunk_id": item["chunk_id"],
            "page_id": item["page_id"],
            "chunk_index": item["chunk_index"],
            "record_digest": item["record_digest"],
            "source_kind": item["source_kind"],
            "notion_path": item["notion_path"],
        }
        for item in chunks
    ]
    qrels_identity = [
        {
            "query_id": item["query_id"],
            "query": item["query"],
            "relevance": item["relevance"],
            "primary_cell": item["primary_cell"],
            "critical_cohorts": item["critical_cohorts"],
            "secondary_tags": item["secondary_tags"],
            "length_bucket": item["length_bucket"],
            "required_citation_paths": item["required_citation_paths"],
            "allowed_citation_paths": item["allowed_citation_paths"],
        }
        for item in queries
    ]
    managed_paths = (
        "dev_state/specs/step-99-hybrid-retrieval-evaluation.md",
        "scripts/generate_step99_preregistration.py",
        "tests/evals/step99_hybrid_eval.py",
        "tests/evals/step99_pgvector_gate.py",
        "tests/evals/test_step99_hybrid_eval.py",
        "tests/evals/step98_citation_eval.py",
        "tests/evals/step98_repository_safety_eval.py",
        "tests/evals/citation_accuracy_eval.py",
        "tests/evals/golden_questions.py",
        "tests/evals/golden_questions.yaml",
        "src/rag/retriever.py",
        "src/repositories/chunk_repository.py",
        "src/policies/synthetic_data.py",
        "src/db/base.py",
        "src/db/models.py",
        "tests/evals/fixtures/step_98/step98-exp-002/source_records.yaml",
        "tests/evals/fixtures/step_98/step98-exp-002/chunks.yaml",
        "tests/evals/fixtures/step_98/step98-exp-002/queries.yaml",
        "tests/evals/fixtures/step_98/step98-exp-002/manifest.sha256",
    )
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "schema_version": "step99_manifest_v1",
        "fixture_class": "public_safe",
        "dataset": {
            "source_experiment_id": "step98-exp-002",
            "source_manifest_digest": source_manifest_digest,
            "source_records_path": "tests/evals/fixtures/step_98/step98-exp-002/source_records.yaml",
            "chunks_path": "tests/evals/fixtures/step_98/step98-exp-002/chunks.yaml",
            "queries_path": "tests/evals/fixtures/step_98/step98-exp-002/queries.yaml",
            "page_count": len(sources),
            "chunk_count": len(chunks),
            "query_count": len(queries),
            "corpus_fingerprint": canonical_digest(corpus_identity),
            "qrels_fingerprint": canonical_digest(qrels_identity),
            "dataset_fingerprint": canonical_digest({"corpus": corpus_identity, "qrels": qrels_identity}),
            "split_version": "primary_cell_ordinals_01_02_tuning_v1",
            "tuning_query_ids": tuning,
            "decision_query_ids": decision,
            "primary_cell_splits": primary_cells,
            "critical_cohorts": critical,
            "secondary_cohorts": secondary,
        },
        "vector_provenance": {
            "receipt_path": "dev_state/artifacts/step_98/step98-exp-002-capture-001/receipt.json",
            "vectors_path": "dev_state/artifacts/step_98/step98-exp-002-capture-001/vectors.json",
            "source_manifest_digest": source_manifest_digest,
            "capture_run_digest": receipt["capture_run_digest"],
            "vectors_file_sha256": file_digest(SOURCE_CAPTURE / "vectors.json"),
            "query_vector_set_digest": receipt["query_vector_set_digest"],
            "body_vector_set_digest": receipt["document_vector_set_digests"]["body_only_v1"],
            "provider": receipt["requested_provider"],
            "model": receipt["requested_model_alias"],
            "dimensions": receipt["dimensions"],
            "query_count": 72,
            "body_document_count": 108,
            "contextual_vector_sets_forbidden": True,
        },
        "variants": ["vector_only", "keyword_only", "weighted_rrf"],
        "vector_only": {
            "algorithm": "exact_cosine_body_only_v1",
            "tie_break": "similarity_desc_chunk_id_asc",
        },
        "keyword_only": {
            "algorithm": "production_lexical_replica_v1",
            "token_pattern": "[a-z0-9]+",
            "coverage_weight": "0.75",
            "density_weight": "0.25",
            "phrase_bonus": "0.15",
            "zero_score": "omit",
            "tie_break": "score_desc_chunk_id_asc",
        },
        "weighted_rrf": {
            "formula": "wv/(60+rank_vector)+wk/(60+rank_keyword)",
            "constant": 60,
            "candidate_depth": 20,
            "missing_result": "zero_contribution",
            "tie_break": "score_desc_vector_rank_keyword_rank_chunk_id",
            "weight_candidates": [
                {"id": "v050_k050", "vector": "0.50", "keyword": "0.50"},
                {"id": "v065_k035", "vector": "0.65", "keyword": "0.35"},
                {"id": "v080_k020", "vector": "0.80", "keyword": "0.20"},
            ],
            "tuning_selection": "ndcg5_mrr5_hit3_hit1_then_higher_vector_weight_v1",
        },
        "retrieval": {
            "quality_scope": "all_108_notion_chunks_no_page_or_section_restriction",
            "evaluation_top_k": [1, 3, 5],
            "citation_top_k": 5,
            "same_scope_all_variants": True,
        },
        "scoring": {"version": "step99_scoring_v1", "precision": 12},
        "thresholds": {
            "overall_hit3_gains": 3,
            "overall_hit3_losses": 0,
            "overall_reciprocal_rank_gain": "2.700",
        },
        "citation_gate": {
            "relative_to_vector": "no_recall_precision_or_invalid_count_regression",
            "derived_or_unsupported_header_count": 0,
            "independent_recall_precision": "1.000",
            "independent_invalid_count": 0,
        },
        "safety_gate": {
            "production_repository_required": True,
            "pgvector_required": True,
            "database_prefix": "learnloop_step99_",
            "expected_eligible_sets_nonempty": True,
            "cleanup_required": True,
        },
        "replay": {
            "version": "canonical_semantic_json_v1",
            "compare_result_digest": True,
            "compare_semantic_payload": True,
            "create_only": True,
        },
        "decision_taxonomy": [
            "hybrid_candidate_for_step100",
            "maintain_vector_primary",
            "inconclusive",
        ],
        "artifacts": {
            "result_path": "dev_state/artifacts/step_99/step99-exp-001-result.json",
            "pgvector_evidence_path": "dev_state/artifacts/step_99/step99-exp-001-pgvector-evidence.json",
        },
        "managed_sources": [
            {"path": path, "sha256": file_digest(_REPO_ROOT / path)} for path in managed_paths
        ],
    }
    return manifest


def _ordinal(query_id: str) -> int:
    return int(query_id.rsplit("-", 1)[1])


def _load_yaml_list(path: Path, key: str) -> list[dict[str, Any]]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))[key]
    if not isinstance(value, list):
        raise RuntimeError(f"invalid fixture: {path.name}")
    return value


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid artifact: {path.name}")
    return value


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / "manifest.yaml"
    if output.exists() or (OUTPUT_DIR / "manifest.sha256").exists():
        raise SystemExit("Step 99 preregistration already exists")
    with output.open("x", encoding="utf-8") as manifest_file:
        yaml.safe_dump(
            build_manifest(),
            manifest_file,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        )
    print(json.dumps({"status": "generated", "experiment_id": EXPERIMENT_ID}, sort_keys=True))


if __name__ == "__main__":
    main()
