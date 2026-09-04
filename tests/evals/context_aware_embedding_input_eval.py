from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.rag.embedding_input_builder import (
    BODY_ONLY_VERSION,
    QUERY_BUILDER_VERSION,
    TITLE_BODY_VERSION,
    TITLE_HEADING_BODY_VERSION,
    EmbeddingInputRecord,
    HeadingSource,
    build_document_embedding_input,
    build_query_embedding_input,
)


EXPERIMENT_ID = "step98-exp-001"
DEFAULT_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "step_98" / EXPERIMENT_ID
)
VARIANTS = (
    BODY_ONLY_VERSION,
    TITLE_BODY_VERSION,
    TITLE_HEADING_BODY_VERSION,
)
EXPECTED_COUNTS = {
    "pages": 18,
    "chunks": 108,
    "queries": 72,
    "hard_negative_pairs": 144,
}
EXPECTED_CRITICAL_DENOMINATORS = {
    "title_only_semantic": 18,
    "body_only": 24,
    "traditional_chinese": 24,
    "english": 24,
    "mixed_language": 24,
    "ambiguous": 24,
}
EXPECTED_SECONDARY_DENOMINATORS = {
    "exact_title_lookup": 6,
    "short": 18,
    "standard_length": 36,
    "long": 18,
    "deduplication": 9,
    "generic_noise": 9,
}
EXPECTED_THRESHOLDS = {
    "overall_hit3_gains": 3,
    "overall_hit3_losses": 0,
    "overall_reciprocal_rank_gain": "3.600",
    "title_semantic_hit3_gains": 2,
    "ambiguity_new_errors": 0,
    "heading_resolved_errors": 2,
    "heading_ambiguity_reciprocal_rank_gain": "1.200",
    "heading_over_title_reciprocal_rank_gain": "1.440",
}


class Step98ContractError(Exception):
    pass


@dataclass(frozen=True)
class Preregistration:
    fixture_dir: Path
    manifest: Dict[str, Any]
    manifest_digest: str
    sources: Tuple[Dict[str, Any], ...]
    chunks: Tuple[Dict[str, Any], ...]
    queries: Tuple[Dict[str, Any], ...]


@dataclass(frozen=True)
class CaptureRequest:
    ordinal: int
    role: str
    variant_id: str
    batch_ordinal: int
    item_ids: Tuple[str, ...]
    input_digests: Tuple[str, ...]


@dataclass(frozen=True)
class CapturePlan:
    experiment_id: str
    manifest_digest: str
    provider: str
    model: str
    dimensions: int
    query_builder_version: str
    requests: Tuple[CaptureRequest, ...]
    request_plan_digest: str


@dataclass(frozen=True)
class CaptureArtifact:
    experiment_id: str
    manifest_digest: str
    capture_run_id: str
    capture_run_digest: str
    request_plan_digest: str
    query_vector_set_digest: str
    document_vector_set_digests: Dict[str, str]
    provider: str
    model: str
    dimensions: int
    provider_revision_id: Optional[str]
    batch_count: int
    retry_count: int
    token_input: Optional[int]
    estimated_cost_usd: Optional[float]
    duration_seconds: float
    vectors_retained: bool


@dataclass(frozen=True)
class IndependentGateEvidence:
    citation_recall: float
    citation_precision: float
    invalid_citation_count: int
    derived_header_citation_count: int
    golden_citation_recall: float
    golden_citation_precision: float
    golden_invalid_citation_count: int
    production_repository_safety_passed: bool
    pgvector_adapter_integration_passed: bool


@dataclass(frozen=True)
class RankedQuery:
    query_id: str
    ranked_chunk_ids: Tuple[str, ...]


@dataclass(frozen=True)
class MetricSummary:
    query_count: int
    hit_at_1: float
    hit_at_3: float
    hit_at_5: float
    reciprocal_rank_sum: float
    mrr_at_5: float
    ndcg_at_5: float


@dataclass(frozen=True)
class VariantScore:
    variant_id: str
    overall: MetricSummary
    primary_cells: Dict[str, MetricSummary]
    critical_cohorts: Dict[str, MetricSummary]
    secondary_cohorts: Dict[str, MetricSummary]
    hit_at_3_query_ids: Tuple[str, ...]
    ambiguity_errors: Tuple[str, ...]
    wrong_page_at_3: Tuple[str, ...]
    rankings: Tuple[RankedQuery, ...]


