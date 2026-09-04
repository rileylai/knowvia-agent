from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.orchestrators import (
    SupplementProposalSchema,
    SupplementProposalValidationError,
)
from src.services.screenshot_quality import (
    build_screenshot_source_snapshot,
    detect_screenshot_language,
    preprocess_screenshot_ocr_text,
    validate_screenshot_proposal,
    validate_screenshot_proposal_with_diagnostics,
    validate_screenshot_proposal_with_title_fallback,
)


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "screenshot_proposal_fixtures.json"


def _load_fixtures() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _merge_images(images: list[dict]) -> str:
    return "\n".join(
        preprocess_screenshot_ocr_text(image["ocr"])
        for image in sorted(images, key=lambda item: item["message_id"])
    )


def test_continuous_multi_image_fixture_is_one_grounded_batch() -> None:
    fixture = next(item for item in _load_fixtures() if item["id"] == "continuous_multi_image_batch")
    source_text = _merge_images(fixture["images"])
    proposal = SupplementProposalSchema.model_validate(fixture["proposal"])

    validated = validate_screenshot_proposal(
        proposal=proposal,
        source_text=source_text,
    )

    assert validated is proposal
    assert source_text.index("Kubernetes Deployment") < source_text.index("PyTorch batch normalization")
    assert 3 <= len(validated.concepts) <= 30
    assert 1 <= len(validated.notes) <= 12


def test_public_safe_four_image_mysql_batch_uses_one_snapshot_and_all_claims() -> None:
    fixture = next(
        item
        for item in _load_fixtures()
        if item["id"] == "public_safe_mysql_four_image_batch"
    )
    source_text = _merge_images(fixture["images"])
    snapshot = build_screenshot_source_snapshot(source_text)
    result = validate_screenshot_proposal_with_title_fallback(
        proposal=SupplementProposalSchema.model_validate(fixture["proposal"]),
        source_text=snapshot.text,
        source_snapshot=snapshot,
    )

    assert result.diagnostics is not None
    diagnostics = result.diagnostics
    assert diagnostics.evidence_claim_count >= 9
    assert diagnostics.unsupported_claim_count == 0
    assert diagnostics.source_normalized_char_count > 0
    assert diagnostics.candidate_field_char_count > 0
    assert diagnostics.source_snapshot_digest == diagnostics.prompt_source_digest
    assert diagnostics.prompt_source_digest == diagnostics.validation_source_digest
    assert "https://example.invalid/mysql" not in snapshot.text
    for expected_anchor in fixture["expected_cleaned_order"]:
        assert expected_anchor in snapshot.text
    assert snapshot.text.index("MySQL EXPLAIN") < snapshot.text.index("索引可協助")
    assert snapshot.text.index("索引可協助") < snapshot.text.index("EXPLAIN 的 key")
    assert snapshot.text.index("EXPLAIN 的 key") < snapshot.text.index("來源說明 MySQL")


def test_live_shaped_four_image_title_allows_unmatched_general_cjk_anchors() -> None:
    fixture = next(
        item
        for item in _load_fixtures()
        if item["id"] == "live_shaped_mysql_four_image_title_general_cjk"
    )
    source_text = _merge_images(fixture["images"])
    snapshot = build_screenshot_source_snapshot(source_text)
    result = validate_screenshot_proposal_with_diagnostics(
        proposal=SupplementProposalSchema.model_validate(fixture["proposal"]),
        source_text=snapshot.text,
        source_snapshot=snapshot,
    )

    assert result.diagnostics is not None
    diagnostics = result.diagnostics.as_dict()
    assert 20 <= len(fixture["proposal"]["title"]) <= 40
    assert diagnostics["title_anchor_count"] == 4
    assert diagnostics["matched_title_anchor_count"] == 1
    assert diagnostics["unmatched_title_anchor_count"] == 3
    assert diagnostics["numeric_anchor_count"] == 0
    assert diagnostics["unmatched_numeric_anchor_count"] == 0
    assert diagnostics["matched_high_specificity_anchor_count"] == 1
    assert diagnostics["unmatched_high_specificity_anchor_count"] == 0
    assert diagnostics["matched_general_anchor_count"] == 0
    assert diagnostics["unmatched_general_anchor_count"] == 3
    assert diagnostics["matched_technical_identifier_count"] == 0
    assert diagnostics["unmatched_technical_identifier_count"] == 0
    assert diagnostics["title_failure_reason"] is None
    assert diagnostics["title_repair_failure_reason"] is None


