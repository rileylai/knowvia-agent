from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.rag.embedding_input_builder import (
    BODY_ONLY_VERSION,
    DEDUP_VERSION,
    DENYLIST_VERSION,
    DIGEST_VERSION,
    NORMALIZATION_VERSION,
    PROVENANCE_VERSION,
    QUERY_BUILDER_VERSION,
    SERIALIZER_VERSION,
    TITLE_BODY_VERSION,
    TITLE_HEADING_BODY_VERSION,
)

EXPERIMENT_ID = "step98-exp-001"
FIXTURE_DIR = REPO_ROOT / "tests" / "evals" / "fixtures" / "step_98" / EXPERIMENT_ID
INTENTS = ("title_associated", "body_only", "ambiguous")
LANGUAGES = ("zh_tw", "en", "mixed")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def language_text(language: str, index: int) -> tuple[str, str, str]:
    if language == "zh_tw":
        return (
            f"檢索脈絡主題 {index}",
            f"這段內容說明第 {index} 個可驗證概念與實際使用限制。",
            f"如何理解第 {index} 個概念的用途與限制？",
        )
    if language == "en":
        return (
            f"Retrieval Context Topic {index}",
            f"This note explains verifiable concept {index} and its practical constraint.",
            f"How should concept {index} be understood and constrained?",
        )
    return (
        f"檢索 Context Topic {index}",
        f"這段 note explains concept {index} 與 practical constraint。",
        f"Concept {index} 的用途與 constraint 是什麼？",
    )


def sized_body(base: str, bucket: str) -> str:
    if bucket == "short":
        return base[:110]
    if bucket == "long":
        return (base + " Supporting detail remains public and synthetic.") * 20
    return (base + " Supporting detail remains public and synthetic.") * 5