@dataclass(frozen=True)
class GateDecision:
    status: str
    selected_variant: Optional[str]
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class EvaluationResult:
    experiment_id: str
    manifest_digest: str
    capture_digest: str
    scoring_version: str
    implementation_source_digest: str
    variant_scores: Dict[str, VariantScore]
    gate: GateDecision
    result_digest: str


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_preregistration(
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    *,
    create_receipt: bool = False,
) -> Preregistration:
    manifest_path = fixture_dir / "manifest.yaml"
    receipt_path = fixture_dir / "manifest.sha256"
    manifest = _load_yaml_mapping(manifest_path)
    manifest_digest = canonical_digest(manifest)

    if manifest.get("experiment_id") != EXPERIMENT_ID:
        raise Step98ContractError("experiment id mismatch")
    for filename in ("source_records.yaml", "chunks.yaml", "queries.yaml"):
        expected = manifest["file_digests"].get(filename)
        if expected != file_digest(fixture_dir / filename):
            raise Step98ContractError(f"managed file digest mismatch: {filename}")
    implementation = manifest["implementation"]
    for role in ("builder", "scoring", "capture"):
        source_path = _REPO_ROOT / implementation[f"{role}_source_path"]
        if implementation[f"{role}_source_digest"] != file_digest(source_path):
            raise Step98ContractError(f"{role} implementation source digest mismatch")

    sources = tuple(_load_yaml_list(fixture_dir / "source_records.yaml", "sources"))
    chunks = tuple(_load_yaml_list(fixture_dir / "chunks.yaml", "chunks"))
    queries = tuple(_load_yaml_list(fixture_dir / "queries.yaml", "queries"))
    _validate_fixture(manifest, sources, chunks, queries)

    if create_receipt:
        try:
            with receipt_path.open("x", encoding="utf-8") as receipt_file:
                receipt_file.write(f"{manifest_digest}\n")
        except FileExistsError:
            pass
    if receipt_path.exists():
        receipt = receipt_path.read_text(encoding="utf-8").strip()
        if receipt != manifest_digest:
            raise Step98ContractError("manifest receipt mismatch")
    elif create_receipt:
        raise Step98ContractError("manifest receipt was not created")

    return Preregistration(
        fixture_dir=fixture_dir,
        manifest=manifest,
        manifest_digest=manifest_digest,
        sources=sources,
        chunks=chunks,
        queries=queries,
    )


def plan_capture(preregistration: Preregistration) -> CapturePlan:
    _require_receipt(preregistration)
    manifest = preregistration.manifest
    batch_size = int(manifest["capture"]["batch_size"])
    builder_digest = manifest["implementation"]["builder_source_digest"]
    requests: list[CaptureRequest] = []
    ordinal = 0

    query_items = [
        (query["query_id"], build_query_embedding_input(query["query"]))
        for query in preregistration.queries
    ]
    for batch_ordinal, batch in enumerate(_batches(query_items, batch_size)):
        requests.append(
            _capture_request(
                ordinal=ordinal,
                role="query",
                variant_id=QUERY_BUILDER_VERSION,
                batch_ordinal=batch_ordinal,
                batch=batch,
            )
        )
        ordinal += 1

    built_by_variant: Dict[str, List[Tuple[str, str]]] = {}
    for variant in VARIANTS:
        built_by_variant[variant] = []
        for chunk in preregistration.chunks:
            built = build_document_embedding_input(
                _builder_record(preregistration, chunk),
                variant_id=variant,
                implementation_source_digest=builder_digest,
            )
            built_by_variant[variant].append((chunk["chunk_id"], built.text))

    batch_counts = {
        len(tuple(_batches(items, batch_size))) for items in built_by_variant.values()
    }
    if len(batch_counts) != 1:
        raise Step98ContractError("variant batch counts differ")
    for batch_ordinal in range(batch_counts.pop()):
        for variant in VARIANTS:
            batch = tuple(_batches(built_by_variant[variant], batch_size))[batch_ordinal]
            requests.append(
                _capture_request(
                    ordinal=ordinal,
                    role="document",
                    variant_id=variant,
                    batch_ordinal=batch_ordinal,
                    batch=batch,
                )
            )
            ordinal += 1

    request_payload = [asdict(request) for request in requests]
    return CapturePlan(
        experiment_id=EXPERIMENT_ID,
        manifest_digest=preregistration.manifest_digest,
        provider=manifest["embedding"]["provider"],
        model=manifest["embedding"]["model"],
        dimensions=int(manifest["embedding"]["dimensions"]),
        query_builder_version=QUERY_BUILDER_VERSION,
        requests=tuple(requests),
        request_plan_digest=canonical_digest(request_payload),
    )


