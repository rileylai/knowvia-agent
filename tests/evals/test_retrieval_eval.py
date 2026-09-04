from __future__ import annotations

from .golden_questions import load_golden_questions
from .retrieval_eval import (
    build_synthetic_retrieval_session,
    evaluate_retrieval_hit_rate,
    format_retrieval_eval_result,
)


def test_retrieval_eval_reports_full_hit_rate_for_synthetic_fixture() -> None:
    question_set = load_golden_questions()
    session = build_synthetic_retrieval_session(question_set)
    try:
        result = evaluate_retrieval_hit_rate(
            session=session,
            question_set=question_set,
        )
    finally:
        session.close()

    assert result.total_questions == 3
    assert result.hit_count == 3
    assert result.hit_rate == 1.0
    assert all(question_result.hit for question_result in result.question_results)

    output = format_retrieval_eval_result(result)
    assert "retrieval_hit_rate: 1.000 (3/3)" in output
    for question in question_set.questions:
        assert question.id in output


def test_retrieval_eval_reports_miss_for_missing_expected_path() -> None:
    question_set = load_golden_questions()
    missing_question_id = "gq-iso-9001-process-001"
    session = build_synthetic_retrieval_session(
        question_set,
        missing_question_ids={missing_question_id},
    )
    try:
        result = evaluate_retrieval_hit_rate(
            session=session,
            question_set=question_set,
        )
    finally:
        session.close()

    assert result.total_questions == 3
    assert result.hit_count == 2
    assert result.hit_rate == 2 / 3

    missing_result = next(
        question_result
        for question_result in result.question_results
        if question_result.question_id == missing_question_id
    )
    assert missing_result.hit is False
    assert missing_result.retrieved_paths == []


def test_retrieval_eval_keeps_non_notion_chunks_out_of_results() -> None:
    question_set = load_golden_questions()
    session = build_synthetic_retrieval_session(question_set)
    try:
        result = evaluate_retrieval_hit_rate(
            session=session,
            question_set=question_set,
        )
    finally:
        session.close()

    for question_result in result.question_results:
        assert "Synthetic/NonProduction/Pending" not in question_result.retrieved_paths
