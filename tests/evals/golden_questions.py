from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_GOLDEN_QUESTIONS_PATH = Path(__file__).with_name("golden_questions.yaml")
_AI_SUPPLEMENT_ZONE_MARKER = "/AI Supplement Zone/"


class GoldenQuestionValidationError(ValueError):
    pass


class GoldenQuestionScope(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    top_k: int = Field(default=5, ge=1, le=20)
    page_ids: List[str] = Field(default_factory=list)
    section_paths: List[str] = Field(default_factory=list)
    source_kinds: List[Literal["notion"]] = Field(min_length=1)


class GoldenQuestionExpected(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_state: Literal["manual_note", "accepted_ai_supplement"]
    paths: List[str] = Field(min_length=1)
    must_include: List[str] = Field(default_factory=list)
    must_not_include: List[str] = Field(default_factory=list)


class GoldenQuestionChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieval_hit_rate: bool
    citation_accuracy: bool
    production_rag_exclusion: bool


class GoldenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    category: Literal["nlp", "iso_9001", "ai_supplement_zone"]
    query: str = Field(min_length=1)
    scope: GoldenQuestionScope
    expected: GoldenQuestionExpected
    checks: GoldenQuestionChecks

    @model_validator(mode="after")
    def _validate_expected_path_ownership(self) -> "GoldenQuestion":
        paths_in_ai_supplement_zone = [
            path
            for path in self.expected.paths
            if _AI_SUPPLEMENT_ZONE_MARKER in path
        ]
        if self.expected.source_state == "accepted_ai_supplement":
            if len(paths_in_ai_supplement_zone) != len(self.expected.paths):
                raise ValueError(
                    "accepted_ai_supplement paths must be under AI Supplement Zone"
                )
        elif paths_in_ai_supplement_zone:
            raise ValueError("manual_note paths must not be under AI Supplement Zone")
        return self


class GoldenQuestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    questions: List[GoldenQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> "GoldenQuestionSet":
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("golden question ids must be unique")
        return self


def load_golden_questions(
    path: Optional[Path] = None,
) -> GoldenQuestionSet:
    golden_path = path or DEFAULT_GOLDEN_QUESTIONS_PATH
    try:
        with golden_path.open(encoding="utf-8") as file:
            payload = yaml.safe_load(file)
    except OSError as exc:
        raise GoldenQuestionValidationError(
            f"Could not read golden question set: {golden_path}"
        ) from exc
    except yaml.YAMLError as exc:
        raise GoldenQuestionValidationError(
            f"Golden question set is not valid YAML: {golden_path}"
        ) from exc

    try:
        return GoldenQuestionSet.model_validate(payload)
    except ValidationError as exc:
        raise GoldenQuestionValidationError(
            f"Golden question set failed schema validation: {golden_path}"
        ) from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load and validate the LearnLoop golden question set."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_GOLDEN_QUESTIONS_PATH,
    )
    args = parser.parse_args()

    question_set = load_golden_questions(args.path)
    print(f"Loaded {len(question_set.questions)} golden questions from {args.path}")


if __name__ == "__main__":
    main()
