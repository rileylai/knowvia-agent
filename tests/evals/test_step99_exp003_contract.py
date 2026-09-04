from __future__ import annotations

import json

import pytest

from tests.evals import step99_hybrid_eval as core
from tests.evals import step99_hybrid_eval_v3 as exp003


_LOCAL_EVIDENCE_PATHS = (
    core._REPO_ROOT / "dev_state/specs/step-99-hybrid-retrieval-evaluation.md",
    core._REPO_ROOT
    / "dev_state/artifacts/step_98/step98-exp-002-capture-001/receipt.json",
    core._REPO_ROOT
    / "dev_state/artifacts/step_98/step98-exp-002-capture-001/vectors.json",
    core._REPO_ROOT
    / "dev_state/artifacts/step_99/step99-exp-003-pgvector-evidence.json",
    core._REPO_ROOT
    / "dev_state/artifacts/step_99/step99-exp-003-result.json",
)

pytestmark = pytest.mark.skipif(
    not all(path.is_file() for path in _LOCAL_EVIDENCE_PATHS),
    reason="complete ignored Step 99 local evidence is unavailable",
)


def test_exp003_source_contract_is_complete_and_body_only() -> None:
    exp003._activate_contract()
    manifest = core.load_contract(exp003.DEFAULT_FIXTURE_DIR)
    sources, chunks, queries = core.load_source_dataset(manifest)
    query_vectors, body_vectors = exp003.load_source_vectors_v3(
        manifest,
        chunks=chunks,
        queries=queries,
    )

    assert manifest["experiment_id"] == "step99-exp-003"
    assert len(sources) == 18
    assert len(chunks) == 108
    assert len(queries) == 72
    assert len(query_vectors) == 72
    assert len(body_vectors) == 108
    assert {len(vector) for vector in query_vectors.values()} == {1536}
    assert {len(vector) for vector in body_vectors.values()} == {1536}


def test_exp003_formal_result_replays_from_frozen_evidence() -> None:
    exp003._activate_contract()
    manifest = core.load_contract(exp003.DEFAULT_FIXTURE_DIR)
    pgvector_evidence = core._REPO_ROOT / manifest["artifacts"]["pgvector_evidence_path"]
    result_path = core._REPO_ROOT / manifest["artifacts"]["result_path"]

    replayed = core.evaluate_experiment(
        fixture_dir=exp003.DEFAULT_FIXTURE_DIR,
        pgvector_evidence_path=pgvector_evidence,
    )
    recorded = json.loads(result_path.read_text(encoding="utf-8"))

    assert replayed == recorded
    assert replayed["decision"] == {
        "reasons": ["OVERALL_MRR_GAIN"],
        "status": "maintain_vector_primary",
    }
    assert replayed["result_digest"] == (
        "6a08aab34455d21e0c361e1a6007a4f58047ae9f803c3f7a4e555595007f7bdf"
    )
