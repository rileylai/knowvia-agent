from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.evals.step98_citation_eval import evaluate_citation_gates
from tests.evals.step98_repository_safety_eval import (
    evaluate_production_repository_safety,
)


EXPERIMENT_ID = "step99-exp-001"
DEFAULT_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "step_99" / EXPERIMENT_ID
SOURCE_FIXTURE_DIR = (
    Path(__file__).parent / "fixtures" / "step_98" / "step98-exp-002"
)
SOURCE_CAPTURE_DIR = (
    _REPO_ROOT
    / "dev_state"
    / "artifacts"
    / "step_98"
    / "step98-exp-002-capture-001"
)
VARIANTS = ("vector_only", "keyword_only", "weighted_rrf")
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class Step99ContractError(Exception):
    pass


def canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonicalize(item) for item in value), key=_canonical_json)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise Step99ContractError("non-finite result value")
        return round(value, 12)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise Step99ContractError(f"unsupported canonical value: {type(value).__name__}")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(canonicalize(value)).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    *,
    create_receipt: bool = False,
) -> Dict[str, Any]:
    manifest_path = fixture_dir / "manifest.yaml"
    manifest = _load_yaml_mapping(manifest_path)
    if manifest.get("experiment_id") != EXPERIMENT_ID:
        raise Step99ContractError("experiment id mismatch")
    _validate_fixed_contract(manifest)
    for item in manifest["managed_sources"]:
        path = _REPO_ROOT / item["path"]
        if file_digest(path) != item["sha256"]:
            raise Step99ContractError(f"managed source digest mismatch: {item['path']}")
    digest = canonical_digest(manifest)
    receipt_path = fixture_dir / "manifest.sha256"
    if create_receipt:
        try:
            with receipt_path.open("x", encoding="utf-8") as output:
                output.write(f"{digest}\n")
        except FileExistsError:
            pass
    if not receipt_path.exists() or receipt_path.read_text(encoding="utf-8").strip() != digest:
        raise Step99ContractError("valid preregistration receipt required")
    return manifest


def load_source_dataset(manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    dataset = manifest["dataset"]
    sources = _load_yaml_list(_REPO_ROOT / dataset["source_records_path"], "sources")
    chunks = _load_yaml_list(_REPO_ROOT / dataset["chunks_path"], "chunks")
    queries = _load_yaml_list(_REPO_ROOT / dataset["queries_path"], "queries")
    if (len(sources), len(chunks), len(queries)) != (18, 108, 72):
        raise Step99ContractError("dataset counts mismatch")
    if canonical_digest(_corpus_identity(chunks)) != dataset["corpus_fingerprint"]:
        raise Step99ContractError("corpus fingerprint mismatch")
    if canonical_digest(_qrels_identity(queries)) != dataset["qrels_fingerprint"]:
        raise Step99ContractError("qrels fingerprint mismatch")
    tuning = set(dataset["tuning_query_ids"])
    decision = set(dataset["decision_query_ids"])
    all_ids = {item["query_id"] for item in queries}
    if tuning & decision or tuning | decision != all_ids:
        raise Step99ContractError("tuning/decision split mismatch")
    if len(tuning) != 18 or len(decision) != 54:
        raise Step99ContractError("tuning/decision denominators mismatch")
    for cell, ids in dataset["primary_cell_splits"].items():
        if len(ids["tuning"]) != 2 or len(ids["decision"]) != 6:
            raise Step99ContractError(f"primary cell split mismatch: {cell}")
    return sources, chunks, queries


def load_source_vectors(
    manifest: Mapping[str, Any],
    *,
    chunks: Sequence[Mapping[str, Any]],
    queries: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, list[float]], Dict[str, list[float]]]:
    provenance = manifest["vector_provenance"]
    receipt_path = _REPO_ROOT / provenance["receipt_path"]
    vectors_path = _REPO_ROOT / provenance["vectors_path"]
    receipt = _load_json_mapping(receipt_path)
    body = dict(receipt)
    receipt_digest = body.pop("receipt_digest", None)
    if canonical_digest(body) != receipt_digest:
        raise Step99ContractError("source capture receipt digest mismatch")
    required = {
        "status": "captured",
        "capture_run_digest": provenance["capture_run_digest"],
        "manifest_digest": provenance["source_manifest_digest"],
        "requested_model_alias": provenance["model"],
        "dimensions": provenance["dimensions"],
        "query_vector_set_digest": provenance["query_vector_set_digest"],
    }
    if any(receipt.get(key) != value for key, value in required.items()):
        raise Step99ContractError("source capture provenance mismatch")
    if receipt.get("vectors_artifact_created") is not True:
        raise Step99ContractError("complete retained source vectors required")
    if receipt.get("document_vector_set_digests", {}).get("body_only_v1") != provenance["body_vector_set_digest"]:
        raise Step99ContractError("body-only vector provenance mismatch")
    if file_digest(vectors_path) != provenance["vectors_file_sha256"]:
        raise Step99ContractError("source vectors file digest mismatch")
    payload = _load_json_mapping(vectors_path)
    query_vectors = payload.get("query_vectors")
    document_vectors = payload.get("document_vectors", {}).get("body_only_v1")
    if not isinstance(query_vectors, dict) or not isinstance(document_vectors, dict):
        raise Step99ContractError("source vector sets missing")
    if canonical_digest(query_vectors) != provenance["query_vector_set_digest"]:
        raise Step99ContractError("query vector-set digest mismatch")
    if canonical_digest(document_vectors) != provenance["body_vector_set_digest"]:
        raise Step99ContractError("body vector-set digest mismatch")
    if set(query_vectors) != {item["query_id"] for item in queries}:
        raise Step99ContractError("query vector identities mismatch")
    if set(document_vectors) != {item["chunk_id"] for item in chunks}:
        raise Step99ContractError("body vector identities mismatch")
    dimensions = int(provenance["dimensions"])
    if any(len(vector) != dimensions for vector in query_vectors.values()):
        raise Step99ContractError("query vector dimensions mismatch")
    if any(len(vector) != dimensions for vector in document_vectors.values()):
        raise Step99ContractError("document vector dimensions mismatch")
    return query_vectors, document_vectors


