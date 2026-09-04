from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Tuple


BODY_ONLY_VERSION = "body_only_v1"
TITLE_BODY_VERSION = "title_body_v1"
TITLE_HEADING_BODY_VERSION = "title_heading_body_v1"
QUERY_BUILDER_VERSION = "query_body_only_v1"
NORMALIZATION_VERSION = "nfkc_casefold_whitespace_punctuation_v1"
DENYLIST_VERSION = "generic_context_denylist_v1"
DEDUP_VERSION = "exact_normalized_body_line_v1"
SERIALIZER_VERSION = "context_labels_newlines_v1"
PROVENANCE_VERSION = "embedding_input_provenance_v1"
DIGEST_VERSION = "sha256_utf8_v1"

_GENERIC_KEYS = frozenset(
    {
        "root",
        "home",
        "workspace",
        "knowledge base",
        "knowledge",
        "notes",
        "untitled",
        "根目錄",
        "首頁",
        "工作區",
        "知識庫",
        "知識",
        "筆記",
        "未命名",
    }
)
_EDGE_PUNCTUATION = re.compile(r"^[\W_]+|[\W_]+$")


@dataclass(frozen=True)
class HeadingSource:
    source_id: str
    text: str


@dataclass(frozen=True)
class EmbeddingInputRecord:
    experiment_id: str
    manifest_digest: str
    source_snapshot_digest: str
    chunk_id: str
    chunk_record_digest: str
    chunk_text: str
    page_title_source_id: str
    page_title: str
    headings: Tuple[HeadingSource, ...]


@dataclass(frozen=True)
class ExcludedContext:
    source_id: str
    reason: str


@dataclass(frozen=True)
class EmbeddingInputProvenance:
    experiment_id: str
    manifest_digest: str
    source_snapshot_digest: str
    chunk_id: str
    chunk_record_digest: str
    page_title_source_id: str
    title_included: bool
    title_omission_reason: Optional[str]
    structural_heading_source_ids: Tuple[str, ...]
    excluded_headings: Tuple[ExcludedContext, ...]
    included_heading_source_ids: Tuple[str, ...]
    variant_id: str
    role: str
    builder_version: str
    normalization_version: str
    denylist_version: str
    dedup_version: str
    serializer_version: str
    provenance_version: str
    digest_version: str
    implementation_source_digest: str
    final_embedding_input_digest: str


@dataclass(frozen=True)
class BuiltEmbeddingInput:
    text: str
    provenance: EmbeddingInputProvenance


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_document_embedding_input(
    record: EmbeddingInputRecord,
    *,
    variant_id: str,
    implementation_source_digest: str,
) -> BuiltEmbeddingInput:
    if variant_id not in {
        BODY_ONLY_VERSION,
        TITLE_BODY_VERSION,
        TITLE_HEADING_BODY_VERSION,
    }:
        raise ValueError("Unsupported embedding input variant")

    body_lines = {_comparison_key(line) for line in record.chunk_text.splitlines()}
    body_lines.discard("")
    title_text = _serialize_context(record.page_title)
    title_key = _comparison_key(title_text)
    title_included = variant_id != BODY_ONLY_VERSION
    title_reason: Optional[str] = None
    if not title_included:
        title_reason = "VARIANT_EXCLUDES_TITLE"
    elif not title_key:
        title_included = False
        title_reason = "EMPTY"
    elif title_key in _GENERIC_KEYS:
        title_included = False
        title_reason = "GENERIC"
    elif title_key in body_lines:
        title_included = False
        title_reason = "DUPLICATE_BODY_LINE"

    included_headings: list[HeadingSource] = []
    excluded_headings: list[ExcludedContext] = []
    previous_key: Optional[str] = None
    for heading in record.headings:
        heading_text = _serialize_context(heading.text)
        heading_key = _comparison_key(heading_text)
        reason: Optional[str] = None
        if variant_id != TITLE_HEADING_BODY_VERSION:
            reason = "VARIANT_EXCLUDES_HEADING"
        elif not heading_key:
            reason = "EMPTY"
        elif heading_key in _GENERIC_KEYS:
            reason = "GENERIC"
        elif heading_key in body_lines:
            reason = "DUPLICATE_BODY_LINE"
        elif previous_key == heading_key:
            reason = "DUPLICATE_ADJACENT_HEADING"
        elif title_included and heading_key == title_key:
            reason = "DUPLICATE_INCLUDED_TITLE"
        if reason is None:
            included_headings.append(
                HeadingSource(source_id=heading.source_id, text=heading_text)
            )
            previous_key = heading_key
        else:
            excluded_headings.append(
                ExcludedContext(source_id=heading.source_id, reason=reason)
            )

    header_lines: list[str] = []
    if title_included:
        header_lines.append(f"Page title: {title_text}")
    if included_headings:
        breadcrumb = " > ".join(heading.text for heading in included_headings)
        header_lines.append(f"Section: {breadcrumb}")
    text = record.chunk_text
    if header_lines:
        text = "\n".join(header_lines) + "\n\n" + record.chunk_text

    return BuiltEmbeddingInput(
        text=text,
        provenance=EmbeddingInputProvenance(
            experiment_id=record.experiment_id,
            manifest_digest=record.manifest_digest,
            source_snapshot_digest=record.source_snapshot_digest,
            chunk_id=record.chunk_id,
            chunk_record_digest=record.chunk_record_digest,
            page_title_source_id=record.page_title_source_id,
            title_included=title_included,
            title_omission_reason=title_reason,
            structural_heading_source_ids=tuple(
                heading.source_id for heading in record.headings
            ),
            excluded_headings=tuple(excluded_headings),
            included_heading_source_ids=tuple(
                heading.source_id for heading in included_headings
            ),
            variant_id=variant_id,
            role="document",
            builder_version=variant_id,
            normalization_version=NORMALIZATION_VERSION,
            denylist_version=DENYLIST_VERSION,
            dedup_version=DEDUP_VERSION,
            serializer_version=SERIALIZER_VERSION,
            provenance_version=PROVENANCE_VERSION,
            digest_version=DIGEST_VERSION,
            implementation_source_digest=implementation_source_digest,
            final_embedding_input_digest=sha256_text(text),
        ),
    )


def build_query_embedding_input(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must not be empty")
    return query


def _serialize_context(value: str) -> str:
    return " ".join(value.split())


def _comparison_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = " ".join(normalized.split())
    return _EDGE_PUNCTUATION.sub("", normalized).strip()