def test_live_shaped_five_image_title_contract_is_bounded_and_fail_closed() -> None:
    fixture = next(
        item
        for item in _load_fixtures()
        if item["id"] == "live_shaped_mysql_five_image_batch"
    )
    source_text = _merge_images(fixture["images"])
    snapshot = build_screenshot_source_snapshot(source_text)
    proposal = SupplementProposalSchema.model_validate(fixture["proposal"])

    result = validate_screenshot_proposal_with_title_fallback(
        proposal=proposal,
        source_text=snapshot.text,
        source_snapshot=snapshot,
    )
    assert result.diagnostics is not None
    diagnostics = result.diagnostics
    assert diagnostics.title_anchor_count >= 5
    assert diagnostics.matched_title_anchor_count == diagnostics.title_anchor_count
    assert diagnostics.unmatched_title_anchor_count == 0
    assert diagnostics.numeric_anchor_count == 0
    assert diagnostics.unmatched_numeric_anchor_count == 0
    assert 100 <= len(fixture["proposal"]["summary"]) <= 180
    assert diagnostics.evidence_claim_count >= 9
    assert diagnostics.extracted_claim_count == diagnostics.matched_claim_count
    assert diagnostics.first_unsupported_claim_index is None
    assert diagnostics.first_unsupported_reason is None

    for unsupported_title in (
        "MySQL EXPLAIN 與 Redis 索引",
        "MySQL 分庫分表與索引",
        "MySQL 索引效能提升 30%",
    ):
        invalid = proposal.model_copy(update={"title": unsupported_title})
        with pytest.raises(SupplementProposalValidationError) as exc_info:
            validate_screenshot_proposal(
                proposal=invalid,
                source_text=snapshot.text,
            )
        diagnostics = exc_info.value.diagnostics
        assert diagnostics["evidence_claim_count"] == 0
        assert diagnostics["unsupported_claim_count"] == 1
        assert diagnostics["unmatched_title_anchor_count"] >= 1
    percentage_diagnostics = exc_info.value.diagnostics
    assert percentage_diagnostics["unmatched_numeric_anchor_count"] >= 1


@pytest.mark.parametrize(
    ("proposal_key", "expected_units", "expected_matched", "expected_failed"),
    [
        ("proposal", 15, 7, 8),
        ("retry_proposal", 16, 9, 7),
    ],
)
def test_workflow_252_255_shape_uses_sentence_and_full_list_item_units(
    proposal_key: str,
    expected_units: int,
    expected_matched: int,
    expected_failed: int,
) -> None:
    fixture = next(
        item
        for item in _load_fixtures()
        if item["id"] == "workflow_252_255_shape_public_safe"
    )
    proposal = SupplementProposalSchema.model_validate(fixture[proposal_key])

    with pytest.raises(SupplementProposalValidationError) as exc_info:
        validate_screenshot_proposal_with_diagnostics(
            proposal=proposal,
            source_text=fixture["source"],
        )

    error = exc_info.value
    diagnostics = error.diagnostics
    assert error.field == "summary"
    assert diagnostics["validation_granularity"] == (
        "summary_sentence_list_item_v1"
    )
    assert diagnostics["validation_unit_count"] == expected_units
    assert diagnostics["extracted_claim_count"] == expected_units
    assert diagnostics["matched_validation_unit_count"] == expected_matched
    assert diagnostics["matched_claim_count"] == expected_matched
    assert diagnostics["failed_validation_unit_count"] == expected_failed
    assert diagnostics["unsupported_claim_count"] == expected_failed
    assert diagnostics["failed_field_count"] == expected_failed
    assert diagnostics["failed_logical_regions"] == [
        "concepts",
        "notes",
        "summary",
    ]
    assert diagnostics["failed_logical_region_count"] == 3
    assert diagnostics["failed_proposal_field_count"] == 3
    assert diagnostics["first_unsupported_reason"] == "PARAPHRASE_NOT_GROUNDED"
    assert diagnostics["summary_repair_eligible"] is False
    assert diagnostics["body_repair_eligible"] is True
    assert diagnostics["repair_scope"] == "body"
    assert diagnostics["unmatched_general_token_count"] >= expected_failed
    assert len(diagnostics["failed_validation_unit_details"]) == expected_failed

    private_fields = error.private_diagnostics["validation_fields"]
    assert len(private_fields) == expected_units
    assert private_fields[0]["field_path"] == "summary"
    assert private_fields[0]["split_result"] == [proposal.summary]
    assert private_fields[1]["split_result"] == [proposal.concepts[0]]
    assert private_fields[-1]["split_result"] == [proposal.notes[-1]]
    assert private_fields[0]["validation_units"][0]["matched_evidence"]
    assert proposal.summary not in json.dumps(diagnostics, ensure_ascii=False)


