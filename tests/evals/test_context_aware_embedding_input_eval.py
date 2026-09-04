from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from .context_aware_embedding_input_eval import (
    BODY_ONLY_VERSION,
    CaptureArtifact,
    DEFAULT_FIXTURE_DIR,
    IndependentGateEvidence,
    TITLE_BODY_VERSION,
    TITLE_HEADING_BODY_VERSION,
    Step98ContractError,
    load_preregistration,
    plan_capture,
    project_citations,
    score_rankings,
    canonical_digest,
    validate_capture_artifact,
    validate_replay,
)


def test_canonical_fixture_has_exact_frozen_denominators() -> None:
    preregistration = load_preregistration()

    assert len(preregistration.sources) == 18
    assert len(preregistration.chunks) == 108
    assert len(preregistration.queries) == 72
    memberships = preregistration.manifest["memberships"]
    assert {len(ids) for ids in memberships["primary_cells"].values()} == {8}
    assert [
        len(memberships["critical_cohorts"][name])
        for name in (
            "title_only_semantic",
            "body_only",
            "traditional_chinese",
            "english",
            "mixed_language",
            "ambiguous",
        )
    ] == [18, 24, 24, 24, 24, 24]
    assert [
        len(memberships["secondary_cohorts"][name])
        for name in ("short", "standard_length", "long")
    ] == [18, 36, 18]
    assert sum(len(query["hard_negative_chunk_ids"]) for query in preregistration.queries) == 144