def materialize_capture_inputs(
    preregistration: Preregistration,
    plan: CapturePlan,
) -> Dict[int, Tuple[str, ...]]:
    rebuilt = plan_capture(preregistration)
    if rebuilt.request_plan_digest != plan.request_plan_digest:
        raise Step98ContractError("capture plan changed during materialization")
    builder_digest = preregistration.manifest["implementation"]["builder_source_digest"]
    query_text_by_id = {
        query["query_id"]: build_query_embedding_input(query["query"])
        for query in preregistration.queries
    }
    document_text_by_variant: Dict[str, Dict[str, str]] = {}
    for variant in VARIANTS:
        document_text_by_variant[variant] = {}
        for chunk in preregistration.chunks:
            document_text_by_variant[variant][chunk["chunk_id"]] = (
                build_document_embedding_input(
                    _builder_record(preregistration, chunk),
                    variant_id=variant,
                    implementation_source_digest=builder_digest,
                ).text
            )
    materialized: Dict[int, Tuple[str, ...]] = {}
    for request in plan.requests:
        source = (
            query_text_by_id
            if request.role == "query"
            else document_text_by_variant[request.variant_id]
        )
        inputs = tuple(source[item_id] for item_id in request.item_ids)
        digests = tuple(hashlib.sha256(value.encode("utf-8")).hexdigest() for value in inputs)
        if digests != request.input_digests:
            raise Step98ContractError("materialized capture input digest mismatch")
        materialized[request.ordinal] = inputs
    return materialized


def validate_capture_artifact(
    preregistration: Preregistration,
    plan: CapturePlan,
    artifact: CaptureArtifact,
) -> None:
    _require_receipt(preregistration)
    if artifact.experiment_id != EXPERIMENT_ID:
        raise Step98ContractError("capture experiment id mismatch")
    if artifact.manifest_digest != preregistration.manifest_digest:
        raise Step98ContractError("capture manifest digest mismatch")
    if artifact.request_plan_digest != plan.request_plan_digest:
        raise Step98ContractError("capture request plan digest mismatch")
    if (
        artifact.provider != plan.provider
        or artifact.model != plan.model
        or artifact.dimensions != plan.dimensions
    ):
        raise Step98ContractError("capture embedding contract mismatch")
    if set(artifact.document_vector_set_digests) != set(VARIANTS):
        raise Step98ContractError("capture document vector sets incomplete")
    if not artifact.query_vector_set_digest or any(
        not value for value in artifact.document_vector_set_digests.values()
    ):
        raise Step98ContractError("capture vector set digest missing")
    if artifact.batch_count != len(plan.requests):
        raise Step98ContractError("capture batch count mismatch")
    capture_contract = preregistration.manifest["capture"]
    if artifact.duration_seconds > float(capture_contract["max_duration_seconds"]):
        raise Step98ContractError("capture duration budget exceeded")
    if artifact.token_input is None or artifact.estimated_cost_usd is None:
        raise Step98ContractError("capture usage or cost incomplete")
    if artifact.estimated_cost_usd > float(capture_contract["max_cost_usd"]):
        raise Step98ContractError("capture cost budget exceeded")
    if not artifact.vectors_retained:
        raise Step98ContractError("capture vectors not retained")
    expected_digest = canonical_digest(
        {
            "experiment_id": artifact.experiment_id,
            "manifest_digest": artifact.manifest_digest,
            "capture_run_id": artifact.capture_run_id,
            "request_plan_digest": artifact.request_plan_digest,
            "query_vector_set_digest": artifact.query_vector_set_digest,
            "document_vector_set_digests": artifact.document_vector_set_digests,
            "provider": artifact.provider,
            "model": artifact.model,
            "dimensions": artifact.dimensions,
            "provider_revision_id": artifact.provider_revision_id,
            "batch_count": artifact.batch_count,
            "retry_count": artifact.retry_count,
            "token_input": artifact.token_input,
            "estimated_cost_usd": artifact.estimated_cost_usd,
            "duration_seconds": artifact.duration_seconds,
            "vectors_retained": artifact.vectors_retained,
        }
    )
    if artifact.capture_run_digest != expected_digest:
        raise Step98ContractError("capture run digest mismatch")


def rank_captured_vectors(
    preregistration: Preregistration,
    *,
    query_vectors: Mapping[str, Sequence[float]],
    document_vectors: Mapping[str, Mapping[str, Sequence[float]]],
) -> Dict[str, Dict[str, List[str]]]:
    query_ids = {query["query_id"] for query in preregistration.queries}
    chunk_ids = {chunk["chunk_id"] for chunk in preregistration.chunks}
    dimensions = int(preregistration.manifest["embedding"]["dimensions"])
    if set(query_vectors) != query_ids or set(document_vectors) != set(VARIANTS):
        raise Step98ContractError("capture vector identities mismatch")
    for vector in query_vectors.values():
        _validate_vector(vector, dimensions)
    rankings: Dict[str, Dict[str, List[str]]] = {}
    for variant in VARIANTS:
        variant_vectors = document_vectors[variant]
        if set(variant_vectors) != chunk_ids:
            raise Step98ContractError("document vector identities mismatch")
        for vector in variant_vectors.values():
            _validate_vector(vector, dimensions)
        rankings[variant] = {}
        for query_id in sorted(query_ids):
            query_vector = query_vectors[query_id]
            scored = [
                (_cosine_similarity(query_vector, variant_vectors[chunk_id]), chunk_id)
                for chunk_id in chunk_ids
            ]
            scored.sort(key=lambda item: (-item[0], item[1]))
            rankings[variant][query_id] = [chunk_id for _, chunk_id in scored]
    return rankings