@pytest.mark.parametrize(
    ("title", "expected_reason"),
    [
        ("MySQL EXPLAIN 與 Redis 索引", "UNMATCHED_PRODUCT_NAME"),
        ("MySQL EXPLAIN 與分庫分表", "UNMATCHED_TECHNICAL_IDENTIFIER"),
        ("MySQL EXPLAIN 索引 30%", "UNMATCHED_NUMBER_OR_VERSION"),
        ("Screenshot summary", "GENERIC_TITLE_ONLY"),
        ("招募", "NO_USABLE_TITLE_ANCHOR"),
    ],
)
def test_title_failure_diagnostics_use_fixed_redacted_enums(
    title: str,
    expected_reason: str,
) -> None:
    fixture = next(
        item
        for item in _load_fixtures()
        if item["id"] == "live_shaped_mysql_five_image_batch"
    )
    proposal = SupplementProposalSchema.model_validate(fixture["proposal"]).model_copy(
        update={"title": title}
    )

    with pytest.raises(SupplementProposalValidationError) as exc_info:
        validate_screenshot_proposal_with_diagnostics(
            proposal=proposal,
            source_text=_merge_images(fixture["images"]),
        )

    diagnostics = exc_info.value.diagnostics
    assert diagnostics["title_failure_reason"] == expected_reason
    assert diagnostics["title_repair_failure_reason"] is None
    assert isinstance(diagnostics["matched_high_specificity_anchor_count"], int)
    assert isinstance(diagnostics["unmatched_general_anchor_count"], int)
    assert isinstance(diagnostics["matched_technical_identifier_count"], int)
    assert isinstance(diagnostics["unmatched_technical_identifier_count"], int)
    assert title not in json.dumps(diagnostics, ensure_ascii=False)


@pytest.mark.parametrize(
    ("invalid_summary", "expected_reason"),
    [
        (
            "這組截圖整理 MySQL EXPLAIN 與 SQL 查詢，另有 Redis。",
            "NEW_TECHNICAL_IDENTIFIER",
        ),
        (
            "這組截圖整理 MySQL EXPLAIN 與 SQL 查詢，索引可提升 30% 效能。",
            "NEW_NUMBER_OR_VERSION",
        ),
        (
            "這組截圖整理 MySQL EXPLAIN 與 SQL 查詢，並建議採用最佳實務進行分庫分表。",
            "UNSUPPORTED_ADVICE",
        ),
    ],
)
def test_live_shaped_summary_contract_rejects_new_claim_content(
    invalid_summary: str,
    expected_reason: str,
) -> None:
    fixture = next(
        item
        for item in _load_fixtures()
        if item["id"] == "live_shaped_mysql_five_image_batch"
    )
    source_text = _merge_images(fixture["images"])
    proposal = SupplementProposalSchema.model_validate(fixture["proposal"]).model_copy(
        update={"summary": invalid_summary}
    )

    with pytest.raises(SupplementProposalValidationError) as exc_info:
        validate_screenshot_proposal_with_diagnostics(
            proposal=proposal,
            source_text=source_text,
        )

    diagnostics = exc_info.value.diagnostics
    assert diagnostics["first_unsupported_reason"] == expected_reason
    assert diagnostics["extracted_claim_count"] >= 1
    assert diagnostics["unsupported_claim_count"] >= 1
    assert diagnostics["first_unsupported_claim_index"] == 0
    assert diagnostics["summary_repair_eligible"] is False
    assert invalid_summary not in json.dumps(diagnostics, ensure_ascii=False)