def vector_ranking(
    query_vector: Sequence[float],
    document_vectors: Mapping[str, Sequence[float]],
) -> list[str]:
    scored = [
        (_cosine(query_vector, vector), chunk_id)
        for chunk_id, vector in document_vectors.items()
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in scored]


def keyword_ranking(query: str, chunks: Sequence[Mapping[str, Any]]) -> list[str]:
    normalized_query = _normalize_text(query)
    query_tokens = set(TOKEN_PATTERN.findall(normalized_query))
    scored: list[tuple[float, str]] = []
    for chunk in chunks:
        normalized_chunk = _normalize_text(str(chunk["chunk_text"]))
        chunk_tokens = set(TOKEN_PATTERN.findall(normalized_chunk))
        overlap = len(query_tokens & chunk_tokens)
        phrase_bonus = 0.15 if normalized_query and normalized_query in normalized_chunk else 0.0
        if overlap == 0 and phrase_bonus == 0.0:
            continue
        coverage = overlap / len(query_tokens) if query_tokens else 0.0
        density = overlap / len(chunk_tokens) if chunk_tokens else 0.0
        score = min(1.0, 0.75 * coverage + 0.25 * density + phrase_bonus)
        if score > 0:
            scored.append((score, str(chunk["chunk_id"])))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[1] for item in scored]


def weighted_rrf_ranking(
    vector: Sequence[str],
    keyword: Sequence[str],
    *,
    vector_weight: float,
    keyword_weight: float,
    constant: int,
    depth: int,
) -> list[str]:
    vector_ranks = {chunk_id: rank for rank, chunk_id in enumerate(vector[:depth], start=1)}
    keyword_ranks = {chunk_id: rank for rank, chunk_id in enumerate(keyword[:depth], start=1)}
    missing = depth + 1
    scored = []
    for chunk_id in set(vector_ranks) | set(keyword_ranks):
        score = 0.0
        if chunk_id in vector_ranks:
            score += vector_weight / (constant + vector_ranks[chunk_id])
        if chunk_id in keyword_ranks:
            score += keyword_weight / (constant + keyword_ranks[chunk_id])
        scored.append(
            (
                score,
                vector_ranks.get(chunk_id, missing),
                keyword_ranks.get(chunk_id, missing),
                chunk_id,
            )
        )
    scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
    return [item[3] for item in scored]


