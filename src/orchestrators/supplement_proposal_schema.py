from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from src.proposal_limits import (
    MAX_SUPPLEMENT_CONCEPT_CHARS,
    MAX_SUPPLEMENT_CONCEPTS,
    MAX_SUPPLEMENT_NOTE_CHARS,
    MAX_SUPPLEMENT_NOTES,
    MAX_SUPPLEMENT_SUMMARY_CHARS,
    MAX_SUPPLEMENT_TITLE_CHARS,
    MAX_SUPPLEMENT_TOTAL_TEXT_CHARS,
)

_JSON_CODE_BLOCK_PATTERN = re.compile(
    r"^```(?:json)?\s*(?P<body>[\s\S]*?)\s*```$",
    re.IGNORECASE,
)


def _normalize_string_items(
    items: List[str],
    *,
    item_name: str,
    max_item_chars: int,
) -> List[str]:
    normalized_items: List[str] = []
    for index, value in enumerate(items):
        if not isinstance(value, str):
            raise ValueError(f"{item_name} at index {index} must be a string")
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError(f"{item_name} at index {index} must not be empty")
        if len(normalized_value) > max_item_chars:
            raise ValueError(
                f"{item_name} at index {index} exceeds {max_item_chars} characters"
            )
        normalized_items.append(normalized_value)
    return normalized_items


class SupplementProposalValidationError(Exception):
    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        failure_stage: str = "proposal_validation",
        diagnostics: Optional[Dict[str, Any]] = None,
        private_diagnostics: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = "LLM_OUTPUT_INVALID"
        self.failure_reason = "LLM_OUTPUT_INVALID"
        self.message = message
        self.field = field
        self.failure_stage = failure_stage
        self.diagnostics = dict(diagnostics or {})
        # Private diagnostics may contain proposal text. They are available
        # only to an explicit in-process diagnostic caller and must never be
        # copied into workflow metadata, API responses, or general logs.
        self.private_diagnostics = dict(private_diagnostics or {})


class SupplementProposalSourceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: str = Field(min_length=1)
    source_display_name: str = Field(min_length=1)


class SupplementProposalCitationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: Optional[str] = Field(default=None, min_length=1)
    source_display_name: Optional[str] = Field(default=None, min_length=1)
    notion_path: Optional[str] = Field(default=None, min_length=1)
    page_id: Optional[str] = Field(default=None, min_length=1)
    quote: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_citation_reference(self) -> "SupplementProposalCitationSchema":
        if not any(
            value is not None
            for value in (
                self.source_display_name,
                self.notion_path,
                self.page_id,
                self.quote,
            )
        ):
            raise ValueError(
                "citation must include source_display_name, notion_path, page_id, or quote"
            )
        return self


