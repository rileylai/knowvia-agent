from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.orchestrators import (
    SupplementProposalGeneratedSchema,
    SupplementProposalValidationError,
    build_deterministic_supplement_source,
    merge_generated_supplement_proposal,
    parse_supplement_generated_json,
    parse_supplement_body_repair_json,
    parse_supplement_proposal_json,
    parse_supplement_title_repair_json,
)


def test_parse_supplement_body_repair_json_accepts_bounded_body() -> None:
    parsed = parse_supplement_body_repair_json(
        """
        {
          "summary": "MySQL EXPLAIN summary.",
          "concepts": ["MySQL", "EXPLAIN", "SQL"],
          "notes": ["MySQL note.", "EXPLAIN note.", "SQL note."]
        }
        """
    )

    assert parsed.summary == "MySQL EXPLAIN summary."
    assert parsed.concepts == ["MySQL", "EXPLAIN", "SQL"]


def test_parse_supplement_body_repair_json_rejects_extra_or_short_lists() -> None:
    with pytest.raises(SupplementProposalValidationError) as exc_info:
        parse_supplement_body_repair_json(
            """
            {
              "summary": "MySQL EXPLAIN summary.",
              "concepts": ["MySQL", "EXPLAIN"],
              "notes": ["one", "two", "three"],
              "title": "not allowed"
            }
            """
        )

    assert exc_info.value.field == "body"


def test_parse_supplement_proposal_json_accepts_valid_object() -> None:
    parsed = parse_supplement_proposal_json(
        """
        {
          "title": "Attention recap from source",
          "target_path": "Knowledge/NLP/Week5/AI Supplement Zone/Attention",
          "source": {
            "source_type": "pdf",
            "source_display_name": "lecture1.pdf"
          },
          "summary": "Summarizes scaled dot-product attention.",
          "concepts": ["scaled dot-product attention", "softmax weighting"],
          "notes": ["Connect this with week4 self-attention notes."]
        }
        """
    )

    assert parsed.title == "Attention recap from source"
    assert parsed.target_path == "Knowledge/NLP/Week5/AI Supplement Zone/Attention"
    assert parsed.source.source_type == "pdf"
    assert parsed.source.source_display_name == "lecture1.pdf"
    assert parsed.summary == "Summarizes scaled dot-product attention."
    assert parsed.concepts == [
        "scaled dot-product attention",
        "softmax weighting",
    ]
    assert parsed.notes == ["Connect this with week4 self-attention notes."]


def test_parse_supplement_proposal_json_supports_markdown_json_fence() -> None:
    parsed = parse_supplement_proposal_json(
        """```json
        {
          "title": "Week 5 concept patch",
          "target_path": "Knowledge/NLP/Week5/AI Supplement Zone",
          "source": {
            "source_type": "url",
            "source_display_name": "https://example.com/attention"
          },
          "summary": "Adds one concise clarification.",
          "concepts": ["attention pooling"],
          "notes": ["Review against existing week5 examples."]
        }
        ```"""
    )

    assert parsed.title == "Week 5 concept patch"
    assert parsed.source.source_type == "url"


def test_parse_supplement_proposal_json_rejects_invalid_json() -> None:
    with pytest.raises(SupplementProposalValidationError) as exc_info:
        parse_supplement_proposal_json("{not-valid-json}")

    assert exc_info.value.error_code == "LLM_OUTPUT_INVALID"
    assert exc_info.value.failure_reason == "LLM_OUTPUT_INVALID"
    assert "not valid JSON" in exc_info.value.message


def test_parse_supplement_proposal_json_rejects_missing_required_field() -> None:
    with pytest.raises(SupplementProposalValidationError) as exc_info:
        parse_supplement_proposal_json(
            """
            {
              "title": "Missing target path",
              "source": {
                "source_type": "chat_text",
                "source_display_name": "chat-2026-05-25"
              },
              "summary": "Summary text",
              "concepts": ["concept a"],
              "notes": ["note a"]
            }
            """
        )

    assert exc_info.value.error_code == "LLM_OUTPUT_INVALID"
    assert "target_path" in exc_info.value.message


def test_parse_supplement_proposal_json_rejects_non_string_list_item() -> None:
    with pytest.raises(SupplementProposalValidationError) as exc_info:
        parse_supplement_proposal_json(
            """
            {
              "title": "Type mismatch",
              "target_path": "Knowledge/NLP/Week5/AI Supplement Zone",
              "source": {
                "source_type": "screenshot",
                "source_display_name": "Screenshot batch (3 images)"
              },
              "summary": "Summary text",
              "concepts": ["concept a", 2],
              "notes": ["note a"]
            }
            """
        )

    assert exc_info.value.error_code == "LLM_OUTPUT_INVALID"
    assert "concepts" in exc_info.value.message


