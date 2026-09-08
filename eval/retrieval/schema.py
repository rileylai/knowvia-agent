from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml


class BenchmarkSchemaError(ValueError):
    """Raised when the controlled retrieval benchmark is malformed."""


@dataclass(frozen=True)
class CorpusSource:
    source_id: str
    filename: str
    sha256: str
    page_count: int


@dataclass(frozen=True)
class RetrievalContract:
    chunk_max_chars: int
    chunk_overlap_chars: int
    page_aware: bool
    embedding_model: str
    embedding_dimensions: int
    similarity: str
    candidate_pool_max: int
    relevance_floor: float
    top_k: tuple[int, ...]


@dataclass(frozen=True)
class EvidenceLocation:
    source_id: str
    pages: tuple[int, ...]
    anchors: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceGroup:
    group_id: str
    locations: tuple[EvidenceLocation, ...]
    match: str = "any"


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    category: str
    query: str
    answerable: bool
    evidence_groups: tuple[EvidenceGroup, ...]
    notes: Optional[str] = None


@dataclass(frozen=True)
class RetrievalBenchmark:
    benchmark_id: str
    version: str
    contract: RetrievalContract
    corpus: tuple[CorpusSource, ...]
    cases: tuple[BenchmarkCase, ...]


def load_benchmark(path: Path) -> RetrievalBenchmark:
    """Load and validate a retrieval benchmark YAML file."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BenchmarkSchemaError(f"benchmark file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise BenchmarkSchemaError(f"invalid benchmark YAML: {path}") from exc

    if not isinstance(payload, Mapping):
        raise BenchmarkSchemaError("benchmark root must be a mapping")

    benchmark_id = _required_string(payload, "benchmark_id", "root")
    version = _required_string(payload, "version", "root")
    contract = _parse_contract(payload.get("contract"))
    corpus = _parse_corpus(payload.get("corpus"))
    corpus_ids = {source.source_id for source in corpus}
    cases = _parse_cases(payload.get("cases"), corpus_ids, corpus)
    return RetrievalBenchmark(
        benchmark_id=benchmark_id,
        version=version,
        contract=contract,
        corpus=tuple(corpus),
        cases=tuple(cases),
    )


def _parse_corpus(value: Any) -> list[CorpusSource]:
    if not isinstance(value, list) or not value:
        raise BenchmarkSchemaError("corpus must be a non-empty list")
    parsed: list[CorpusSource] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        context = f"corpus[{index}]"
        if not isinstance(item, Mapping):
            raise BenchmarkSchemaError(f"{context} must be a mapping")
        source_id = _required_string(item, "source_id", context)
        if source_id in seen:
            raise BenchmarkSchemaError(f"duplicate source_id: {source_id}")
        seen.add(source_id)
        filename = _required_string(item, "filename", context)
        sha256 = _required_string(item, "sha256", context).lower()
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise BenchmarkSchemaError(f"{context}.sha256 must be a SHA256 hex digest")
        page_count = _positive_int(item.get("page_count"), f"{context}.page_count")
        parsed.append(
            CorpusSource(
                source_id=source_id,
                filename=filename,
                sha256=sha256,
                page_count=page_count,
            )
        )
    return parsed


def _parse_contract(value: Any) -> RetrievalContract:
    if not isinstance(value, Mapping):
        raise BenchmarkSchemaError("contract must be a mapping")
    chunk_max_chars = _positive_int(value.get("chunk_max_chars"), "contract.chunk_max_chars")
    chunk_overlap_chars = value.get("chunk_overlap_chars")
    if (
        isinstance(chunk_overlap_chars, bool)
        or not isinstance(chunk_overlap_chars, int)
        or chunk_overlap_chars < 0
    ):
        raise BenchmarkSchemaError("contract.chunk_overlap_chars must be a non-negative integer")
    page_aware = _required_bool(value.get("page_aware"), "contract.page_aware")
    embedding_model = _required_string(value, "embedding_model", "contract")
    embedding_dimensions = _positive_int(
        value.get("embedding_dimensions"), "contract.embedding_dimensions"
    )
    similarity = _required_string(value, "similarity", "contract")
    candidate_pool_max = _positive_int(
        value.get("candidate_pool_max"), "contract.candidate_pool_max"
    )
    relevance_floor = value.get("relevance_floor")
    if isinstance(relevance_floor, bool) or not isinstance(relevance_floor, (int, float)):
        raise BenchmarkSchemaError("contract.relevance_floor must be a number")
    if relevance_floor < 0 or relevance_floor > 1:
        raise BenchmarkSchemaError("contract.relevance_floor must be between 0 and 1")
    top_k = tuple(_positive_int_list(value.get("top_k"), "contract.top_k"))
    if top_k != (1, 3, 5):
        raise BenchmarkSchemaError("contract.top_k must be [1, 3, 5]")
    return RetrievalContract(
        chunk_max_chars=chunk_max_chars,
        chunk_overlap_chars=chunk_overlap_chars,
        page_aware=page_aware,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        similarity=similarity,
        candidate_pool_max=candidate_pool_max,
        relevance_floor=float(relevance_floor),
        top_k=top_k,
    )


def _parse_cases(
    value: Any,
    corpus_ids: set[str],
    corpus: list[CorpusSource],
) -> list[BenchmarkCase]:
    if not isinstance(value, list) or not value:
        raise BenchmarkSchemaError("cases must be a non-empty list")
    page_counts = {source.source_id: source.page_count for source in corpus}
    parsed: list[BenchmarkCase] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        context = f"cases[{index}]"
        if not isinstance(item, Mapping):
            raise BenchmarkSchemaError(f"{context} must be a mapping")
        case_id = _required_string(item, "id", context)
        if case_id in seen:
            raise BenchmarkSchemaError(f"duplicate case id: {case_id}")
        seen.add(case_id)
        category = _required_string(item, "category", context)
        query = _required_string(item, "query", context)
        answerable = _required_bool(item.get("answerable"), f"{context}.answerable")
        groups = _parse_evidence_groups(
            item.get("evidence_groups"),
            context,
            corpus_ids,
            page_counts,
        )
        if answerable and not groups:
            raise BenchmarkSchemaError(f"{context} answerable case needs evidence_groups")
        if not answerable and groups:
            raise BenchmarkSchemaError(f"{context} negative case must not have evidence_groups")
        notes = item.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise BenchmarkSchemaError(f"{context}.notes must be a string")
        parsed.append(
            BenchmarkCase(
                case_id=case_id,
                category=category,
                query=query,
                answerable=answerable,
                evidence_groups=tuple(groups),
                notes=notes,
            )
        )
    return parsed


def _parse_evidence_groups(
    value: Any,
    context: str,
    corpus_ids: set[str],
    page_counts: dict[str, int],
) -> list[EvidenceGroup]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BenchmarkSchemaError(f"{context}.evidence_groups must be a list")
    parsed: list[EvidenceGroup] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        group_context = f"{context}.evidence_groups[{index}]"
        if not isinstance(item, Mapping):
            raise BenchmarkSchemaError(f"{group_context} must be a mapping")
        group_id = _required_string(item, "group_id", group_context)
        if group_id in seen:
            raise BenchmarkSchemaError(f"duplicate evidence group id: {group_id}")
        seen.add(group_id)
        match = item.get("match", "any")
        if match not in {"any", "all"}:
            raise BenchmarkSchemaError(f"{group_context}.match must be any or all")
        locations = item.get("locations")
        if not isinstance(locations, list) or not locations:
            raise BenchmarkSchemaError(f"{group_context}.locations must be non-empty")
        parsed_locations: list[EvidenceLocation] = []
        for location_index, location in enumerate(locations):
            location_context = f"{group_context}.locations[{location_index}]"
            if not isinstance(location, Mapping):
                raise BenchmarkSchemaError(f"{location_context} must be a mapping")
            source_id = _required_string(location, "source_id", location_context)
            if source_id not in corpus_ids:
                raise BenchmarkSchemaError(
                    f"{location_context}.source_id is not in corpus: {source_id}"
                )
            pages = _positive_int_list(location.get("pages"), f"{location_context}.pages")
            invalid_pages = [page for page in pages if page > page_counts[source_id]]
            if invalid_pages:
                raise BenchmarkSchemaError(
                    f"{location_context}.pages out of range: {invalid_pages}"
                )
            anchors = location.get("anchors")
            if not isinstance(anchors, list) or not anchors or not all(
                isinstance(anchor, str) and anchor.strip() for anchor in anchors
            ):
                raise BenchmarkSchemaError(
                    f"{location_context}.anchors must contain non-empty strings"
                )
            parsed_locations.append(
                EvidenceLocation(
                    source_id=source_id,
                    pages=tuple(pages),
                    anchors=tuple(anchor.strip() for anchor in anchors),
                )
            )
        parsed.append(
            EvidenceGroup(
                group_id=group_id,
                locations=tuple(parsed_locations),
                match=match,
            )
        )
    return parsed


def _required_string(value: Mapping[str, Any], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise BenchmarkSchemaError(f"{context}.{key} must be a non-empty string")
    return item.strip()


def _required_bool(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkSchemaError(f"{context} must be a boolean")
    return value


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BenchmarkSchemaError(f"{context} must be a positive integer")
    return value


def _positive_int_list(value: Any, context: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise BenchmarkSchemaError(f"{context} must be a non-empty list")
    return [_positive_int(item, f"{context}[{index}]") for index, item in enumerate(value)]