def test_summary_analysis_is_bounded_without_early_return_diagnostic_loss() -> None:
    fixture = next(
        item
        for item in _load_fixtures()
        if item["id"] == "live_shaped_mysql_five_image_batch"
    )
    source_text = _merge_images(fixture["images"])
    proposal = SupplementProposalSchema.model_validate(fixture["proposal"]).model_copy(
        update={
            "summary": (
                "這組截圖加入 Redis。"
                "MySQL EXPLAIN 會顯示查詢的執行計畫。"
            )
        }
    )

    with pytest.raises(SupplementProposalValidationError) as exc_info:
        validate_screenshot_proposal_with_diagnostics(
            proposal=proposal,
            source_text=source_text,
        )

    diagnostics = exc_info.value.diagnostics
    assert diagnostics["extracted_claim_count"] >= 2
    assert diagnostics["matched_claim_count"] >= 1
    assert diagnostics["evidence_claim_count"] == diagnostics["matched_claim_count"]
    assert diagnostics["unsupported_claim_count"] == 1
    assert diagnostics["first_unsupported_claim_index"] == 0
    assert diagnostics["first_unsupported_reason"] == "NEW_TECHNICAL_IDENTIFIER"


def test_summary_claim_extraction_handles_mixed_cjk_punctuation_and_newlines() -> None:
    fixture = next(
        item
        for item in _load_fixtures()
        if item["id"] == "live_shaped_mysql_five_image_batch"
    )
    source_text = _merge_images(fixture["images"])
    proposal = SupplementProposalSchema.model_validate(fixture["proposal"]).model_copy(
        update={
            "summary": (
                "摘要：MySQL EXPLAIN（含 type、key、rows）；"
                "SQL 查詢可搭配 EXPLAIN 觀察索引。\n"
                "索引可協助查詢條件過濾。"
            )
        }
    )

    result = validate_screenshot_proposal_with_diagnostics(
        proposal=proposal,
        source_text=source_text,
    )

    assert result.diagnostics is not None
    assert result.diagnostics.validation_granularity == (
        "summary_sentence_list_item_v1"
    )
    assert result.diagnostics.summary_validation_unit_count == 2
    assert result.diagnostics.concept_validation_unit_count == 5
    assert result.diagnostics.note_validation_unit_count == 4
    assert result.diagnostics.extracted_claim_count == 11
    assert result.diagnostics.extracted_claim_count == result.diagnostics.matched_claim_count
    assert result.diagnostics.unsupported_claim_count == 0


def test_browser_ui_fixture_is_removed_before_proposal_grounding() -> None:
    fixture = next(item for item in _load_fixtures() if item["id"] == "browser_ui_noise")
    source_text = _merge_images(fixture["images"])

    assert "https://example.invalid" not in source_text
    assert "Back" not in source_text
    assert "New Tab" not in source_text
    assert source_text == fixture["expected_cleaned"]
    assert source_text.index("Docker Compose") < source_text.index("RQ worker")
    validate_screenshot_proposal(
        proposal=SupplementProposalSchema.model_validate(fixture["proposal"]),
        source_text=source_text,
    )


def test_traditional_chinese_fixture_selects_zh_hant_and_preserves_terms() -> None:
    fixture = next(item for item in _load_fixtures() if item["id"] == "traditional_chinese_source")
    source_text = _merge_images(fixture["images"])

    assert detect_screenshot_language(source_text).code == fixture["expected_language"]
    proposal = validate_screenshot_proposal(
        proposal=SupplementProposalSchema.model_validate(fixture["proposal"]),
        source_text=source_text,
    )
    natural_text = " ".join(
        [proposal.title, proposal.summary, *proposal.concepts, *proposal.notes]
    )
    assert "Docker Compose" in natural_text
    assert "RQ worker" in natural_text
    assert "docker compose up -d" in natural_text

    english_proposal = proposal.model_copy(
        update={
            "title": "Docker Compose and Redis delayed jobs",
            "summary": "The source describes Docker Compose and Redis delayed jobs.",
            "concepts": ["Docker Compose", "Redis", "RQ worker"],
            "notes": [
                "Docker Compose starts Redis.",
                "Redis is available.",
                "RQ worker processes delayed jobs.",
            ],
        }
    )
    with pytest.raises(
        SupplementProposalValidationError,
        match="Traditional Chinese",
    ):
        validate_screenshot_proposal(
            proposal=english_proposal,
            source_text=source_text,
        )


