from __future__ import annotations

from .golden_questions import load_golden_questions


def test_golden_question_set_loads_with_required_examples() -> None:
    question_set = load_golden_questions()

    assert question_set.version == 1
    assert len(question_set.questions) == 3
    assert {question.category for question in question_set.questions} == {
        "nlp",
        "iso_9001",
        "ai_supplement_zone",
    }


def test_golden_questions_enforce_production_rag_boundaries() -> None:
    question_set = load_golden_questions()

    for question in question_set.questions:
        assert question.scope.source_kinds == ["notion"]
        assert question.checks.production_rag_exclusion is True
        assert "pending change request content" in question.expected.must_not_include
        assert "rejected change request content" in question.expected.must_not_include

    ai_supplement_question = next(
        question
        for question in question_set.questions
        if question.category == "ai_supplement_zone"
    )
    assert ai_supplement_question.expected.source_state == "accepted_ai_supplement"
    assert all(
        "/AI Supplement Zone/" in path
        for path in ai_supplement_question.expected.paths
    )
