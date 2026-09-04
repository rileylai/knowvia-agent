from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from src.orchestrators import SupplementProposalSchema, SupplementProposalValidationError
from src.services.screenshot_quality import (
    validate_screenshot_proposal,
    validate_screenshot_proposal_with_diagnostics,
    validate_screenshot_proposal_with_title_fallback,
    preprocess_screenshot_ocr_text,
)


SOURCE = (
    "Index supports the query access path. EXPLAIN shows the execution plan. "
    "Query rewrite changes the query form. Keyset pagination uses a stable cursor."
)
CONCEPTS = ["Index", "EXPLAIN", "execution plan", "query rewrite", "pagination"]
NOTES = [
    "Index supports the query access path.",
    "EXPLAIN shows the execution plan.",
    "Query rewrite changes the query form.",
    "Keyset pagination uses a stable cursor.",
]


def _proposal(*, summary: str, notes: list[str] | None = None) -> SupplementProposalSchema:
    return SupplementProposalSchema.model_validate(
        {
            "title": "Index EXPLAIN query rewrite pagination",
            "target_path": "Knowledge/Database/AI Supplement Zone",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "Public-safe SQL fixture",
            },
            "summary": summary,
            "concepts": CONCEPTS,
            "notes": notes or NOTES,
        }
    )


@pytest.mark.parametrize(
    "summary",
    [
        "Index supports the query access path.",
        "Index supports the query access path. EXPLAIN shows the execution plan.",
        (
            "Index supports the query access path. EXPLAIN shows the execution plan. "
            "Query rewrite changes the query form. Keyset pagination uses a stable cursor."
        ),
    ],
)
def test_grounded_summary_sentence_counts_are_soft_preferences(summary: str) -> None:
    result = validate_screenshot_proposal_with_diagnostics(
        proposal=_proposal(summary=summary),
        source_text=SOURCE,
    )

    assert result.diagnostics is not None
    assert result.diagnostics.summary_validation_unit_count == len(
        [part for part in summary.split(". ") if part]
    )


def test_summary_splitter_still_rejects_one_unsupported_sentence() -> None:
    proposal = _proposal(
        summary=(
            "Index supports the query access path. Redis improves performance."
        )
    )

    with pytest.raises(SupplementProposalValidationError) as exc_info:
        validate_screenshot_proposal(proposal=proposal, source_text=SOURCE)

    assert exc_info.value.field == "summary"
    assert exc_info.value.diagnostics["failed_summary_validation_unit_count"] == 1


def test_empty_and_oversized_summary_fail_through_resource_bounds() -> None:
    with pytest.raises(ValidationError):
        _proposal(summary="")

    oversized = _proposal(summary="Index supports the query access path.").model_copy(
        update={"summary": "Index " + ("query " * 500)}
    )
    with pytest.raises(SupplementProposalValidationError, match="character bound"):
        validate_screenshot_proposal(proposal=oversized, source_text=SOURCE)


def test_concept_coverage_and_duplicate_notes_are_deterministic() -> None:
    uncovered = _proposal(
        summary="Index supports the query access path.",
        notes=NOTES[1:],
    )
    with pytest.raises(SupplementProposalValidationError) as coverage_error:
        validate_screenshot_proposal(proposal=uncovered, source_text=SOURCE)
    assert coverage_error.value.field == "notes"
    assert coverage_error.value.diagnostics["uncovered_concept_count"] == 1
    assert coverage_error.value.diagnostics["body_repair_eligible"] is True

    duplicate = _proposal(
        summary="Index supports the query access path.",
        notes=[*NOTES, "Index supports the query access path."],
    )
    with pytest.raises(SupplementProposalValidationError, match="duplicate"):
        validate_screenshot_proposal(proposal=duplicate, source_text=SOURCE)


def test_bounded_engineering_context_is_tied_to_a_concept_and_fail_closed() -> None:
    enriched = _proposal(
        summary="Index supports the query access path.",
        notes=[
            "Index：Index supports the query access path. Practical application: enterprise systems evaluate query paths. Trade-off: read performance and write maintenance cost are trade-offs.",
            *NOTES[1:],
        ],
    )
    result = validate_screenshot_proposal_with_diagnostics(
        proposal=enriched,
        source_text=SOURCE,
    )
    assert result.diagnostics is not None
    assert result.diagnostics.notes_with_application_count == 1

    unsupported_product = enriched.model_copy(
        update={
            "notes": [
                "Index：Index supports the query access path. Practical application: PostgreSQL handles the query.",
                *NOTES[1:],
            ]
        }
    )
    with pytest.raises(SupplementProposalValidationError):
        validate_screenshot_proposal(proposal=unsupported_product, source_text=SOURCE)


def test_title_fallback_decision_is_source_anchored_and_revalidated() -> None:
    mixed_title = _proposal(summary="Index supports the query access path.").model_copy(
        update={"title": "Index EXPLAIN Redis"}
    )
    recovered = validate_screenshot_proposal_with_title_fallback(
        proposal=mixed_title,
        source_text=SOURCE,
    )
    assert recovered.title_fallback_used is True
    assert "Redis" not in recovered.proposal.title
    validate_screenshot_proposal(proposal=recovered.proposal, source_text=SOURCE)

    unsupported_only = mixed_title.model_copy(update={"title": "Redis"})
    with pytest.raises(SupplementProposalValidationError):
        validate_screenshot_proposal_with_title_fallback(
            proposal=unsupported_only,
            source_text=SOURCE,
        )


def test_note_count_has_bounded_maximum_without_forcing_filler() -> None:
    small = _proposal(summary="Index supports the query access path.", notes=[NOTES[0]]).model_copy(
        update={"concepts": ["Index", "query access", "path"]}
    )
    validate_screenshot_proposal(proposal=small, source_text=SOURCE)

    too_many = small.model_copy(update={"notes": NOTES * 4})
    with pytest.raises(ValidationError):
        SupplementProposalSchema.model_validate(too_many.model_dump())


def test_public_safe_live_shaped_sql_fixture_covers_title_recovery_and_notes() -> None:
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "screenshot_proposal_fixtures.json"
    fixture = next(
        item
        for item in json.loads(fixture_path.read_text(encoding="utf-8"))
        if item["id"] == "public_safe_live_shaped_sql_context_recovery"
    )
    source = "\n".join(
        preprocess_screenshot_ocr_text(image["ocr"])
        for image in sorted(fixture["images"], key=lambda image: image["message_id"])
    )

    with pytest.raises(SupplementProposalValidationError) as initial_error:
        validate_screenshot_proposal(
            proposal=SupplementProposalSchema.model_validate(fixture["initial_proposal"]),
            source_text=source,
        )
    assert initial_error.value.diagnostics["title_failure_reason"] == (
        "UNMATCHED_PRODUCT_NAME"
    )

    repaired = validate_screenshot_proposal_with_diagnostics(
        proposal=SupplementProposalSchema.model_validate(fixture["repaired_proposal"]),
        source_text=source,
    )
    assert repaired.diagnostics is not None
    assert repaired.diagnostics.covered_concept_count == 5
    assert repaired.diagnostics.note_count == 4
    assert repaired.diagnostics.notes_with_application_count == 4
