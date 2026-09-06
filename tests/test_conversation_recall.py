from __future__ import annotations

import pytest

from src.conversation_recall import (
    ConversationRecallKind,
    classify_conversation_recall,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What did I say?", ConversationRecallKind.PREVIOUS_USER_UTTERANCE),
        ("What did you say before?", ConversationRecallKind.PREVIOUS_ASSISTANT_ANSWER),
        (
            "Which pattern did you choose in your previous answer?",
            ConversationRecallKind.PREVIOUS_CHOICE,
        ),
        (
            "What was your previous recommendation?",
            ConversationRecallKind.PREVIOUS_CHOICE,
        ),
        (
            "Why did you recommend that pattern?",
            ConversationRecallKind.PREVIOUS_RECOMMENDATION_REASON,
        ),
        (
            "so why you recommended this pattern",
            ConversationRecallKind.PREVIOUS_RECOMMENDATION_REASON,
        ),
    ],
)
def test_classify_bounded_conversation_recall_questions(
    question: str,
    expected: ConversationRecallKind,
) -> None:
    assert classify_conversation_recall(question) is expected


@pytest.mark.parametrize(
    "question",
    [
        "What database does production use?",
        "What does the indexed document say about patterns?",
        "What limitations does this pattern have according to the document?",
    ],
)
def test_classify_enterprise_questions_as_knowledge_requests(question: str) -> None:
    assert classify_conversation_recall(question) is None