def score_rankings(
    preregistration: Preregistration,
    *,
    capture_digest: str,
    rankings_by_variant: Mapping[str, Mapping[str, Sequence[str]]],
    implementation_source_digest: str,
    independent_evidence: IndependentGateEvidence,
) -> EvaluationResult:
    _require_receipt(preregistration)
    if set(rankings_by_variant) != set(VARIANTS):
        raise Step98ContractError("complete variant rankings are required")
    query_ids = [query["query_id"] for query in preregistration.queries]
    scores: Dict[str, VariantScore] = {}
    for variant in VARIANTS:
        rankings = rankings_by_variant[variant]
        if set(rankings) != set(query_ids):
            raise Step98ContractError("ranking query ids mismatch")
        scores[variant] = _score_variant(preregistration, variant, rankings)
    decision = _apply_gate(preregistration, scores, independent_evidence)
    result_without_digest = {
        "experiment_id": EXPERIMENT_ID,
        "manifest_digest": preregistration.manifest_digest,
        "capture_digest": capture_digest,
        "scoring_version": preregistration.manifest["scoring"]["version"],
        "implementation_source_digest": implementation_source_digest,
        "variant_scores": {
            key: asdict(value) for key, value in sorted(scores.items())
        },
        "gate": asdict(decision),
        "independent_evidence": asdict(independent_evidence),
    }
    return EvaluationResult(
        experiment_id=EXPERIMENT_ID,
        manifest_digest=preregistration.manifest_digest,
        capture_digest=capture_digest,
        scoring_version=preregistration.manifest["scoring"]["version"],
        implementation_source_digest=implementation_source_digest,
        variant_scores=scores,
        gate=decision,
        result_digest=canonical_digest(result_without_digest),
    )


def validate_replay(canonical: EvaluationResult, replay: EvaluationResult) -> None:
    contract = (
        "manifest_digest",
        "capture_digest",
        "scoring_version",
        "implementation_source_digest",
    )
    if any(getattr(canonical, key) != getattr(replay, key) for key in contract):
        raise Step98ContractError("replay contract mismatch")
    if canonical.result_digest != replay.result_digest:
        raise Step98ContractError("non_deterministic_result")


def project_citations(
    *,
    retrieved_chunk_ids: Sequence[str],
    chunk_paths: Mapping[str, str],
    required_paths: Sequence[str],
    allowed_paths: Sequence[str],
) -> Dict[str, Any]:
    citations: list[str] = []
    seen: set[str] = set()
    invalid_count = 0
    for chunk_id in retrieved_chunk_ids:
        path = chunk_paths.get(chunk_id)
        if path is None:
            invalid_count += 1
            continue
        if path not in seen:
            seen.add(path)
            citations.append(path)
    invalid_count += sum(1 for path in citations if path not in set(allowed_paths))
    required = set(required_paths)
    emitted = set(citations)
    recall = len(required & emitted) / len(required) if required else 1.0
    precision = (
        len(emitted & set(allowed_paths)) / len(emitted) if emitted else 0.0
    )
    return {
        "citation_paths": citations,
        "recall": recall,
        "precision": precision,
        "invalid_citation_count": invalid_count,
        "derived_header_citation_count": 0,
    }