def test_unsupported_fixture_is_rejected_without_an_llm_judge() -> None:
    fixture = next(item for item in _load_fixtures() if item["id"] == "unsupported_hallucination")

    with pytest.raises(
        SupplementProposalValidationError,
        match="not supported by OCR source",
    ):
        validate_screenshot_proposal(
            proposal=SupplementProposalSchema.model_validate(fixture["proposal"]),
            source_text=fixture["source"],
        )


def test_reasonable_english_paraphrase_passes_grounding() -> None:
    source_text = "The deployment performs a rolling update with new pods."
    proposal = SupplementProposalSchema.model_validate(
        {
            "title": "Deployment rollout with updated pods",
            "target_path": "Knowledge/Engineering/AI Supplement Zone",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "Paraphrase fixture",
            },
            "summary": "The rollout includes updated pods during a gradual update.",
            "concepts": [
                "deployment rollout",
                "updated pods",
                "gradual update",
            ],
            "notes": [
                "The rollout performs an update with new pods.",
                "The rolling update includes new pods.",
                "The source shows a rolling update with pods.",
            ],
        }
    )

    validate_screenshot_proposal(proposal=proposal, source_text=source_text)


def _mysql_sql_proposal(*, title: str) -> SupplementProposalSchema:
    return SupplementProposalSchema.model_validate(
        {
            "title": title,
            "target_path": "Knowledge/Engineering/AI Supplement Zone",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "MySQL SQL fixture",
            },
            "summary": "來源說明 MySQL 索引、EXPLAIN 與 SQL 查詢優化。",
            "concepts": ["MySQL", "索引", "EXPLAIN", "SQL"],
            "notes": [
                "MySQL 使用索引。",
                "EXPLAIN 協助檢查 SQL 查詢。",
                "來源提到查詢優化。",
            ],
        }
    )


def test_chinese_sql_title_uses_multiple_source_anchors() -> None:
    source_text = "MySQL 索引、EXPLAIN 與 SQL 查詢優化。"
    proposal = _mysql_sql_proposal(title="MySQL 索引與查詢優化")

    validate_screenshot_proposal(proposal=proposal, source_text=source_text)


def test_mixed_technical_title_normalizes_unicode_brackets_and_simplified_text() -> None:
    source_text = "MySQL（索引）EXPLAIN SQL 查询优化。"
    proposal = _mysql_sql_proposal(title="MySQL (索引) 與 SQL 查詢優化")

    validate_screenshot_proposal(proposal=proposal, source_text=source_text)


def test_title_normalizes_ocr_spaces_inside_cjk_terms() -> None:
    source_text = "MySQL 索 引、EXPLAIN 與 SQL 查 詢 優 化。"
    proposal = _mysql_sql_proposal(title="MySQL 索引與查詢優化")

    validate_screenshot_proposal(proposal=proposal, source_text=source_text)


def test_unrelated_title_still_fails_closed() -> None:
    source_text = "SQL 查詢優化與索引策略。"
    proposal = _mysql_sql_proposal(title="AI 招募")

    with pytest.raises(
        SupplementProposalValidationError,
        match="title is not supported by OCR source",
    ):
        validate_screenshot_proposal(proposal=proposal, source_text=source_text)


def test_grounded_title_fallback_does_not_call_ocr_or_llm() -> None:
    source_text = "MySQL 索引、EXPLAIN 與 SQL 查詢優化。"
    proposal = _mysql_sql_proposal(title="索引")

    result = validate_screenshot_proposal_with_title_fallback(
        proposal=proposal,
        source_text=source_text,
    )

    assert result.title_fallback_used is True
    assert result.proposal.title != "MySQL 主題"
    assert "MySQL" in result.proposal.title


def test_unsupported_advice_is_rejected_even_with_a_grounded_anchor() -> None:
    source_text = (
        "Docker Compose starts Redis. The RQ worker processes delayed jobs "
        "after Redis is available."
    )
    proposal = SupplementProposalSchema.model_validate(
        {
            "title": "Redis delayed job processing",
            "target_path": "Knowledge/Engineering/AI Supplement Zone",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "Advice fixture",
            },
            "summary": "The RQ worker processes delayed jobs after Redis is available.",
            "concepts": ["Docker Compose", "Redis", "delayed jobs"],
            "notes": [
                "Docker Compose starts Redis.",
                "The worker handles delayed jobs after Redis is ready.",
                "You should enable caching for faster processing.",
            ],
        }
    )

    with pytest.raises(
        SupplementProposalValidationError,
        match="unsupported advice",
    ):
        validate_screenshot_proposal(proposal=proposal, source_text=source_text)


