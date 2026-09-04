from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from .context_aware_embedding_input_eval import Step98ContractError, VARIANTS, canonical_digest, file_digest
from .step98_citation_eval import evaluate_citation_gates, evaluate_step98_decision_citations
from .step98_pgvector_gate import build_passed_evidence, validate_disposable_target
from .step98_repository_safety_eval import evaluate_production_repository_safety
from . import step98_phase_c
from .step98_phase_c import validate_capture_bundle, write_or_replay_result


def test_phase_c_rejects_failed_or_incomplete_capture(tmp_path: Path) -> None:
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    (capture_dir / "receipt.json").write_text(
        json.dumps({"status": "failed", "receipt_digest": "unused"}),
        encoding="utf-8",
    )

    with pytest.raises(Step98ContractError, match="successful complete capture required"):
        validate_capture_bundle(capture_dir)


def test_phase_c_result_is_create_once_and_same_digest_replays(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    payload = {
        "experiment_id": "step98-exp-002",
        "manifest_digest": "manifest",
        "capture_run_digest": "capture",
        "scoring_version": "step98_scoring_v1",
        "implementation_source_digest": "source",
        "status": "inconclusive",
        "result_digest": "result",
    }

    first = write_or_replay_result(result_path, payload)
    second = write_or_replay_result(result_path, payload)

    assert first == "created"
    assert second == "deterministic_replay"
    changed = dict(payload, result_digest="different")
    with pytest.raises(Step98ContractError, match="non_deterministic_result"):
        write_or_replay_result(result_path, changed)
    assert json.loads(result_path.read_text(encoding="utf-8")) == payload


def test_phase_c_validates_complete_capture_digest_chain(tmp_path: Path) -> None:
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    query_vectors = {"q": [1.0]}
    document_vectors = {variant: {"chunk": [1.0]} for variant in VARIANTS}
    query_digest = canonical_digest(query_vectors)
    document_digests = {
        variant: canonical_digest(document_vectors[variant]) for variant in VARIANTS
    }
    capture_core = {
        "experiment_id": "step98-exp-002",
        "capture_run_id": "step98-exp-002-capture-001",
        "manifest_digest": "manifest",
        "request_plan_digest": "plan",
        "capture_source_digest": "capture-source",
        "implementation_source_digest": "implementation-source",
        "requested_provider": "openai",
        "requested_model_alias": "text-embedding-3-small",
        "dimensions": 1,
        "provider_revision_id": None,
        "approval_id": "approval",
        "budget_id": "budget",
        "query_vector_set_digest": query_digest,
        "document_vector_set_digests": document_digests,
        "logical_execution_order": [],
        "completed_logical_request_count": 0,
        "actual_external_attempt_count": 0,
        "retry_count": 0,
        "provider_token_usage": 0,
        "bounded_cost_estimate_usd": 0.0,
    }
    capture_digest = canonical_digest(capture_core)
    vectors = {
        "metadata": {
            "experiment_id": "step98-exp-002",
            "capture_run_digest": capture_digest,
            "query_vector_set_digest": query_digest,
            "document_vector_set_digests": document_digests,
        },
        "query_vectors": query_vectors,
        "document_vectors": document_vectors,
    }
    vectors_path = capture_dir / "vectors.json"
    vectors_path.write_text(
        json.dumps(vectors, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    receipt = dict(
        capture_core,
        status="captured",
        capture_run_digest=capture_digest,
        vectors_file_digest=file_digest(vectors_path),
        vectors_artifact_created=True,
    )
    receipt["receipt_digest"] = canonical_digest(receipt)
    (capture_dir / "receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    loaded_receipt, loaded_vectors = validate_capture_bundle(capture_dir)

    assert loaded_receipt["capture_run_digest"] == capture_digest
    assert loaded_vectors["query_vectors"] == query_vectors


def test_phase_c_runs_deterministic_production_repository_safety_seam() -> None:
    assert evaluate_production_repository_safety() is True


def test_phase_c_runs_frozen_projection_and_existing_golden_citation_seams() -> None:
    evidence = evaluate_citation_gates()

    assert evidence.projection_conformance_passed is True
    assert evidence.citation_recall == 1.0
    assert evidence.citation_precision == 1.0
    assert evidence.invalid_citation_count == 0
    assert evidence.derived_header_citation_count == 0
    assert evidence.golden_citation_recall == 1.0
    assert evidence.golden_citation_precision == 1.0
    assert evidence.golden_invalid_citation_count == 0


def test_decision_citation_evidence_counts_disallowed_top_five_path() -> None:
    preregistration = SimpleNamespace(
        chunks=[
            {"chunk_id": "relevant", "notion_path": "Public/Right"},
            {"chunk_id": "decoy", "notion_path": "Public/Wrong"},
        ],
        queries=[
            {
                "query_id": "q-1",
                "required_citation_paths": ["Public/Right"],
                "allowed_citation_paths": ["Public/Right"],
            }
        ],
    )

    evidence = evaluate_step98_decision_citations(
        preregistration=preregistration,
        rankings_by_variant={"title_body_v1": {"q-1": ["relevant", "decoy"]}},
    )["title_body_v1"]

    assert evidence.recall == 1.0
    assert evidence.precision == 0.5
    assert evidence.invalid_citation_count == 1
    assert evidence.derived_header_citation_count == 0


def test_pgvector_evidence_binds_frozen_gate_and_disposable_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate_source = "gate-source"
    repository_source = "repository-source"
    manifest = {
        "experiment_id": "step98-exp-002",
        "artifacts": {"pgvector_evidence_path": "pgvector.json"},
        "pgvector_gate_contract": {
            "version": "step98_pgvector_adapter_gate_v1",
            "gate_source_digest": gate_source,
            "repository_test_source_digest": repository_source,
            "target_class": "disposable_non_production_postgresql",
            "disposable_database_prefix": "learnloop_step98_",
            "production_database_name_must_be_distinct": True,
            "case_count": 3,
        },
        "implementation": {"pgvector_gate_source_digest": gate_source},
    }
    evidence = build_passed_evidence(manifest)
    evidence_path = tmp_path / "pgvector.json"
    evidence_path.write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(step98_phase_c, "_REPO_ROOT", tmp_path)

    assert step98_phase_c._load_pgvector_evidence(
        SimpleNamespace(manifest=manifest, manifest_digest=canonical_digest(manifest)),
        evidence_path,
    ) is True


def test_pgvector_target_validation_rejects_disposable_namespace_as_production() -> None:
    with pytest.raises(Step98ContractError, match="production target uses disposable"):
        validate_disposable_target(
            {
                "DATABASE_URL": "postgresql+psycopg://user:secret@localhost/learnloop_step98_bad",
                "LEARNLOOP_PGVECTOR_ADMIN_DATABASE_URL": "postgresql+psycopg://user:secret@localhost/postgres",
            }
        )


def test_pgvector_target_validation_accepts_distinct_names_without_disclosing_them() -> None:
    validate_disposable_target(
        {
            "DATABASE_URL": "postgresql+psycopg://user:secret@localhost/learnloop",
            "LEARNLOOP_PGVECTOR_ADMIN_DATABASE_URL": "postgresql+psycopg://user:secret@localhost/postgres",
        }
    )