def test_phase_a_receipt_is_create_once_and_rejects_mismatch(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(DEFAULT_FIXTURE_DIR, fixture_dir)
    receipt = fixture_dir / "manifest.sha256"
    receipt.unlink(missing_ok=True)

    first = load_preregistration(fixture_dir, create_receipt=True)
    second = load_preregistration(fixture_dir, create_receipt=True)
    assert first.manifest_digest == second.manifest_digest
    assert receipt.read_text(encoding="utf-8").strip() == first.manifest_digest

    receipt.write_text("wrong-digest\n", encoding="utf-8")
    with pytest.raises(Step98ContractError, match="receipt mismatch"):
        load_preregistration(fixture_dir, create_receipt=True)


def test_phase_b_plan_uses_one_query_pass_then_document_round_robin(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(DEFAULT_FIXTURE_DIR, fixture_dir)
    (fixture_dir / "manifest.sha256").unlink(missing_ok=True)
    preregistration = load_preregistration(fixture_dir, create_receipt=True)

    plan = plan_capture(preregistration)

    query_requests = [request for request in plan.requests if request.role == "query"]
    assert sum(len(request.item_ids) for request in query_requests) == 72
    document_requests = [request for request in plan.requests if request.role == "document"]
    assert sum(len(request.item_ids) for request in document_requests) == 324
    assert [request.variant_id for request in document_requests[:3]] == [
        BODY_ONLY_VERSION,
        TITLE_BODY_VERSION,
        TITLE_HEADING_BODY_VERSION,
    ]
    assert plan.dimensions == 1536


def test_phase_b_capture_artifact_validates_all_contract_digests(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(DEFAULT_FIXTURE_DIR, fixture_dir)
    (fixture_dir / "manifest.sha256").unlink(missing_ok=True)
    preregistration = load_preregistration(fixture_dir, create_receipt=True)
    plan = plan_capture(preregistration)
    payload = {
        "experiment_id": preregistration.manifest["experiment_id"],
        "manifest_digest": preregistration.manifest_digest,
        "capture_run_id": "capture-1",
        "request_plan_digest": plan.request_plan_digest,
        "query_vector_set_digest": "query-vectors",
        "document_vector_set_digests": {
            BODY_ONLY_VERSION: "body-vectors",
            TITLE_BODY_VERSION: "title-vectors",
            TITLE_HEADING_BODY_VERSION: "heading-vectors",
        },
        "provider": plan.provider,
        "model": plan.model,
        "dimensions": plan.dimensions,
        "provider_revision_id": None,
        "batch_count": len(plan.requests),
        "retry_count": 0,
        "token_input": 1000,
        "estimated_cost_usd": 0.00002,
        "duration_seconds": 1.0,
        "vectors_retained": True,
    }
    artifact = CaptureArtifact(capture_run_digest=canonical_digest(payload), **payload)

    validate_capture_artifact(preregistration, plan, artifact)


def test_phase_c_scores_gate_and_validates_deterministic_replay(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(DEFAULT_FIXTURE_DIR, fixture_dir)
    (fixture_dir / "manifest.sha256").unlink(missing_ok=True)
    preregistration = load_preregistration(fixture_dir, create_receipt=True)
    baseline = _rankings(preregistration, improved=False)
    improved = _rankings(preregistration, improved=True)
    rankings = {
        BODY_ONLY_VERSION: baseline,
        TITLE_BODY_VERSION: improved,
        TITLE_HEADING_BODY_VERSION: improved,
    }

    canonical = score_rankings(
        preregistration,
        capture_digest="capture-digest",
        rankings_by_variant=rankings,
        implementation_source_digest="scoring-source-digest",
        independent_evidence=_passing_evidence(),
    )
    replay = score_rankings(
        preregistration,
        capture_digest="capture-digest",
        rankings_by_variant=rankings,
        implementation_source_digest="scoring-source-digest",
        independent_evidence=_passing_evidence(),
    )

    assert canonical.gate.status == "pass_candidate_identified"
    assert canonical.gate.selected_variant == TITLE_BODY_VERSION
    assert canonical.variant_scores[BODY_ONLY_VERSION].ambiguity_errors
    assert canonical.variant_scores[TITLE_BODY_VERSION].ambiguity_errors == ()
    validate_replay(canonical, replay)


def test_replay_rejects_non_deterministic_result(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(DEFAULT_FIXTURE_DIR, fixture_dir)
    (fixture_dir / "manifest.sha256").unlink(missing_ok=True)
    preregistration = load_preregistration(fixture_dir, create_receipt=True)
    rankings = {
        BODY_ONLY_VERSION: _rankings(preregistration, improved=False),
        TITLE_BODY_VERSION: _rankings(preregistration, improved=True),
        TITLE_HEADING_BODY_VERSION: _rankings(preregistration, improved=True),
    }
    canonical = score_rankings(
        preregistration,
        capture_digest="capture-digest",
        rankings_by_variant=rankings,
        implementation_source_digest="scoring-source-digest",
        independent_evidence=_passing_evidence(),
    )
    changed_rankings = dict(rankings)
    changed = dict(changed_rankings[TITLE_BODY_VERSION])
    query_id = preregistration.queries[0]["query_id"]
    changed[query_id] = list(reversed(changed[query_id]))
    changed_rankings[TITLE_BODY_VERSION] = changed
    replay = score_rankings(
        preregistration,
        capture_digest="capture-digest",
        rankings_by_variant=changed_rankings,
        implementation_source_digest="scoring-source-digest",
        independent_evidence=_passing_evidence(),
    )

    with pytest.raises(Step98ContractError, match="non_deterministic_result"):
        validate_replay(canonical, replay)


@pytest.mark.parametrize(
    ("retrieved", "required", "allowed", "expected_invalid"),
    [
        (["a", "a"], ["Path/A"], ["Path/A"], 0),
        (["a", "b"], ["Path/A"], ["Path/A", "Path/B"], 0),
        (["a", "missing"], ["Path/A"], ["Path/A"], 1),
        (["b", "a"], ["Path/A"], ["Path/A"], 1),
        (["a", "a", "b"], ["Path/A"], ["Path/A", "Path/B"], 0),
        (["a"], ["Path/A"], ["Path/A"], 0),
        (["missing", "a"], ["Path/A"], ["Path/A"], 1),
        (["a", "b", "missing"], ["Path/A"], ["Path/A", "Path/B"], 1),
    ],
)
def test_independent_citation_projection_contract(
    retrieved: list[str],
    required: list[str],
    allowed: list[str],
    expected_invalid: int,
) -> None:
    result = project_citations(
        retrieved_chunk_ids=retrieved,
        chunk_paths={"a": "Path/A", "b": "Path/B"},
        required_paths=required,
        allowed_paths=allowed,
    )

    assert result["recall"] == 1.0
    assert result["invalid_citation_count"] == expected_invalid
    assert result["derived_header_citation_count"] == 0


def _rankings(preregistration, *, improved: bool) -> dict[str, list[str]]:
    rankings: dict[str, list[str]] = {}
    all_chunk_ids = [chunk["chunk_id"] for chunk in preregistration.chunks]
    for query in preregistration.queries:
        target = next(chunk_id for chunk_id, grade in query["relevance"].items() if grade == 2)
        negatives = query["hard_negative_chunk_ids"]
        filler = next(
            chunk_id
            for chunk_id in all_chunk_ids
            if chunk_id not in {target, *negatives}
        )
        if improved:
            prefix = [target, filler, negatives[1], negatives[0]]
        else:
            prefix = [negatives[0], filler, negatives[1], target]
        rankings[query["query_id"]] = prefix + [
            chunk_id for chunk_id in all_chunk_ids if chunk_id not in prefix
        ]
    return rankings


def _passing_evidence() -> IndependentGateEvidence:
    return IndependentGateEvidence(
        citation_recall=1.0,
        citation_precision=1.0,
        invalid_citation_count=0,
        derived_header_citation_count=0,
        golden_citation_recall=1.0,
        golden_citation_precision=1.0,
        golden_invalid_citation_count=0,
        production_repository_safety_passed=True,
        pgvector_adapter_integration_passed=True,
    )