def test_descriptive_use_is_not_misclassified_as_new_advice() -> None:
    source_text = "The screenshot shows how to use Redis for delayed jobs."
    proposal = SupplementProposalSchema.model_validate(
        {
            "title": "Redis delayed jobs",
            "target_path": "Knowledge/Engineering/AI Supplement Zone",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "Descriptive use fixture",
            },
            "summary": "The screenshot shows how to use Redis for delayed jobs.",
            "concepts": ["Redis", "delayed jobs", "use"],
            "notes": [
                "The screenshot shows Redis.",
                "The screenshot shows delayed jobs.",
                "The screenshot shows how to use Redis.",
            ],
        }
    )

    validate_screenshot_proposal(proposal=proposal, source_text=source_text)


@pytest.mark.parametrize(
    ("unsupported_note", "expected_error"),
    [
        (
            "Redis is available on port 6379.",
            "not supported by OCR source",
        ),
        (
            "Redis availability is better.",
            "unsupported conclusion",
        ),
    ],
)
def test_new_number_and_conclusion_are_rejected(
    unsupported_note: str,
    expected_error: str,
) -> None:
    source_text = "Redis is available. The worker processes delayed jobs."
    proposal = SupplementProposalSchema.model_validate(
        {
            "title": "Redis availability and delayed jobs",
            "target_path": "Knowledge/Engineering/AI Supplement Zone",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "Unsupported detail fixture",
            },
            "summary": "Redis is available and the worker processes delayed jobs.",
            "concepts": ["Redis", "availability", "delayed jobs"],
            "notes": [
                "Redis is available.",
                "The worker processes delayed jobs.",
                unsupported_note,
            ],
        }
    )

    with pytest.raises(
        SupplementProposalValidationError,
        match=expected_error,
    ):
        validate_screenshot_proposal(proposal=proposal, source_text=source_text)


def test_browser_ocr_noise_cannot_be_used_as_proposal_evidence() -> None:
    raw_source = (
        "Back\nForward\nhttps://example.invalid/course/slide-1\n"
        "New Tab\nBookmarks\nDocker Compose starts Redis."
    )
    cleaned_source = preprocess_screenshot_ocr_text(raw_source)
    assert cleaned_source == "Docker Compose starts Redis."

    proposal = SupplementProposalSchema.model_validate(
        {
            "title": "Back navigation and Redis",
            "target_path": "Knowledge/Engineering/AI Supplement Zone",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "Browser noise fixture",
            },
            "summary": "The browser navigation appears before Redis starts.",
            "concepts": ["Back navigation", "Redis", "browser tab"],
            "notes": [
                "Back is browser navigation.",
                "Redis starts from Docker Compose.",
                "The source includes a browser tab.",
            ],
        }
    )

    with pytest.raises(
        SupplementProposalValidationError,
        match="not supported by OCR source",
    ):
        validate_screenshot_proposal(proposal=proposal, source_text=raw_source)


def test_traditional_chinese_paraphrase_passes_and_simplified_output_fails() -> None:
    source_text = "系統會在 Redis 可用後處理延遲工作。"
    proposal = SupplementProposalSchema.model_validate(
        {
            "title": "Redis 就緒後的延遲工作處理",
            "target_path": "知識庫/工程/AI Supplement Zone",
            "source": {
                "source_type": "screenshot",
                "source_display_name": "繁中改寫 fixture",
            },
            "summary": "內容指出 Redis 準備完成後，服務才會處理延遲任務。",
            "concepts": ["Redis", "延遲工作", "系統處理"],
            "notes": ["系統會在 Redis 可用後處理延遲工作。"],
        }
    )

    validate_screenshot_proposal(proposal=proposal, source_text=source_text)

    simplified = proposal.model_copy(
        update={
            "title": "Redis 就緒後的延迟工作处理",
            "summary": "內容指出 Redis 準備完成後，服務才會處理延遲任務。",
        }
    )
    with pytest.raises(
        SupplementProposalValidationError,
        match="Traditional Chinese",
    ):
        validate_screenshot_proposal(proposal=simplified, source_text=source_text)