def _validate_fixture(
    manifest: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    chunks: Sequence[Mapping[str, Any]],
    queries: Sequence[Mapping[str, Any]],
) -> None:
    counts = manifest["counts"]
    if counts != EXPECTED_COUNTS:
        raise Step98ContractError("frozen count contract mismatch")
    if manifest["builders"]["document"] != list(VARIANTS):
        raise Step98ContractError("frozen document builder contract mismatch")
    if manifest["builders"]["query"] != QUERY_BUILDER_VERSION:
        raise Step98ContractError("frozen query builder contract mismatch")
    if manifest["embedding"] != {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "dimensions": 1536,
        "distance": "cosine",
        "revision_policy": "single_capture_session_if_revision_unavailable",
    }:
        raise Step98ContractError("frozen embedding contract mismatch")
    if manifest["thresholds"] != EXPECTED_THRESHOLDS:
        raise Step98ContractError("frozen threshold contract mismatch")
    if (len(sources), len(chunks), len(queries)) != (
        counts["pages"],
        counts["chunks"],
        counts["queries"],
    ):
        raise Step98ContractError("fixture count mismatch")
    _require_unique((source["page_id"] for source in sources), "page")
    _require_unique((chunk["chunk_id"] for chunk in chunks), "chunk")
    _require_unique((query["query_id"] for query in queries), "query")
    chunk_ids = {chunk["chunk_id"] for chunk in chunks}
    query_ids = {query["query_id"] for query in queries}
    source_ids = {source["page_id"] for source in sources}
    if any(chunk["page_id"] not in source_ids for chunk in chunks):
        raise Step98ContractError("chunk page reference mismatch")

    hard_negative_pairs = 0
    for query in queries:
        if "Page title:" in query["query"] or "Section:" in query["query"]:
            raise Step98ContractError("query contains serializer label")
        relevance = query["relevance"]
        if sum(1 for grade in relevance.values() if grade == 2) != 1:
            raise Step98ContractError("query must have one grade-2 target")
        if not set(relevance).issubset(chunk_ids):
            raise Step98ContractError("unknown relevance chunk")
        negatives = query["hard_negative_chunk_ids"]
        if len(negatives) != 2 or any(relevance.get(item) != 0 for item in negatives):
            raise Step98ContractError("hard-negative contract mismatch")
        hard_negative_pairs += len(negatives)
        if query["length_bucket"] not in {"short", "standard_length", "long"}:
            raise Step98ContractError("invalid length bucket")
    if hard_negative_pairs != counts["hard_negative_pairs"]:
        raise Step98ContractError("hard-negative pair count mismatch")

    memberships = manifest["memberships"]
    if len(memberships["primary_cells"]) != 9 or any(
        len(ids) != 8 for ids in memberships["primary_cells"].values()
    ):
        raise Step98ContractError("frozen primary-cell denominator mismatch")
    if {
        name: len(ids) for name, ids in memberships["critical_cohorts"].items()
    } != EXPECTED_CRITICAL_DENOMINATORS:
        raise Step98ContractError("frozen critical-cohort denominator mismatch")
    if {
        name: len(ids) for name, ids in memberships["secondary_cohorts"].items()
    } != EXPECTED_SECONDARY_DENOMINATORS:
        raise Step98ContractError("frozen secondary-cohort denominator mismatch")
    for family in ("primary_cells", "critical_cohorts", "secondary_cohorts"):
        declared = memberships[family]
        for name, expected_ids in declared.items():
            if len(expected_ids) != len(set(expected_ids)) or not set(expected_ids).issubset(query_ids):
                raise Step98ContractError(f"invalid membership: {family}/{name}")
    primary_ids = [
        query_id
        for ids in memberships["primary_cells"].values()
        for query_id in ids
    ]
    if len(primary_ids) != len(queries) or set(primary_ids) != query_ids:
        raise Step98ContractError("primary cells must partition all queries")
    length_ids = [
        query_id
        for name in ("short", "standard_length", "long")
        for query_id in memberships["secondary_cohorts"][name]
    ]
    if len(length_ids) != len(queries) or set(length_ids) != query_ids:
        raise Step98ContractError("length buckets must partition all queries")
    for query in queries:
        if query["query_id"] not in memberships["primary_cells"][query["primary_cell"]]:
            raise Step98ContractError("query primary cell mismatch")
        if query["query_id"] not in memberships["secondary_cohorts"][query["length_bucket"]]:
            raise Step98ContractError("query length bucket mismatch")


def _builder_record(
    preregistration: Preregistration,
    chunk: Mapping[str, Any],
) -> EmbeddingInputRecord:
    page = next(source for source in preregistration.sources if source["page_id"] == chunk["page_id"])
    return EmbeddingInputRecord(
        experiment_id=EXPERIMENT_ID,
        manifest_digest=preregistration.manifest_digest,
        source_snapshot_digest=preregistration.manifest["source_snapshot_digest"],
        chunk_id=chunk["chunk_id"],
        chunk_record_digest=chunk["record_digest"],
        chunk_text=chunk["chunk_text"],
        page_title_source_id=page["title_source_id"],
        page_title=page["title"],
        headings=tuple(
            HeadingSource(source_id=item["source_id"], text=item["text"])
            for item in chunk["headings"]
        ),
    )