def main() -> None:
    receipt = FIXTURE_DIR / "manifest.sha256"
    if receipt.exists():
        raise SystemExit("Refusing to regenerate a frozen Step 98 fixture")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    sources: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    primary: dict[str, list[str]] = {}
    critical = {
        "title_only_semantic": [],
        "body_only": [],
        "traditional_chinese": [],
        "english": [],
        "mixed_language": [],
        "ambiguous": [],
    }
    secondary = {
        "exact_title_lookup": [],
        "short": [],
        "standard_length": [],
        "long": [],
        "deduplication": [],
        "generic_noise": [],
    }

    global_query_index = 0
    page_index = 0
    for intent in INTENTS:
        for language in LANGUAGES:
            cell = f"{intent}__{language}"
            primary[cell] = []
            page_ids = [f"page-{intent}-{language}-{number}" for number in (1, 2)]
            page_chunk_ids: dict[str, list[str]] = {}
            for page_number, page_id in enumerate(page_ids, start=1):
                page_index += 1
                title, _, _ = language_text(language, page_index)
                if intent == "ambiguous":
                    title = f"{title} Page {page_number}"
                sources.append(
                    {
                        "page_id": page_id,
                        "title_source_id": f"title-{page_id}",
                        "title": title,
                        "notion_path": f"Synthetic/{language}/{title}",
                    }
                )
                page_chunk_ids[page_id] = []
                for chunk_number in range(1, 7):
                    chunk_id = f"chunk-{intent}-{language}-{page_number}-{chunk_number}"
                    heading = "Shared Section" if intent == "ambiguous" else f"Section {chunk_number}"
                    chunk = {
                        "chunk_id": chunk_id,
                        "page_id": page_id,
                        "chunk_index": chunk_number - 1,
                        "chunk_text": f"Synthetic evidence {intent} {language} page {page_number} chunk {chunk_number}.",
                        "notion_path": f"Synthetic/{language}/{title}/{heading}/{chunk_number}",
                        "headings": [
                            {"source_id": f"heading-root-{page_id}", "text": "Knowledge Base"},
                            {"source_id": f"heading-{page_id}-{chunk_number}", "text": heading},
                        ],
                        "source_kind": "notion",
                    }
                    chunk["record_digest"] = digest(chunk)
                    chunks.append(chunk)
                    page_chunk_ids[page_id].append(chunk_id)

            for local_index in range(8):
                page_id = page_ids[local_index // 4]
                other_page_id = page_ids[1 - (local_index // 4)]
                target_position = local_index % 4
                target_id = page_chunk_ids[page_id][target_position]
                support_id = page_chunk_ids[page_id][4]
                wrong_page_id = page_chunk_ids[other_page_id][target_position]
                second_negative_id = page_chunk_ids[other_page_id][5]
                bucket = "short" if global_query_index < 18 else "standard_length" if global_query_index < 54 else "long"
                title, base_body, natural_query = language_text(language, page_index - 1 + (local_index // 4))
                target_chunk = next(item for item in chunks if item["chunk_id"] == target_id)
                target_chunk["chunk_text"] = sized_body(base_body, bucket)
                target_chunk["record_digest"] = digest({key: value for key, value in target_chunk.items() if key != "record_digest"})

                query_id = f"q-{intent}-{language}-{local_index + 1:02d}"
                exact_title = intent == "title_associated" and local_index in {3, 7}
                query_text = title if exact_title else natural_query
                query = {
                    "query_id": query_id,
                    "query": query_text,
                    "primary_cell": cell,
                    "critical_cohorts": [
                        "body_only" if intent == "body_only" else "ambiguous" if intent == "ambiguous" else "title_only_semantic",
                        "traditional_chinese" if language == "zh_tw" else "english" if language == "en" else "mixed_language",
                    ],
                    "secondary_tags": (["exact_title_lookup"] if exact_title else []),
                    "length_bucket": bucket,
                    "relevance": {target_id: 2, support_id: 1, wrong_page_id: 0, second_negative_id: 0},
                    "required_citation_paths": [target_chunk["notion_path"]],
                    "allowed_citation_paths": [target_chunk["notion_path"], next(item for item in chunks if item["chunk_id"] == support_id)["notion_path"]],
                    "hard_negative_chunk_ids": [wrong_page_id, second_negative_id],
                    "wrong_page_chunk_ids": [wrong_page_id, second_negative_id] if intent == "ambiguous" else [],
                    "top_k": 5,
                }
                if intent == "title_associated" and not exact_title:
                    critical["title_only_semantic"].append(query_id)
                if intent == "body_only":
                    critical["body_only"].append(query_id)
                if intent == "ambiguous":
                    critical["ambiguous"].append(query_id)
                critical["traditional_chinese" if language == "zh_tw" else "english" if language == "en" else "mixed_language"].append(query_id)
                primary[cell].append(query_id)
                secondary[bucket].append(query_id)
                if exact_title:
                    secondary["exact_title_lookup"].append(query_id)
                if local_index == 0:
                    secondary["deduplication"].append(query_id)
                    secondary["generic_noise"].append(query_id)
                queries.append(query)
                global_query_index += 1

    source_payload = {"sources": sources}
    chunk_payload = {"chunks": chunks}
    query_payload = {"queries": queries}
    dump(FIXTURE_DIR / "source_records.yaml", source_payload)
    dump(FIXTURE_DIR / "chunks.yaml", chunk_payload)
    dump(FIXTURE_DIR / "queries.yaml", query_payload)

    builder_path = REPO_ROOT / "src" / "rag" / "embedding_input_builder.py"
    evaluator_path = REPO_ROOT / "tests" / "evals" / "context_aware_embedding_input_eval.py"
    capture_path = REPO_ROOT / "tests" / "evals" / "step98_phase_b_capture.py"
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "schema_version": "step98_manifest_v1",
        "source_snapshot_digest": digest(source_payload),
        "file_digests": {
            "source_records.yaml": file_digest(FIXTURE_DIR / "source_records.yaml"),
            "chunks.yaml": file_digest(FIXTURE_DIR / "chunks.yaml"),
            "queries.yaml": file_digest(FIXTURE_DIR / "queries.yaml"),
        },
        "counts": {"pages": 18, "chunks": 108, "queries": 72, "hard_negative_pairs": 144},
        "memberships": {
            "primary_cells": primary,
            "critical_cohorts": critical,
            "secondary_cohorts": secondary,
        },
        "builders": {
            "document": [BODY_ONLY_VERSION, TITLE_BODY_VERSION, TITLE_HEADING_BODY_VERSION],
            "query": QUERY_BUILDER_VERSION,
            "normalization": NORMALIZATION_VERSION,
            "denylist": DENYLIST_VERSION,
            "deduplication": DEDUP_VERSION,
            "serializer": SERIALIZER_VERSION,
            "provenance": PROVENANCE_VERSION,
            "digest": DIGEST_VERSION,
        },
        "implementation": {
            "builder_source_path": "src/rag/embedding_input_builder.py",
            "builder_source_digest": file_digest(builder_path),
            "scoring_source_path": "tests/evals/context_aware_embedding_input_eval.py",
            "scoring_source_digest": file_digest(evaluator_path),
            "capture_source_path": "tests/evals/step98_phase_b_capture.py",
            "capture_source_digest": file_digest(capture_path),
        },
        "embedding": {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536, "distance": "cosine", "revision_policy": "single_capture_session_if_revision_unavailable"},
        "retrieval": {"top_k": [1, 3, 5], "tie_break": "similarity_desc_chunk_id_asc"},
        "scoring": {"version": "step98_scoring_v1", "precision": 12, "tolerance": "1e-12"},
        "capture": {"batch_size": 32, "concurrency": 1, "schedule": "query_once_then_document_round_robin_v1", "max_requests": 16, "max_inputs": 396, "max_estimated_tokens": 250000, "max_duration_seconds": 600, "max_cost_usd": "0.010000", "vector_retention_required": True},
        "thresholds": {"overall_hit3_gains": 3, "overall_hit3_losses": 0, "overall_reciprocal_rank_gain": "3.600", "title_semantic_hit3_gains": 2, "ambiguity_new_errors": 0, "heading_resolved_errors": 2, "heading_ambiguity_reciprocal_rank_gain": "1.200", "heading_over_title_reciprocal_rank_gain": "1.440"},
        "citation_contract": {"version": "step98_citation_projection_v1", "recall": "1.000", "precision": "1.000", "invalid_count": 0, "derived_header_count": 0, "conformance_fixture_count": 8},
        "production_safety_contract": {"version": "step98_production_safety_v1", "case_count": 10, "decoy_types": ["pending", "rejected", "non_notion", "wrong_page", "wrong_section"], "verify_eligible_set_before_top_k": True},
    }
    dump(FIXTURE_DIR / "manifest.yaml", manifest)


if __name__ == "__main__":
    main()