def evaluate_experiment(
    *,
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    pgvector_evidence_path: Optional[Path] = None,
) -> Dict[str, Any]:
    manifest = load_contract(fixture_dir)
    _, chunks, queries = load_source_dataset(manifest)
    query_vectors, document_vectors = load_source_vectors(
        manifest,
        chunks=chunks,
        queries=queries,
    )
    query_by_id = {item["query_id"]: item for item in queries}
    vector_rankings: Dict[str, list[str]] = {}
    keyword_rankings: Dict[str, list[str]] = {}
    for query in queries:
        query_id = query["query_id"]
        vector_rankings[query_id] = vector_ranking(
            query_vectors[query_id], document_vectors
        )
        keyword_rankings[query_id] = keyword_ranking(query["query"], chunks)

    tuning_ids = manifest["dataset"]["tuning_query_ids"]
    rrf = manifest["weighted_rrf"]
    tuning_candidates: Dict[str, Any] = {}
    candidate_rankings: Dict[str, Dict[str, list[str]]] = {}
    for weights in rrf["weight_candidates"]:
        key = weights["id"]
        rankings = {
            query_id: weighted_rrf_ranking(
                vector_rankings[query_id],
                keyword_rankings[query_id],
                vector_weight=float(weights["vector"]),
                keyword_weight=float(weights["keyword"]),
                constant=int(rrf["constant"]),
                depth=int(rrf["candidate_depth"]),
            )
            for query_id in tuning_ids
        }
        candidate_rankings[key] = rankings
        tuning_candidates[key] = _metric_summary(tuning_ids, rankings, query_by_id)
    selected_weight_id = max(
        tuning_candidates,
        key=lambda key: _tuning_selection_key(
            tuning_candidates[key],
            next(item for item in rrf["weight_candidates"] if item["id"] == key),
        ),
    )
    selected_weights = next(
        item for item in rrf["weight_candidates"] if item["id"] == selected_weight_id
    )
    selected_rrf_rankings = {
        query_id: weighted_rrf_ranking(
            vector_rankings[query_id],
            keyword_rankings[query_id],
            vector_weight=float(selected_weights["vector"]),
            keyword_weight=float(selected_weights["keyword"]),
            constant=int(rrf["constant"]),
            depth=int(rrf["candidate_depth"]),
        )
        for query_id in manifest["dataset"]["decision_query_ids"]
    }
    decision_ids = manifest["dataset"]["decision_query_ids"]
    decision_rankings = {
        "vector_only": {query_id: vector_rankings[query_id] for query_id in decision_ids},
        "keyword_only": {query_id: keyword_rankings[query_id] for query_id in decision_ids},
        "weighted_rrf": selected_rrf_rankings,
    }
    scores = {
        variant: _score_variant(
            manifest,
            decision_ids,
            rankings,
            query_by_id,
            baseline_rankings=decision_rankings["vector_only"],
        )
        for variant, rankings in decision_rankings.items()
    }
    citations = {
        variant: _citation_evidence(decision_ids, rankings, query_by_id, chunks)
        for variant, rankings in decision_rankings.items()
    }
    independent_citation = evaluate_citation_gates()
    repository_safety = evaluate_production_repository_safety()
    pgvector_evidence = _validate_pgvector_evidence(manifest, pgvector_evidence_path)
    gate = _apply_gate(
        manifest,
        scores,
        citations,
        independent_citation=independent_citation,
        repository_safety=repository_safety,
        pgvector_passed=pgvector_evidence,
    )
    operation_counts = {
        "vector_cosine_evaluations": len(queries) * len(chunks),
        "keyword_chunk_evaluations": len(queries) * len(chunks),
        "rrf_candidate_depth_per_input": int(rrf["candidate_depth"]),
        "decision_query_count": len(decision_ids),
        "latency_kind": "offline_deterministic_operation_count_not_wall_clock",
    }
    body = {
        "experiment_id": EXPERIMENT_ID,
        "manifest_digest": canonical_digest(manifest),
        "source_capture_run_digest": manifest["vector_provenance"]["capture_run_digest"],
        "selected_weight_id": selected_weight_id,
        "selected_weights": selected_weights,
        "tuning_candidate_metrics": tuning_candidates,
        "decision_variant_scores": scores,
        "decision_citation_evidence": citations,
        "independent_citation_evidence": vars(independent_citation),
        "production_repository_safety_passed": repository_safety,
        "pgvector_adapter_integration_passed": pgvector_evidence,
        "offline_computational_overhead": operation_counts,
        "decision": gate,
        "scoring_version": manifest["scoring"]["version"],
    }
    canonical = canonicalize(body)
    canonical["result_digest"] = canonical_digest(canonical)
    return canonical