def _capture_request(
    *,
    ordinal: int,
    role: str,
    variant_id: str,
    batch_ordinal: int,
    batch: Sequence[Tuple[str, str]],
) -> CaptureRequest:
    return CaptureRequest(
        ordinal=ordinal,
        role=role,
        variant_id=variant_id,
        batch_ordinal=batch_ordinal,
        item_ids=tuple(item[0] for item in batch),
        input_digests=tuple(hashlib.sha256(item[1].encode("utf-8")).hexdigest() for item in batch),
    )


def _batches(
    items: Sequence[Tuple[str, str]],
    size: int,
) -> Iterable[Tuple[Tuple[str, str], ...]]:
    for start in range(0, len(items), size):
        yield tuple(items[start : start + size])


def _score_variant(
    preregistration: Preregistration,
    variant_id: str,
    rankings: Mapping[str, Sequence[str]],
) -> VariantScore:
    query_by_id = {query["query_id"]: query for query in preregistration.queries}
    memberships = preregistration.manifest["memberships"]
    overall = _metric_summary(query_by_id, rankings, tuple(query_by_id))
    primary = {
        name: _metric_summary(query_by_id, rankings, tuple(ids))
        for name, ids in memberships["primary_cells"].items()
    }
    critical = {
        name: _metric_summary(query_by_id, rankings, tuple(ids))
        for name, ids in memberships["critical_cohorts"].items()
    }
    secondary = {
        name: _metric_summary(query_by_id, rankings, tuple(ids))
        for name, ids in memberships["secondary_cohorts"].items()
    }
    ambiguous_ids = memberships["critical_cohorts"]["ambiguous"]
    ambiguity_errors = []
    wrong_page_at_3 = []
    for query_id in ambiguous_ids:
        query = query_by_id[query_id]
        ranked = list(rankings[query_id])
        relevant = {chunk_id for chunk_id, grade in query["relevance"].items() if grade >= 1}
        wrong = set(query["wrong_page_chunk_ids"])
        best_relevant = _best_rank(ranked, relevant)
        best_wrong = _best_rank(ranked, wrong)
        if best_wrong <= 3:
            wrong_page_at_3.append(query_id)
        if best_wrong < best_relevant or (best_relevant > 3 and best_wrong <= 3):
            ambiguity_errors.append(query_id)
    return VariantScore(
        variant_id=variant_id,
        overall=overall,
        primary_cells=primary,
        critical_cohorts=critical,
        secondary_cohorts=secondary,
        hit_at_3_query_ids=tuple(
            query_id
            for query_id, query in query_by_id.items()
            if _is_hit(rankings[query_id], query["relevance"], 3)
        ),
        ambiguity_errors=tuple(ambiguity_errors),
        wrong_page_at_3=tuple(wrong_page_at_3),
        rankings=tuple(
            RankedQuery(query_id=query_id, ranked_chunk_ids=tuple(rankings[query_id]))
            for query_id in query_by_id
        ),
    )


def _metric_summary(
    query_by_id: Mapping[str, Mapping[str, Any]],
    rankings: Mapping[str, Sequence[str]],
    query_ids: Sequence[str],
) -> MetricSummary:
    hit1 = hit3 = hit5 = 0
    reciprocal_sum = 0.0
    ndcg_sum = 0.0
    for query_id in query_ids:
        relevance = query_by_id[query_id]["relevance"]
        ranked = rankings[query_id]
        hit1 += int(_is_hit(ranked, relevance, 1))
        hit3 += int(_is_hit(ranked, relevance, 3))
        hit5 += int(_is_hit(ranked, relevance, 5))
        rank = _best_rank(ranked[:5], {key for key, grade in relevance.items() if grade >= 1})
        reciprocal_sum += 0.0 if math.isinf(rank) else 1.0 / rank
        ndcg_sum += _ndcg_at_5(ranked, relevance)
    count = len(query_ids)
    return MetricSummary(
        query_count=count,
        hit_at_1=hit1 / count,
        hit_at_3=hit3 / count,
        hit_at_5=hit5 / count,
        reciprocal_rank_sum=round(reciprocal_sum, 12),
        mrr_at_5=round(reciprocal_sum / count, 12),
        ndcg_at_5=round(ndcg_sum / count, 12),
    )


