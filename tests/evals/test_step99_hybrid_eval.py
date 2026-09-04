from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.step99_hybrid_eval import (
    Step99ContractError,
    canonical_digest,
    canonicalize,
    keyword_ranking,
    weighted_rrf_ranking,
    write_or_replay,
)


def test_canonicalize_normalizes_tuple_list_mapping_and_set() -> None:
    left = {"b": (2, 3), "a": {"z", "x"}}
    right = {"a": ["x", "z"], "b": [2, 3]}

    assert canonicalize(left) == canonicalize(right)
    assert canonical_digest(left) == canonical_digest(right)


def test_keyword_ranking_matches_frozen_production_formula() -> None:
    chunks = [
        {"chunk_id": "c-2", "chunk_text": "alpha beta gamma"},
        {"chunk_id": "c-1", "chunk_text": "alpha beta"},
        {"chunk_id": "c-3", "chunk_text": "unrelated"},
    ]

    assert keyword_ranking("alpha beta", chunks) == ["c-1", "c-2"]


def test_weighted_rrf_deduplicates_and_uses_frozen_tie_break() -> None:
    ranking = weighted_rrf_ranking(
        ["shared", "vector-only"],
        ["shared", "keyword-only"],
        vector_weight=0.5,
        keyword_weight=0.5,
        constant=60,
        depth=20,
    )

    assert ranking == ["shared", "vector-only", "keyword-only"]
    assert ranking.count("shared") == 1


def test_replay_compares_canonical_semantics(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    payload = {"values": (1, 2), "result_digest": canonical_digest({"value": 1})}

    assert write_or_replay(path, payload) == "created"
    assert write_or_replay(path, {"values": [1, 2], "result_digest": payload["result_digest"]}) == "deterministic_replay"


def test_replay_refuses_semantic_difference_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    original = {"value": 1, "result_digest": "same"}
    write_or_replay(path, original)

    with pytest.raises(Step99ContractError, match="non_deterministic_semantic_payload"):
        write_or_replay(path, {"value": 2, "result_digest": "same"})

    assert json.loads(path.read_text(encoding="utf-8")) == original