def write_or_replay(path: Path, payload: Mapping[str, Any]) -> str:
    canonical = canonicalize(payload)
    if not path.exists():
        _write_json_create_only(path, canonical)
        return "created"
    existing = canonicalize(_load_json_mapping(path))
    if existing.get("result_digest") != canonical.get("result_digest"):
        raise Step99ContractError("non_deterministic_result_digest")
    if existing != canonical:
        raise Step99ContractError("non_deterministic_semantic_payload")
    return "deterministic_replay"


def _score_variant(
    manifest: Mapping[str, Any],
    query_ids: Sequence[str],
    rankings: Mapping[str, Sequence[str]],
    query_by_id: Mapping[str, Mapping[str, Any]],
    *,
    baseline_rankings: Mapping[str, Sequence[str]],
) -> Dict[str, Any]:
    baseline_hit3 = {query_id for query_id in query_ids if _relevant_rank(query_by_id[query_id], baseline_rankings[query_id]) <= 3}
    current_hit3 = {query_id for query_id in query_ids if _relevant_rank(query_by_id[query_id], rankings[query_id]) <= 3}
    rank_delta: Dict[str, Any] = {}
    improved: list[str] = []
    unchanged: list[str] = []
    worsened: list[str] = []
    for query_id in query_ids:
        base_rank = _relevant_rank(query_by_id[query_id], baseline_rankings[query_id])
        rank = _relevant_rank(query_by_id[query_id], rankings[query_id])
        delta = _finite_rank(base_rank) - _finite_rank(rank)
        rank_delta[query_id] = {
            "vector_rank": None if math.isinf(base_rank) else int(base_rank),
            "variant_rank": None if math.isinf(rank) else int(rank),
            "delta_positive_is_improvement": int(delta),
        }
        if delta > 0:
            improved.append(query_id)
        elif delta < 0:
            worsened.append(query_id)
        else:
            unchanged.append(query_id)
    primary = {
        name: _metric_summary(
            [query_id for query_id in ids["decision"]], rankings, query_by_id
        )
        for name, ids in manifest["dataset"]["primary_cell_splits"].items()
    }
    critical = {
        name: _metric_summary(
            [query_id for query_id in ids if query_id in set(query_ids)],
            rankings,
            query_by_id,
        )
        for name, ids in manifest["dataset"]["critical_cohorts"].items()
    }
    secondary = {
        name: _metric_summary(
            [query_id for query_id in ids if query_id in set(query_ids)],
            rankings,
            query_by_id,
        )
        for name, ids in manifest["dataset"]["secondary_cohorts"].items()
    }
    return {
        "overall": _metric_summary(query_ids, rankings, query_by_id),
        "primary_cells": primary,
        "critical_cohorts": critical,
        "secondary_cohorts": secondary,
        "hit_at_3_gained_query_ids": sorted(current_hit3 - baseline_hit3),
        "hit_at_3_lost_query_ids": sorted(baseline_hit3 - current_hit3),
        "rank_improved_query_ids": sorted(improved),
        "rank_unchanged_query_ids": sorted(unchanged),
        "rank_worsened_query_ids": sorted(worsened),
        "per_query_rank_delta": rank_delta,
    }