class SupplementProposalGeneratedSchema(BaseModel):
    """Provider-owned content that can be merged into a final proposal.

    Source, target, and citation fields deliberately do not belong here.  The
    parser below tolerates legacy backend-owned keys at the provider boundary
    and drops them before this strict model is validated.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=MAX_SUPPLEMENT_TITLE_CHARS)
    summary: str = Field(min_length=1, max_length=MAX_SUPPLEMENT_SUMMARY_CHARS)
    concepts: List[str] = Field(min_length=1, max_length=MAX_SUPPLEMENT_CONCEPTS)
    notes: List[str] = Field(min_length=1, max_length=MAX_SUPPLEMENT_NOTES)

    @field_validator("concepts")
    @classmethod
    def _validate_concepts(cls, items: List[str]) -> List[str]:
        return _normalize_string_items(
            items,
            item_name="concept",
            max_item_chars=MAX_SUPPLEMENT_CONCEPT_CHARS,
        )

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, items: List[str]) -> List[str]:
        return _normalize_string_items(
            items,
            item_name="note",
            max_item_chars=MAX_SUPPLEMENT_NOTE_CHARS,
        )


class SupplementProposalSchema(SupplementProposalGeneratedSchema):
    """Final business proposal with deterministic backend-owned fields."""

    target_path: str = Field(min_length=1)
    source: SupplementProposalSourceSchema
    citations: List[SupplementProposalCitationSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_total_text_bound(self) -> "SupplementProposalSchema":
        total_chars = sum(
            len(value)
            for value in (
                self.title,
                self.target_path,
                self.summary,
                *self.concepts,
                *self.notes,
                *(citation.quote or "" for citation in self.citations),
            )
        )
        if total_chars > MAX_SUPPLEMENT_TOTAL_TEXT_CHARS:
            raise ValueError(
                "proposal text exceeds the configured total character bound"
            )
        return self


# These keys were historically requested from the model, but their values are
# backend-owned.  They are explicitly removed at the provider boundary so a
# legacy model response cannot override the persisted source or target.  Any
# other unknown key remains a strict provider-output schema failure.
BACKEND_OWNED_PROVIDER_FIELDS = frozenset(
    {
        "source",
        "target_path",
        "citations",
        "source_document_id",
        "source_attachment_count",
        "target_notion_page_id",
        "target_notion_path",
    }
)


def build_deterministic_supplement_source(
    *,
    source_type: str,
    source_display_name: str,
) -> SupplementProposalSourceSchema:
    """Build canonical proposal source metadata from persisted backend state."""

    return SupplementProposalSourceSchema(
        source_type=source_type,
        source_display_name=source_display_name,
    )


def merge_generated_supplement_proposal(
    *,
    generated: SupplementProposalGeneratedSchema,
    source: SupplementProposalSourceSchema,
    target_path: str,
    citations: Optional[List[SupplementProposalCitationSchema]] = None,
) -> SupplementProposalSchema:
    """Compose provider content with deterministic source/target ownership."""

    try:
        return SupplementProposalSchema.model_validate(
            {
                **generated.model_dump(),
                "source": source.model_dump(),
                "target_path": target_path,
                "citations": [
                    citation.model_dump()
                    if isinstance(citation, SupplementProposalCitationSchema)
                    else citation
                    for citation in (citations or [])
                ],
            }
        )
    except ValidationError as exc:
        first_error = exc.errors()[0]
        error_loc = ".".join(str(part) for part in first_error.get("loc", []))
        error_message = first_error.get("msg", "schema validation failed")
        raise SupplementProposalValidationError(
            f"LLM provider output failed final schema validation at '{error_loc}': {error_message}",
            field="provider_output",
            failure_stage="provider_output_validation",
        ) from exc


class SupplementTitleRepairSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=MAX_SUPPLEMENT_TITLE_CHARS)


class SupplementSummaryRepairSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1, max_length=MAX_SUPPLEMENT_SUMMARY_CHARS)


class SupplementBodyRepairSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=1)
    concepts: List[str] = Field(min_length=3, max_length=30)
    notes: List[str] = Field(min_length=1, max_length=MAX_SUPPLEMENT_NOTES)

    @field_validator("summary")
    @classmethod
    def _validate_summary_length(cls, value: str) -> str:
        return value.strip()

    @field_validator("concepts")
    @classmethod
    def _validate_concepts(cls, items: List[str]) -> List[str]:
        return _normalize_string_items(
            items,
            item_name="concept",
            max_item_chars=MAX_SUPPLEMENT_CONCEPT_CHARS,
        )

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, items: List[str]) -> List[str]:
        return _normalize_string_items(
            items,
            item_name="note",
            max_item_chars=MAX_SUPPLEMENT_NOTE_CHARS,
        )


def parse_supplement_proposal_json(llm_output: str) -> SupplementProposalSchema:
    normalized_output = llm_output.strip()
    if not normalized_output:
        raise SupplementProposalValidationError("LLM output is empty")

    json_payload = _extract_json_payload(normalized_output)
    try:
        parsed_value = json.loads(json_payload)
    except json.JSONDecodeError as exc:
        raise SupplementProposalValidationError(
            "LLM output is not valid JSON"
        ) from exc

    if not isinstance(parsed_value, dict):
        raise SupplementProposalValidationError(
            "LLM output JSON must be an object"
        )

    try:
        return SupplementProposalSchema.model_validate(parsed_value)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        error_loc = ".".join(str(part) for part in first_error.get("loc", []))
        error_message = first_error.get("msg", "schema validation failed")
        raise SupplementProposalValidationError(
            f"LLM output schema validation failed at '{error_loc}': {error_message}"
        ) from exc


def parse_supplement_generated_json(
    llm_output: str,
) -> SupplementProposalGeneratedSchema:
    """Parse provider-generated content without accepting source ownership."""

    normalized_output = llm_output.strip()
    if not normalized_output:
        raise SupplementProposalValidationError(
            "LLM provider output is empty",
            field="provider_output",
            failure_stage="provider_output_validation",
        )

    json_payload = _extract_json_payload(normalized_output)
    try:
        parsed_value = json.loads(json_payload)
    except json.JSONDecodeError as exc:
        raise SupplementProposalValidationError(
            "LLM provider output is not valid JSON",
            field="provider_output",
            failure_stage="provider_output_validation",
        ) from exc

    if not isinstance(parsed_value, dict):
        raise SupplementProposalValidationError(
            "LLM provider output JSON must be an object",
            field="provider_output",
            failure_stage="provider_output_validation",
        )

    generated_payload = {
        key: value
        for key, value in parsed_value.items()
        if key not in BACKEND_OWNED_PROVIDER_FIELDS
    }
    try:
        return SupplementProposalGeneratedSchema.model_validate(generated_payload)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        error_loc = ".".join(str(part) for part in first_error.get("loc", []))
        error_message = first_error.get("msg", "schema validation failed")
        raise SupplementProposalValidationError(
            f"LLM provider output schema validation failed at '{error_loc}': {error_message}",
            field="provider_output",
            failure_stage="provider_output_validation",
        ) from exc


def parse_supplement_title_repair_json(llm_output: str) -> SupplementTitleRepairSchema:
    normalized_output = llm_output.strip()
    if not normalized_output:
        raise SupplementProposalValidationError(
            "title repair output is empty",
            field="title",
        )

    json_payload = _extract_json_payload(normalized_output)
    try:
        parsed_value = json.loads(json_payload)
    except json.JSONDecodeError as exc:
        raise SupplementProposalValidationError(
            "title repair output is not valid JSON",
            field="title",
        ) from exc

    if not isinstance(parsed_value, dict):
        raise SupplementProposalValidationError(
            "title repair output JSON must be an object",
            field="title",
        )

    try:
        return SupplementTitleRepairSchema.model_validate(parsed_value)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        error_message = first_error.get("msg", "title repair schema validation failed")
        raise SupplementProposalValidationError(
            f"title repair schema validation failed: {error_message}",
            field="title",
        ) from exc


def parse_supplement_summary_repair_json(
    llm_output: str,
) -> SupplementSummaryRepairSchema:
    normalized_output = llm_output.strip()
    if not normalized_output:
        raise SupplementProposalValidationError(
            "summary repair output is empty",
            field="summary",
        )

    json_payload = _extract_json_payload(normalized_output)
    try:
        parsed_value = json.loads(json_payload)
    except json.JSONDecodeError as exc:
        raise SupplementProposalValidationError(
            "summary repair output is not valid JSON",
            field="summary",
        ) from exc

    if not isinstance(parsed_value, dict):
        raise SupplementProposalValidationError(
            "summary repair output JSON must be an object",
            field="summary",
        )

    try:
        return SupplementSummaryRepairSchema.model_validate(parsed_value)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        error_message = first_error.get("msg", "summary repair schema validation failed")
        raise SupplementProposalValidationError(
            f"summary repair schema validation failed: {error_message}",
            field="summary",
        ) from exc


def parse_supplement_body_repair_json(
    llm_output: str,
) -> SupplementBodyRepairSchema:
    normalized_output = llm_output.strip()
    if not normalized_output:
        raise SupplementProposalValidationError(
            "body repair output is empty",
            field="body",
        )

    json_payload = _extract_json_payload(normalized_output)
    try:
        parsed_value = json.loads(json_payload)
    except json.JSONDecodeError as exc:
        raise SupplementProposalValidationError(
            "body repair output is not valid JSON",
            field="body",
        ) from exc

    if not isinstance(parsed_value, dict):
        raise SupplementProposalValidationError(
            "body repair output JSON must be an object",
            field="body",
        )

    try:
        return SupplementBodyRepairSchema.model_validate(parsed_value)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        error_message = first_error.get("msg", "body repair schema validation failed")
        raise SupplementProposalValidationError(
            f"body repair schema validation failed: {error_message}",
            field="body",
        ) from exc


def _extract_json_payload(llm_output: str) -> str:
    code_block_match = _JSON_CODE_BLOCK_PATTERN.match(llm_output)
    if code_block_match:
        return code_block_match.group("body").strip()
    return llm_output
