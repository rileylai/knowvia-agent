from __future__ import annotations

from pathlib import Path

import pytest

from scripts.generate_step98_exp002_preregistration import EXP001_DIR, generate

from .context_aware_embedding_input_eval import IndependentGateEvidence, Step98ContractError, VARIANTS, file_digest
from .step98_experiment_v2 import load_preregistration, plan_capture, score_rankings, validate_public_safe_sources


EXP001_IMMUTABLE_DIGESTS = {
    "manifest.yaml": "6ff090fd9f7691974ac10289533af320d74723d6b86cef888d22e11c99735892",
    "manifest.sha256": "024df6b367ca08b0c974a038afdefdbc6f5e9a55530aae20c337745b11fb2f78",
    "source_records.yaml": "db0bdfa6d25218835ebcfafde2af5c26fcccfd548b557f4a563b8d7c4b51ee35",
    "chunks.yaml": "39882073cf568cf08e788ee4c3be7a400c8acab31b00389d1eeecb9bafc0c607",
    "queries.yaml": "acc2341bfa084ba3301c9e91f37fa99f9b6080e80986ab9a7fd4ee73fe9f8565",
}


def test_exp001_evidence_remains_byte_for_byte_immutable() -> None:
    assert {
        filename: file_digest(EXP001_DIR / filename)
        for filename in EXP001_IMMUTABLE_DIGESTS
    } == EXP001_IMMUTABLE_DIGESTS


def test_exp002_generator_reuses_decision_set_and_freezes_new_receipt(tmp_path: Path) -> None:
    output = tmp_path / "step98-exp-002"
    generated_digest = generate(output)

    assert not (output / "manifest.sha256").exists()
    preregistration = load_preregistration(output, create_receipt=True)
    plan = plan_capture(preregistration)
    assert preregistration.manifest_digest == generated_digest
    assert preregistration.manifest["experiment_id"] == "step98-exp-002"
    assert preregistration.manifest["fixture_class"] == "public_safe"
    assert len(preregistration.sources) == 18
    assert len(preregistration.chunks) == 108
    assert len(preregistration.queries) == 72
    assert len(plan.requests) == 15
    assert plan.request_plan_digest == preregistration.manifest["capture"]["request_plan_digest"]
    for filename in ("source_records.yaml", "chunks.yaml", "queries.yaml"):
        assert (output / filename).read_bytes() == (EXP001_DIR / filename).read_bytes()


def test_public_safe_validation_rejects_unapproved_source_shape() -> None:
    with pytest.raises(Step98ContractError, match="public-safe source schema mismatch"):
        validate_public_safe_sources(
            [
                {
                    "page_id": "page-public",
                    "title_source_id": "title-page-public",
                    "title": "Public",
                    "notion_path": "Synthetic/Public",
                    "secret": "not-allowed",
                }
            ]
        )


def test_quality_failure_is_no_adoption_without_pgvector_evidence(tmp_path: Path) -> None:
    output = tmp_path / "step98-exp-002"
    generate(output)
    preregistration = load_preregistration(output, create_receipt=True)
    chunk_ids = [chunk["chunk_id"] for chunk in preregistration.chunks]
    rankings = {}
    for variant in VARIANTS:
        rankings[variant] = {}
        for query in preregistration.queries:
            target = next(chunk_id for chunk_id, grade in query["relevance"].items() if grade == 2)
            prefix = [chunk_id for chunk_id in chunk_ids if chunk_id != target][:3] + [target]
            rankings[variant][query["query_id"]] = prefix + [
                chunk_id for chunk_id in chunk_ids if chunk_id not in prefix
            ]
    result = score_rankings(
        preregistration,
        capture_digest="capture",
        rankings_by_variant=rankings,
        implementation_source_digest="source",
        independent_evidence=IndependentGateEvidence(
            citation_recall=1.0,
            citation_precision=1.0,
            invalid_citation_count=0,
            derived_header_citation_count=0,
            golden_citation_recall=1.0,
            golden_citation_precision=1.0,
            golden_invalid_citation_count=0,
            production_repository_safety_passed=True,
            pgvector_adapter_integration_passed=False,
        ),
    )

    assert result.gate.status == "no_adoption"
