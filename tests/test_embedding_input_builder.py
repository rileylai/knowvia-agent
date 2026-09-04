from __future__ import annotations

from src.rag import (
    BODY_ONLY_VERSION,
    TITLE_BODY_VERSION,
    TITLE_HEADING_BODY_VERSION,
    EmbeddingInputRecord,
    HeadingSource,
    build_document_embedding_input,
    sha256_text,
)


def _record(*, body: str = "Original body", title: str = "Useful Page") -> EmbeddingInputRecord:
    return EmbeddingInputRecord(
        experiment_id="step98-exp-001",
        manifest_digest="manifest-digest",
        source_snapshot_digest="source-digest",
        chunk_id="chunk-1",
        chunk_record_digest="chunk-digest",
        chunk_text=body,
        page_title_source_id="title-page-1",
        page_title=title,
        headings=(
            HeadingSource(source_id="heading-root", text="Knowledge Base"),
            HeadingSource(source_id="heading-nearest", text="Useful Section"),
        ),
    )


def test_body_only_preserves_exact_bytes_and_provenance() -> None:
    record = _record(body=" Original\nbody ")
    built = build_document_embedding_input(
        record,
        variant_id=BODY_ONLY_VERSION,
        implementation_source_digest="source-code-digest",
    )

    assert built.text == record.chunk_text
    assert built.provenance.final_embedding_input_digest == sha256_text(record.chunk_text)
    assert built.provenance.title_included is False
    assert built.provenance.title_omission_reason == "VARIANT_EXCLUDES_TITLE"
    assert built.provenance.chunk_record_digest == "chunk-digest"


def test_title_and_heading_variants_use_frozen_serialization() -> None:
    record = _record()
    title = build_document_embedding_input(
        record,
        variant_id=TITLE_BODY_VERSION,
        implementation_source_digest="source-code-digest",
    )
    heading = build_document_embedding_input(
        record,
        variant_id=TITLE_HEADING_BODY_VERSION,
        implementation_source_digest="source-code-digest",
    )

    assert title.text == "Page title: Useful Page\n\nOriginal body"
    assert heading.text == (
        "Page title: Useful Page\nSection: Useful Section\n\nOriginal body"
    )
    assert heading.provenance.included_heading_source_ids == ("heading-nearest",)
    assert heading.provenance.excluded_headings[0].reason == "GENERIC"


def test_context_already_present_as_body_line_is_omitted() -> None:
    built = build_document_embedding_input(
        _record(body="Useful Page\nUseful Section\nOriginal body"),
        variant_id=TITLE_HEADING_BODY_VERSION,
        implementation_source_digest="source-code-digest",
    )

    assert built.text == "Useful Page\nUseful Section\nOriginal body"
    assert built.provenance.title_omission_reason == "DUPLICATE_BODY_LINE"
    assert [item.reason for item in built.provenance.excluded_headings] == [
        "GENERIC",
        "DUPLICATE_BODY_LINE",
    ]


def test_generic_title_and_heading_noise_degenerate_to_body() -> None:
    built = build_document_embedding_input(
        _record(title="首頁", body="Body"),
        variant_id=TITLE_BODY_VERSION,
        implementation_source_digest="source-code-digest",
    )

    assert built.text == "Body"
    assert built.provenance.title_omission_reason == "GENERIC"
