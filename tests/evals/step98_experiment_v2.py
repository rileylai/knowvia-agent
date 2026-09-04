from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.rag.embedding_input_builder import (
    QUERY_BUILDER_VERSION,
    EmbeddingInputRecord,
    HeadingSource,
    build_document_embedding_input,
    build_query_embedding_input,
)

try:
    from . import context_aware_embedding_input_eval as v1
except ImportError:
    import context_aware_embedding_input_eval as v1  # type: ignore[no-redef]


EXPERIMENT_ID = "step98-exp-002"
DEFAULT_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "step_98" / EXPERIMENT_ID
)
PUBLIC_SAFE_FIXTURE_CLASS = "public_safe"
IMPLEMENTATION_SOURCE_ROLES = (
    "builder",
    "contract",
    "scoring",
    "capture",
    "phase_c",
    "safety",
    "citation",
    "pgvector_gate",
)


def load_preregistration(
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    *,
    create_receipt: bool = False,
) -> v1.Preregistration:
    manifest_path = fixture_dir / "manifest.yaml"
    receipt_path = fixture_dir / "manifest.sha256"
    manifest = v1._load_yaml_mapping(manifest_path)
    manifest_digest = v1.canonical_digest(manifest)
    if manifest.get("experiment_id") != EXPERIMENT_ID:
        raise v1.Step98ContractError("experiment id mismatch")
    if manifest.get("fixture_class") != PUBLIC_SAFE_FIXTURE_CLASS:
        raise v1.Step98ContractError("fixture is not declared public-safe")

    for filename in ("source_records.yaml", "chunks.yaml", "queries.yaml"):
        if manifest["file_digests"].get(filename) != v1.file_digest(fixture_dir / filename):
            raise v1.Step98ContractError(f"managed file digest mismatch: {filename}")
    implementation = manifest["implementation"]
    for role in IMPLEMENTATION_SOURCE_ROLES:
        path = v1._REPO_ROOT / implementation[f"{role}_source_path"]
        if implementation[f"{role}_source_digest"] != v1.file_digest(path):
            raise v1.Step98ContractError(f"{role} implementation source digest mismatch")
    for dependency in manifest["implementation_dependencies"]:
        dependency_path = v1._REPO_ROOT / dependency["path"]
        if dependency["digest"] != v1.file_digest(dependency_path):
            raise v1.Step98ContractError(
                f"implementation dependency digest mismatch: {dependency['path']}"
            )

    sources = tuple(v1._load_yaml_list(fixture_dir / "source_records.yaml", "sources"))
    chunks = tuple(v1._load_yaml_list(fixture_dir / "chunks.yaml", "chunks"))
    queries = tuple(v1._load_yaml_list(fixture_dir / "queries.yaml", "queries"))
    v1._validate_fixture(manifest, sources, chunks, queries)
    validate_public_safe_sources(sources)
    capture = manifest["capture"]
    if int(capture.get("max_external_attempts", 0)) != 16:
        raise v1.Step98ContractError("global external-attempt budget mismatch")
    if capture.get("artifact_contract") != "immutable_capture_directory_v2":
        raise v1.Step98ContractError("capture artifact contract mismatch")
    pgvector_contract = manifest["pgvector_gate_contract"]
    if (
        pgvector_contract["gate_source_digest"]
        != implementation["pgvector_gate_source_digest"]
    ):
        raise v1.Step98ContractError("pgvector gate source digest mismatch")
    repository_test_path = v1._REPO_ROOT / "tests/test_chunk_repository_pgvector_live.py"
    if pgvector_contract["repository_test_source_digest"] != v1.file_digest(repository_test_path):
        raise v1.Step98ContractError("pgvector repository test source digest mismatch")

    if create_receipt:
        try:
            with receipt_path.open("x", encoding="utf-8") as receipt_file:
                receipt_file.write(f"{manifest_digest}\n")
        except FileExistsError:
            pass
    if not receipt_path.exists():
        raise v1.Step98ContractError("valid preregistration receipt required")
    if receipt_path.read_text(encoding="utf-8").strip() != manifest_digest:
        raise v1.Step98ContractError("manifest receipt mismatch")
    return v1.Preregistration(
        fixture_dir=fixture_dir,
        manifest=manifest,
        manifest_digest=manifest_digest,
        sources=sources,
        chunks=chunks,
        queries=queries,
    )


