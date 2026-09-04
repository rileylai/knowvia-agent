from __future__ import annotations

import pytest

from .citation_accuracy_eval import (
    evaluate_citation_accuracy,
    format_citation_accuracy_eval_result,
)
from .golden_questions import load_golden_questions
from .retrieval_eval import build_synthetic_retrieval_session


def test_citation_accuracy_eval_reports_full_accuracy_for_synthetic_fixture() -> None:
    question_set = load_golden_questions()
    session = build_synthetic_retrieval_session(question_set)
    try:
        result = evaluate_citation_accuracy(
            session=session,
            question_set=question_set,
        )
    finally:
        session.close()

    assert result.total_questions == 3
    assert result.accurate_count == 3
    assert result.accuracy == 1.0
    assert result.passed is True
    assert all(question_result.accurate for question_result in result.question_results)

    output = format_citation_accuracy_eval_result(result)
    assert "citation_accuracy: 1.000 (3/3)" in output
    assert "threshold: 1.000" in output
    assert "status: pass" in output


def test_citation_accuracy_eval_reports_inaccurate_missing_expected_path() -> None:
    question_set = load_golden_questions()
    missing_question_id = "gq-iso-9001-process-001"
    session = build_synthetic_retrieval_session(
        question_set,
        missing_question_ids={missing_question_id},
    )
    try:
        result = evaluate_citation_accuracy(
            session=session,
            question_set=question_set,
        )
    finally:
        session.close()

    assert result.total_questions == 3
    assert result.accurate_count == 2
    assert result.accuracy == 2 / 3
    assert result.passed is False

    missing_result = next(
        question_result
        for question_result in result.question_results
        if question_result.question_id == missing_question_id
    )
    assert missing_result.accurate is False
    assert missing_result.citation_paths == []


def test_citation_accuracy_threshold_can_be_lowered_for_reporting() -> None:
    question_set = load_golden_questions()
    session = build_synthetic_retrieval_session(
        question_set,
        missing_question_ids={"gq-iso-9001-process-001"},
    )
    try:
        result = evaluate_citation_accuracy(
            session=session,
            question_set=question_set,
            threshold=0.5,
        )
    finally:
        session.close()

    assert result.accuracy == 2 / 3
    assert result.threshold == 0.5
    assert result.passed is True


def test_citation_accuracy_rejects_invalid_threshold() -> None:
    question_set = load_golden_questions()
    session = build_synthetic_retrieval_session(question_set)
    try:
        with pytest.raises(ValueError, match="threshold"):
            evaluate_citation_accuracy(
                session=session,
                question_set=question_set,
                threshold=1.1,
            )
    finally:
        session.close()


def test_citation_accuracy_keeps_non_production_paths_out_of_citations() -> None:
    question_set = load_golden_questions()
    session = build_synthetic_retrieval_session(question_set)
    try:
        result = evaluate_citation_accuracy(
            session=session,
            question_set=question_set,
        )
    finally:
        session.close()

    for question_result in result.question_results:
        assert "Synthetic/NonProduction/Pending" not in question_result.citation_paths