def _apply_gate(
    preregistration: Preregistration,
    scores: Mapping[str, VariantScore],
    evidence: IndependentGateEvidence,
) -> GateDecision:
    evidence_failures = _independent_evidence_failures(evidence)
    if evidence_failures:
        return GateDecision(
            status="inconclusive",
            selected_variant=None,
            reasons=tuple(evidence_failures),
        )
    baseline = scores[BODY_ONLY_VERSION]
    eligible: list[str] = []
    failures: list[str] = []
    for variant in (TITLE_BODY_VERSION, TITLE_HEADING_BODY_VERSION):
        candidate = scores[variant]
        reasons = _candidate_failures(preregistration, baseline, candidate)
        if reasons:
            failures.extend(f"{variant}:{reason}" for reason in reasons)
        else:
            eligible.append(variant)
    if not eligible:
        return GateDecision(status="no_adoption", selected_variant=None, reasons=tuple(failures))
    selected = eligible[0]
    if len(eligible) == 2:
        selection_threshold = float(
            preregistration.manifest["thresholds"][
                "heading_over_title_reciprocal_rank_gain"
            ]
        )
        heading_gain = (
            scores[TITLE_HEADING_BODY_VERSION].overall.reciprocal_rank_sum
            - scores[TITLE_BODY_VERSION].overall.reciprocal_rank_sum
        )
        if heading_gain >= selection_threshold:
            selected = TITLE_HEADING_BODY_VERSION
    return GateDecision(status="pass_candidate_identified", selected_variant=selected, reasons=())


def _independent_evidence_failures(
    evidence: IndependentGateEvidence,
) -> List[str]:
    failures: list[str] = []
    if evidence.citation_recall != 1.0 or evidence.citation_precision != 1.0:
        failures.append("CITATION_ACCURACY_INCOMPLETE")
    if evidence.invalid_citation_count or evidence.derived_header_citation_count:
        failures.append("CITATION_INVALID")
    if (
        evidence.golden_citation_recall != 1.0
        or evidence.golden_citation_precision != 1.0
        or evidence.golden_invalid_citation_count
    ):
        failures.append("GOLDEN_CITATION_REGRESSION")
    if not evidence.production_repository_safety_passed:
        failures.append("PRODUCTION_REPOSITORY_SAFETY_INCOMPLETE")
    if not evidence.pgvector_adapter_integration_passed:
        failures.append("PGVECTOR_ADAPTER_INTEGRATION_INCOMPLETE")
    return failures


def _candidate_failures(
    preregistration: Preregistration,
    baseline: VariantScore,
    candidate: VariantScore,
) -> List[str]:
    failures: list[str] = []
    thresholds = preregistration.manifest["thresholds"]
    baseline_hits = set(baseline.hit_at_3_query_ids)
    candidate_hits = set(candidate.hit_at_3_query_ids)
    if len(candidate_hits - baseline_hits) < int(thresholds["overall_hit3_gains"]):
        failures.append("OVERALL_HIT3_GAIN")
    if baseline_hits - candidate_hits:
        failures.append("OVERALL_HIT3_LOST")
    if (
        candidate.overall.reciprocal_rank_sum
        - baseline.overall.reciprocal_rank_sum
        < float(thresholds["overall_reciprocal_rank_gain"])
    ):
        failures.append("OVERALL_MRR_GAIN")
    if candidate.overall.hit_at_1 < baseline.overall.hit_at_1:
        failures.append("OVERALL_HIT1_REGRESSION")
    if candidate.overall.hit_at_5 < baseline.overall.hit_at_5:
        failures.append("OVERALL_HIT5_REGRESSION")
    if candidate.overall.ndcg_at_5 < baseline.overall.ndcg_at_5:
        failures.append("OVERALL_NDCG_REGRESSION")

    memberships = preregistration.manifest["memberships"]
    for metric_name in ("hit_at_3", "mrr_at_5", "ndcg_at_5"):
        baseline_macro = sum(
            getattr(summary, metric_name) for summary in baseline.primary_cells.values()
        ) / len(baseline.primary_cells)
        candidate_macro = sum(
            getattr(summary, metric_name) for summary in candidate.primary_cells.values()
        ) / len(candidate.primary_cells)
        if candidate_macro < baseline_macro:
            failures.append(f"PRIMARY_MACRO_{metric_name.upper()}_REGRESSION")
    for family, ids in memberships["primary_cells"].items():
        _append_cohort_veto(failures, family, ids, baseline, candidate)
    for family, ids in memberships["critical_cohorts"].items():
        _append_cohort_veto(failures, family, ids, baseline, candidate)
    for family in ("short", "long"):
        _append_cohort_veto(
            failures,
            family,
            memberships["secondary_cohorts"][family],
            baseline,
            candidate,
        )
    title_ids = set(memberships["critical_cohorts"]["title_only_semantic"])
    if len((candidate_hits - baseline_hits) & title_ids) < int(
        thresholds["title_semantic_hit3_gains"]
    ):
        failures.append("TITLE_HIT3_GAIN")
    new_errors = set(candidate.ambiguity_errors) - set(baseline.ambiguity_errors)
    if new_errors:
        failures.append("NEW_AMBIGUITY_ERROR")
    if len(candidate.wrong_page_at_3) > len(baseline.wrong_page_at_3):
        failures.append("WRONG_PAGE_AT_3_REGRESSION")
    if candidate.variant_id == TITLE_HEADING_BODY_VERSION:
        resolved = set(baseline.ambiguity_errors) - set(candidate.ambiguity_errors)
        if len(resolved) < int(thresholds["heading_resolved_errors"]):
            failures.append("AMBIGUITY_RESOLUTION_GAIN")
        base_mrr = baseline.critical_cohorts["ambiguous"].reciprocal_rank_sum
        candidate_mrr = candidate.critical_cohorts["ambiguous"].reciprocal_rank_sum
        if candidate_mrr - base_mrr < float(
            thresholds["heading_ambiguity_reciprocal_rank_gain"]
        ):
            failures.append("AMBIGUITY_MRR_GAIN")
    return failures