def implementation_bundle_digest(manifest: Mapping[str, Any]) -> str:
    implementation = manifest["implementation"]
    return v1.canonical_digest(
        {
            "sources": {
                role: implementation[f"{role}_source_digest"]
                for role in IMPLEMENTATION_SOURCE_ROLES
            },
            "dependencies": manifest["implementation_dependencies"],
        }
    )


def validate_public_safe_sources(sources: Sequence[Mapping[str, Any]]) -> None:
    allowed_keys = {"page_id", "title_source_id", "title", "notion_path"}
    prohibited_fragments = ("token", "secret", "password", "authorization", "api_key")
    for source in sources:
        if set(source) != allowed_keys:
            raise v1.Step98ContractError("public-safe source schema mismatch")
        if not str(source["page_id"]).startswith("page-"):
            raise v1.Step98ContractError("public-safe page identity mismatch")
        if not str(source["title_source_id"]).startswith("title-page-"):
            raise v1.Step98ContractError("public-safe title identity mismatch")
        if not str(source["notion_path"]).startswith("Synthetic/"):
            raise v1.Step98ContractError("public-safe path mismatch")
        serialized = " ".join(str(value).casefold() for value in source.values())
        if any(fragment in serialized for fragment in prohibited_fragments):
            raise v1.Step98ContractError("public-safe source contains prohibited marker")


def plan_capture(preregistration: v1.Preregistration) -> v1.CapturePlan:
    _require_receipt(preregistration)
    return _build_capture_plan(preregistration)


def plan_capture_unfrozen(preregistration: v1.Preregistration) -> v1.CapturePlan:
    if (preregistration.fixture_dir / "manifest.sha256").exists():
        raise v1.Step98ContractError("unfrozen planning refused after receipt creation")
    return _build_capture_plan(preregistration)


def _build_capture_plan(preregistration: v1.Preregistration) -> v1.CapturePlan:
    batch_size = int(preregistration.manifest["capture"]["batch_size"])
    requests: List[v1.CaptureRequest] = []
    ordinal = 0
    query_items = [
        (query["query_id"], build_query_embedding_input(query["query"]))
        for query in preregistration.queries
    ]
    for batch_ordinal, batch in enumerate(v1._batches(query_items, batch_size)):
        requests.append(_capture_request(ordinal, "query", QUERY_BUILDER_VERSION, batch_ordinal, batch))
        ordinal += 1

    built_by_variant: Dict[str, List[Tuple[str, str]]] = {}
    builder_digest = preregistration.manifest["implementation"]["builder_source_digest"]
    for variant in v1.VARIANTS:
        built_by_variant[variant] = [
            (
                chunk["chunk_id"],
                build_document_embedding_input(
                    _builder_record(preregistration, chunk),
                    variant_id=variant,
                    implementation_source_digest=builder_digest,
                ).text,
            )
            for chunk in preregistration.chunks
        ]
    batches_by_variant = {
        variant: tuple(v1._batches(items, batch_size))
        for variant, items in built_by_variant.items()
    }
    if len({len(batches) for batches in batches_by_variant.values()}) != 1:
        raise v1.Step98ContractError("variant batch counts differ")
    for batch_ordinal in range(len(next(iter(batches_by_variant.values())))):
        for variant in v1.VARIANTS:
            requests.append(
                _capture_request(
                    ordinal,
                    "document",
                    variant,
                    batch_ordinal,
                    batches_by_variant[variant][batch_ordinal],
                )
            )
            ordinal += 1
    request_plan_digest = v1.canonical_digest(
        {
            "experiment_id": EXPERIMENT_ID,
            "provider": preregistration.manifest["embedding"]["provider"],
            "model": preregistration.manifest["embedding"]["model"],
            "dimensions": int(preregistration.manifest["embedding"]["dimensions"]),
            "schedule": preregistration.manifest["capture"]["schedule"],
            "requests": [asdict(request) for request in requests],
        }
    )
    return v1.CapturePlan(
        experiment_id=EXPERIMENT_ID,
        manifest_digest=preregistration.manifest_digest,
        provider=preregistration.manifest["embedding"]["provider"],
        model=preregistration.manifest["embedding"]["model"],
        dimensions=int(preregistration.manifest["embedding"]["dimensions"]),
        query_builder_version=QUERY_BUILDER_VERSION,
        requests=tuple(requests),
        request_plan_digest=request_plan_digest,
    )