def test_provider_output_schema_ignores_only_legacy_backend_owned_fields() -> None:
    generated = parse_supplement_generated_json(
        json.dumps(
            {
                "title": "Generated title",
                "summary": "Grounded summary.",
                "concepts": ["Index"],
                "notes": ["Index supports the query access path."],
                "source": "Screenshot batch (7 images)",
                "target_path": "Fabricated target",
                "citations": [{"notion_path": "Fabricated path"}],
            }
        )
    )

    assert isinstance(generated, SupplementProposalGeneratedSchema)
    assert generated.model_dump() == {
        "title": "Generated title",
        "summary": "Grounded summary.",
        "concepts": ["Index"],
        "notes": ["Index supports the query access path."],
    }


def test_provider_output_schema_accepts_generated_fields_only() -> None:
    generated = parse_supplement_generated_json(
        json.dumps(
            {
                "title": "Generated title",
                "summary": "Grounded summary.",
                "concepts": ["Index"],
                "notes": ["Index supports the query access path."],
            }
        )
    )

    assert generated.title == "Generated title"
    assert generated.concepts == ["Index"]


def test_provider_output_unknown_fields_remain_strict() -> None:
    with pytest.raises(SupplementProposalValidationError) as exc_info:
        parse_supplement_generated_json(
            json.dumps(
                {
                    "title": "Generated title",
                    "summary": "Grounded summary.",
                    "concepts": ["Index"],
                    "notes": ["Index supports the query access path."],
                    "unowned_field": "must fail",
                }
            )
        )

    assert exc_info.value.failure_stage == "provider_output_validation"
    assert exc_info.value.field == "provider_output"


def test_public_safe_source_ownership_fixture_merges_canonical_source() -> None:
    fixture_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "supplement_source_ownership_regression.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    source_document = fixture["source_document"]

    with pytest.raises(SupplementProposalValidationError):
        parse_supplement_proposal_json(json.dumps(fixture["provider_output"]))

    generated = parse_supplement_generated_json(
        json.dumps(fixture["provider_output"])
    )
    canonical_source = build_deterministic_supplement_source(
        source_type=source_document["source_type"],
        source_display_name=source_document["source_display_name"],
    )
    final_proposal = merge_generated_supplement_proposal(
        generated=generated,
        source=canonical_source,
        target_path=fixture["expected_target_path"],
    )

    assert final_proposal.source.source_type == "screenshot"
    assert final_proposal.source.source_display_name == "Screenshot batch (7 images)"
    assert final_proposal.target_path == fixture["expected_target_path"]


def test_fabricated_structured_source_identity_and_target_cannot_override_backend() -> None:
    generated_payload = {
        "title": "Generated title",
        "summary": "Grounded summary.",
        "concepts": ["Index"],
        "notes": ["Index supports the query access path."],
        "source": {
            "source_type": "pdf",
            "source_display_name": "fabricated.pdf",
            "source_document_id": 999999,
        },
        "source_document_id": 999999,
        "source_attachment_count": 999,
        "target_path": "Fabricated target",
    }

    generated = parse_supplement_generated_json(json.dumps(generated_payload))
    final_proposal = merge_generated_supplement_proposal(
        generated=generated,
        source=build_deterministic_supplement_source(
            source_type="screenshot",
            source_display_name="Screenshot batch (7 images)",
        ),
        target_path="Knowledge/Database/AI Supplement Zone",
    )

    assert final_proposal.source.source_type == "screenshot"
    assert final_proposal.source.source_display_name == "Screenshot batch (7 images)"
    assert final_proposal.target_path == "Knowledge/Database/AI Supplement Zone"


@pytest.mark.parametrize(
    ("source_type", "source_display_name"),
    [
        ("pdf", "lecture.pdf"),
        ("url", "https://example.test/article"),
        ("youtube", "YouTube transcript (synthetic-video)"),
        ("chat_text", "chat-synthetic"),
    ],
)
def test_deterministic_source_renderer_supports_all_source_types(
    source_type: str,
    source_display_name: str,
) -> None:
    source = build_deterministic_supplement_source(
        source_type=source_type,
        source_display_name=source_display_name,
    )

    assert source.model_dump() == {
        "source_type": source_type,
        "source_display_name": source_display_name,
    }


def test_repair_schemas_do_not_accept_or_mutate_source() -> None:
    with pytest.raises(SupplementProposalValidationError):
        parse_supplement_title_repair_json(
            json.dumps({"title": "Index", "source": "fabricated"})
        )