def _append_cohort_veto(
    failures: List[str],
    name: str,
    query_ids: Sequence[str],
    baseline: VariantScore,
    candidate: VariantScore,
) -> None:
    baseline_hits = set(baseline.hit_at_3_query_ids) & set(query_ids)
    candidate_hits = set(candidate.hit_at_3_query_ids) & set(query_ids)
    if baseline_hits - candidate_hits:
        failures.append(f"{name}:HIT3_LOST")
    baseline_summary = _lookup_summary(baseline, name)
    candidate_summary = _lookup_summary(candidate, name)
    if candidate_summary.mrr_at_5 < baseline_summary.mrr_at_5:
        failures.append(f"{name}:MRR_REGRESSION")


def _lookup_summary(score: VariantScore, name: str) -> MetricSummary:
    if name in score.primary_cells:
        return score.primary_cells[name]
    if name in score.critical_cohorts:
        return score.critical_cohorts[name]
    return score.secondary_cohorts[name]


def _is_hit(ranking: Sequence[str], relevance: Mapping[str, int], k: int) -> bool:
    return any(relevance.get(chunk_id, 0) >= 1 for chunk_id in ranking[:k])


def _best_rank(ranking: Sequence[str], candidates: set[str]) -> float:
    for index, chunk_id in enumerate(ranking, start=1):
        if chunk_id in candidates:
            return float(index)
    return math.inf


def _ndcg_at_5(ranking: Sequence[str], relevance: Mapping[str, int]) -> float:
    gains = [int(relevance.get(chunk_id, 0)) for chunk_id in ranking[:5]]
    dcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sorted((int(value) for value in relevance.values()), reverse=True)[:5]
    idcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def _validate_vector(vector: Sequence[float], dimensions: int) -> None:
    if len(vector) != dimensions:
        raise Step98ContractError("capture vector dimensions mismatch")
    for value in vector:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise Step98ContractError("capture vector contains non-finite value")


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return -1.0
    return dot / (left_norm * right_norm)


def _require_receipt(preregistration: Preregistration) -> None:
    receipt_path = preregistration.fixture_dir / "manifest.sha256"
    if not receipt_path.exists() or receipt_path.read_text(encoding="utf-8").strip() != preregistration.manifest_digest:
        raise Step98ContractError("valid preregistration receipt required")


def _require_unique(values: Iterable[str], label: str) -> None:
    collected = list(values)
    if len(collected) != len(set(collected)):
        raise Step98ContractError(f"duplicate {label} id")


def _load_yaml_mapping(path: Path) -> Dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise Step98ContractError(f"invalid mapping fixture: {path.name}")
    return loaded


def _load_yaml_list(path: Path, key: str) -> List[Dict[str, Any]]:
    loaded = _load_yaml_mapping(path)
    values = loaded.get(key)
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise Step98ContractError(f"invalid list fixture: {path.name}/{key}")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 98 deterministic experiment contract")
    parser.add_argument("--phase-a", action="store_true")
    parser.add_argument("--plan-phase-b", action="store_true")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    args = parser.parse_args()
    preregistration = load_preregistration(
        args.fixture_dir,
        create_receipt=args.phase_a,
    )
    if args.phase_a:
        print(json.dumps({"status": "frozen", "experiment_id": EXPERIMENT_ID, "manifest_digest": preregistration.manifest_digest}, sort_keys=True))
        return
    if args.plan_phase_b:
        plan = plan_capture(preregistration)
        print(json.dumps({"status": "planned", "request_count": len(plan.requests), "request_plan_digest": plan.request_plan_digest}, sort_keys=True))
        return
    print(json.dumps({"status": "validated", "experiment_id": EXPERIMENT_ID, "manifest_digest": preregistration.manifest_digest}, sort_keys=True))


if __name__ == "__main__":
    main()