def materialize_capture_inputs(
    preregistration: v1.Preregistration,
    plan: v1.CapturePlan,
) -> Dict[int, Tuple[str, ...]]:
    rebuilt = plan_capture(preregistration)
    if rebuilt.request_plan_digest != plan.request_plan_digest:
        raise v1.Step98ContractError("capture plan changed during materialization")
    query_text = {
        query["query_id"]: build_query_embedding_input(query["query"])
        for query in preregistration.queries
    }
    builder_digest = preregistration.manifest["implementation"]["builder_source_digest"]
    document_text: Dict[str, Dict[str, str]] = {}
    for variant in v1.VARIANTS:
        document_text[variant] = {
            chunk["chunk_id"]: build_document_embedding_input(
                _builder_record(preregistration, chunk),
                variant_id=variant,
                implementation_source_digest=builder_digest,
            ).text
            for chunk in preregistration.chunks
        }
    materialized: Dict[int, Tuple[str, ...]] = {}
    for request in plan.requests:
        source = query_text if request.role == "query" else document_text[request.variant_id]
        inputs = tuple(source[item_id] for item_id in request.item_ids)
        digests = tuple(hashlib.sha256(value.encode("utf-8")).hexdigest() for value in inputs)
        if digests != request.input_digests:
            raise v1.Step98ContractError("materialized capture input digest mismatch")
        materialized[request.ordinal] = inputs
    return materialized


def score_rankings(
    preregistration: v1.Preregistration,
    *,
    capture_digest: str,
    rankings_by_variant: Mapping[str, Mapping[str, Sequence[str]]],
    implementation_source_digest: str,
    independent_evidence: v1.IndependentGateEvidence,
    citation_evidence_by_variant: Optional[Mapping[str, Any]] = None,
) -> v1.EvaluationResult:
    _require_receipt(preregistration)
    if set(rankings_by_variant) != set(v1.VARIANTS):
        raise v1.Step98ContractError("complete variant rankings are required")
    query_ids = {query["query_id"] for query in preregistration.queries}
    scores: Dict[str, v1.VariantScore] = {}
    for variant in v1.VARIANTS:
        if set(rankings_by_variant[variant]) != query_ids:
            raise v1.Step98ContractError("ranking query ids mismatch")
        scores[variant] = v1._score_variant(
            preregistration,
            variant,
            rankings_by_variant[variant],
        )
    decision = _apply_gate(
        preregistration,
        scores,
        independent_evidence,
        citation_evidence_by_variant,
    )
    result_body = {
        "experiment_id": EXPERIMENT_ID,
        "manifest_digest": preregistration.manifest_digest,
        "capture_digest": capture_digest,
        "scoring_version": preregistration.manifest["scoring"]["version"],
        "implementation_source_digest": implementation_source_digest,
        "variant_scores": {key: asdict(value) for key, value in sorted(scores.items())},
        "gate": asdict(decision),
        "independent_evidence": asdict(independent_evidence),
        "citation_evidence_by_variant": {
            key: asdict(value) for key, value in sorted((citation_evidence_by_variant or {}).items())
        },
    }
    return v1.EvaluationResult(
        experiment_id=EXPERIMENT_ID,
        manifest_digest=preregistration.manifest_digest,
        capture_digest=capture_digest,
        scoring_version=preregistration.manifest["scoring"]["version"],
        implementation_source_digest=implementation_source_digest,
        variant_scores=scores,
        gate=decision,
        result_digest=v1.canonical_digest(result_body),
    )