def _metric_summary(
    query_ids: Sequence[str],
    rankings: Mapping[str, Sequence[str]],
    query_by_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    if not query_ids:
        return {
            "query_count": 0,
            "hit_at_1": 0.0,
            "hit_at_3": 0.0,
            "hit_at_5": 0.0,
            "reciprocal_rank_sum": 0.0,
            "mrr_at_5": 0.0,
            "ndcg_at_5": 0.0,
        }
    ranks = [_relevant_rank(query_by_id[query_id], rankings[query_id]) for query_id in query_ids]
    reciprocal = [1.0 / rank if rank <= 5 else 0.0 for rank in ranks]
    ndcg = [_ndcg_at_5(query_by_id[query_id], rankings[query_id]) for query_id in query_ids]
    count = len(query_ids)
    return {
        "query_count": count,
        "hit_at_1": sum(rank <= 1 for rank in ranks) / count,
        "hit_at_3": sum(rank <= 3 for rank in ranks) / count,
        "hit_at_5": sum(rank <= 5 for rank in ranks) / count,
        "reciprocal_rank_sum": sum(reciprocal),
        "mrr_at_5": sum(reciprocal) / count,
        "ndcg_at_5": sum(ndcg) / count,
    }


def _citation_evidence(
    query_ids: Sequence[str],
    rankings: Mapping[str, Sequence[str]],
    query_by_id: Mapping[str, Mapping[str, Any]],
    chunks: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    paths = {item["chunk_id"]: item["notion_path"] for item in chunks}
    required_total = emitted_total = correct_total = invalid_total = collapsed = 0
    for query_id in query_ids:
        query = query_by_id[query_id]
        required = set(query["required_citation_paths"])
        allowed = set(query["allowed_citation_paths"])
        emitted: list[str] = []
        seen: set[str] = set()
        for chunk_id in rankings[query_id][:5]:
            path = paths[chunk_id]
            if path in seen:
                collapsed += 1
                continue
            seen.add(path)
            emitted.append(path)
        emitted_set = set(emitted)
        required_total += len(required)
        emitted_total += len(emitted_set)
        correct_total += len(required & emitted_set)
        invalid_total += len(emitted_set - allowed)
    return {
        "recall": correct_total / required_total if required_total else 0.0,
        "precision": (emitted_total - invalid_total) / emitted_total if emitted_total else 0.0,
        "invalid_citation_count": invalid_total,
        "derived_or_unsupported_header_citation_count": 0,
        "duplicate_citation_paths_collapsed": collapsed,
    }


def _apply_gate(
    manifest: Mapping[str, Any],
    scores: Mapping[str, Mapping[str, Any]],
    citations: Mapping[str, Mapping[str, Any]],
    *,
    independent_citation: Any,
    repository_safety: bool,
    pgvector_passed: bool,
) -> Dict[str, Any]:
    if not repository_safety or not pgvector_passed:
        return {
            "status": "inconclusive",
            "reasons": [
                reason
                for reason, passed in (
                    ("PRODUCTION_REPOSITORY_SAFETY_INCOMPLETE", repository_safety),
                    ("PGVECTOR_ADAPTER_INTEGRATION_INCOMPLETE", pgvector_passed),
                )
                if not passed
            ],
        }
    independent_values = (
        independent_citation.citation_recall,
        independent_citation.citation_precision,
        independent_citation.golden_citation_recall,
        independent_citation.golden_citation_precision,
    )
    if independent_values != (1.0, 1.0, 1.0, 1.0) or independent_citation.invalid_citation_count != 0 or independent_citation.golden_invalid_citation_count != 0:
        return {"status": "inconclusive", "reasons": ["INDEPENDENT_CITATION_GATE_FAILED"]}
    baseline = scores["vector_only"]
    hybrid = scores["weighted_rrf"]
    base_overall = baseline["overall"]
    hybrid_overall = hybrid["overall"]
    thresholds = manifest["thresholds"]
    reasons: list[str] = []
    if len(hybrid["hit_at_3_gained_query_ids"]) < int(thresholds["overall_hit3_gains"]):
        reasons.append("OVERALL_HIT3_GAIN")
    if len(hybrid["hit_at_3_lost_query_ids"]) != int(thresholds["overall_hit3_losses"]):
        reasons.append("OVERALL_HIT3_LOSS")
    if hybrid_overall["reciprocal_rank_sum"] - base_overall["reciprocal_rank_sum"] < float(thresholds["overall_reciprocal_rank_gain"]):
        reasons.append("OVERALL_MRR_GAIN")
    for metric in ("hit_at_1", "hit_at_5", "ndcg_at_5"):
        if hybrid_overall[metric] < base_overall[metric]:
            reasons.append(f"OVERALL_{metric.upper()}_REGRESSION")
    for group in ("primary_cells", "critical_cohorts"):
        for name, summary in hybrid[group].items():
            base_summary = baseline[group][name]
            query_ids = manifest["dataset"]["primary_cell_splits"][name]["decision"] if group == "primary_cells" else [query_id for query_id in manifest["dataset"]["critical_cohorts"][name] if query_id in set(manifest["dataset"]["decision_query_ids"])]
            lost = _cohort_lost_hits(query_ids, baseline, hybrid)
            if lost:
                reasons.append(f"{group}:{name}:HIT3_LOST")
            if summary["mrr_at_5"] < base_summary["mrr_at_5"]:
                reasons.append(f"{group}:{name}:MRR_REGRESSION")
    for name in ("short", "long"):
        query_ids = [query_id for query_id in manifest["dataset"]["secondary_cohorts"][name] if query_id in set(manifest["dataset"]["decision_query_ids"])]
        if _cohort_lost_hits(query_ids, baseline, hybrid):
            reasons.append(f"secondary_cohorts:{name}:HIT3_LOST")
        if hybrid["secondary_cohorts"][name]["mrr_at_5"] < baseline["secondary_cohorts"][name]["mrr_at_5"]:
            reasons.append(f"secondary_cohorts:{name}:MRR_REGRESSION")
    base_citation = citations["vector_only"]
    hybrid_citation = citations["weighted_rrf"]
    if hybrid_citation["recall"] < base_citation["recall"]:
        reasons.append("CITATION_RECALL_REGRESSION")
    if hybrid_citation["precision"] < base_citation["precision"]:
        reasons.append("CITATION_PRECISION_REGRESSION")
    if hybrid_citation["invalid_citation_count"] > base_citation["invalid_citation_count"]:
        reasons.append("INVALID_CITATION_REGRESSION")
    if hybrid_citation["derived_or_unsupported_header_citation_count"] != 0:
        reasons.append("DERIVED_CITATION")
    return {
        "status": "maintain_vector_primary" if reasons else "hybrid_candidate_for_step100",
        "reasons": reasons,
    }


def _cohort_lost_hits(
    query_ids: Sequence[str],
    baseline: Mapping[str, Any],
    hybrid: Mapping[str, Any],
) -> list[str]:
    base_lost = set(baseline["hit_at_3_lost_query_ids"])
    hybrid_lost = set(hybrid["hit_at_3_lost_query_ids"])
    return sorted(set(query_ids) & (hybrid_lost - base_lost))


def _validate_pgvector_evidence(
    manifest: Mapping[str, Any],
    path: Optional[Path],
) -> bool:
    if path is None:
        return False
    expected = _REPO_ROOT / manifest["artifacts"]["pgvector_evidence_path"]
    if path.resolve() != expected.resolve():
        raise Step99ContractError("pgvector evidence path mismatch")
    evidence = _load_json_mapping(path)
    body = dict(evidence)
    digest = body.pop("receipt_digest", None)
    if canonical_digest(body) != digest:
        raise Step99ContractError("pgvector evidence digest mismatch")
    if (
        evidence.get("status") != "passed"
        or evidence.get("experiment_id") != EXPERIMENT_ID
        or evidence.get("manifest_digest") != canonical_digest(manifest)
        or evidence.get("database_prefix") != "learnloop_step99_"
        or evidence.get("production_database_used") is not False
        or evidence.get("expected_eligible_sets_nonempty") is not True
        or evidence.get("filter_before_top_k_passed") is not True
        or evidence.get("cleanup_status") != "passed"
        or int(evidence.get("remaining_database_count", -1)) != 0
    ):
        raise Step99ContractError("pgvector evidence contract mismatch")
    return True


def _validate_fixed_contract(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != "step99_manifest_v1":
        raise Step99ContractError("schema version mismatch")
    rrf = manifest.get("weighted_rrf", {})
    if rrf.get("constant") != 60 or rrf.get("candidate_depth") != 20:
        raise Step99ContractError("RRF contract mismatch")
    expected_weights = [
        {"id": "v050_k050", "vector": "0.50", "keyword": "0.50"},
        {"id": "v065_k035", "vector": "0.65", "keyword": "0.35"},
        {"id": "v080_k020", "vector": "0.80", "keyword": "0.20"},
    ]
    if rrf.get("weight_candidates") != expected_weights:
        raise Step99ContractError("RRF weight candidates mismatch")
    if manifest.get("thresholds") != {
        "overall_hit3_gains": 3,
        "overall_hit3_losses": 0,
        "overall_reciprocal_rank_gain": "2.700",
    }:
        raise Step99ContractError("threshold contract mismatch")


def _corpus_identity(chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
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


def _qrels_identity(queries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
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


def _relevant_rank(query: Mapping[str, Any], ranking: Sequence[str]) -> float:
    relevant = {chunk_id for chunk_id, grade in query["relevance"].items() if int(grade) > 0}
    for rank, chunk_id in enumerate(ranking, start=1):
        if chunk_id in relevant:
            return float(rank)
    return math.inf


def _ndcg_at_5(query: Mapping[str, Any], ranking: Sequence[str]) -> float:
    grades = {chunk_id: int(grade) for chunk_id, grade in query["relevance"].items()}
    dcg = sum(
        ((2**grades.get(chunk_id, 0)) - 1) / math.log2(rank + 1)
        for rank, chunk_id in enumerate(ranking[:5], start=1)
    )
    ideal = sorted(grades.values(), reverse=True)[:5]
    idcg = sum(((2**grade) - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


def _tuning_selection_key(summary: Mapping[str, Any], weights: Mapping[str, Any]) -> tuple[float, ...]:
    return (
        round(float(summary["ndcg_at_5"]), 12),
        round(float(summary["mrr_at_5"]), 12),
        round(float(summary["hit_at_3"]), 12),
        round(float(summary["hit_at_1"]), 12),
        float(weights["vector"]),
    )


def _finite_rank(rank: float) -> int:
    return 109 if math.isinf(rank) else int(rank)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else -1.0


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_yaml_mapping(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Step99ContractError(f"invalid YAML mapping: {path.name}")
    return value


def _load_yaml_list(path: Path, key: str) -> list[dict[str, Any]]:
    value = _load_yaml_mapping(path).get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise Step99ContractError(f"invalid YAML list: {path.name}")
    return value


def _load_json_mapping(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise Step99ContractError(f"invalid JSON artifact: {path.name}") from None
    if not isinstance(value, dict):
        raise Step99ContractError(f"invalid JSON mapping: {path.name}")
    return value


def _write_json_create_only(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.pending")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(canonicalize(payload), output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Step 99 hybrid retrieval evaluation")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--pgvector-evidence", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    try:
        if args.freeze:
            manifest = load_contract(args.fixture_dir, create_receipt=True)
            print(json.dumps({"status": "frozen", "experiment_id": EXPERIMENT_ID, "manifest_digest": canonical_digest(manifest)}, sort_keys=True))
            return
        if args.evaluate:
            if args.result is None:
                raise Step99ContractError("canonical result path required")
            manifest = load_contract(args.fixture_dir)
            expected = _REPO_ROOT / manifest["artifacts"]["result_path"]
            if args.result.resolve() != expected.resolve():
                raise Step99ContractError("canonical result path mismatch")
            payload = evaluate_experiment(
                fixture_dir=args.fixture_dir,
                pgvector_evidence_path=args.pgvector_evidence,
            )
            replay = write_or_replay(args.result, payload)
            print(json.dumps({"status": payload["decision"]["status"], "selected_weight_id": payload["selected_weight_id"], "result_digest": payload["result_digest"], "replay_status": replay}, sort_keys=True))
            return
        manifest = load_contract(args.fixture_dir)
        print(json.dumps({"status": "validated", "manifest_digest": canonical_digest(manifest)}, sort_keys=True))
    except Step99ContractError as exc:
        print(json.dumps({"status": "inconclusive", "safe_failure_category": str(exc)}, sort_keys=True))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