def _heading_is_no_worse_than_title(scores: Mapping[str, v1.VariantScore]) -> bool:
    title = scores[v1.TITLE_BODY_VERSION]
    heading = scores[v1.TITLE_HEADING_BODY_VERSION]
    for name in title.critical_cohorts:
        title_summary = title.critical_cohorts[name]
        heading_summary = heading.critical_cohorts[name]
        if heading_summary.hit_at_3 < title_summary.hit_at_3:
            return False
        if heading_summary.mrr_at_5 < title_summary.mrr_at_5:
            return False
    return True


def _apply_gate(
    preregistration: v1.Preregistration,
    scores: Mapping[str, v1.VariantScore],
    evidence: v1.IndependentGateEvidence,
    citation_evidence_by_variant: Optional[Mapping[str, Any]],
) -> v1.GateDecision:
    baseline = scores[v1.BODY_ONLY_VERSION]
    eligible: List[str] = []
    failures: List[str] = []
    for variant in (v1.TITLE_BODY_VERSION, v1.TITLE_HEADING_BODY_VERSION):
        reasons = v1._candidate_failures(preregistration, baseline, scores[variant])
        citation = (citation_evidence_by_variant or {}).get(variant)
        if citation is not None and (
            citation.recall != 1.0
            or citation.precision != 1.0
            or citation.invalid_citation_count != 0
            or citation.derived_header_citation_count != 0
        ):
            reasons.append("CITATION_REGRESSION")
        if reasons:
            failures.extend(f"{variant}:{reason}" for reason in reasons)
        else:
            eligible.append(variant)
    if not eligible:
        return v1.GateDecision(
            status="no_adoption",
            selected_variant=None,
            reasons=tuple(failures),
        )
    evidence_failures = v1._independent_evidence_failures(evidence)
    if evidence_failures:
        return v1.GateDecision(
            status="inconclusive",
            selected_variant=None,
            reasons=tuple(evidence_failures),
        )
    selected = eligible[0]
    if len(eligible) == 2:
        heading_gain = (
            scores[v1.TITLE_HEADING_BODY_VERSION].overall.reciprocal_rank_sum
            - scores[v1.TITLE_BODY_VERSION].overall.reciprocal_rank_sum
        )
        threshold = float(
            preregistration.manifest["thresholds"]["heading_over_title_reciprocal_rank_gain"]
        )
        if heading_gain >= threshold and _heading_is_no_worse_than_title(scores):
            selected = v1.TITLE_HEADING_BODY_VERSION
    return v1.GateDecision(
        status="pass_candidate_identified",
        selected_variant=selected,
        reasons=(),
    )


def _builder_record(
    preregistration: v1.Preregistration,
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
    ordinal: int,
    role: str,
    variant_id: str,
    batch_ordinal: int,
    batch: Sequence[Tuple[str, str]],
) -> v1.CaptureRequest:
    return v1.CaptureRequest(
        ordinal=ordinal,
        role=role,
        variant_id=variant_id,
        batch_ordinal=batch_ordinal,
        item_ids=tuple(item[0] for item in batch),
        input_digests=tuple(hashlib.sha256(item[1].encode("utf-8")).hexdigest() for item in batch),
    )


def _require_receipt(preregistration: v1.Preregistration) -> None:
    receipt = preregistration.fixture_dir / "manifest.sha256"
    if not receipt.exists() or receipt.read_text(encoding="utf-8").strip() != preregistration.manifest_digest:
        raise v1.Step98ContractError("valid preregistration receipt required")


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 98 exp-002 deterministic contract")
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
