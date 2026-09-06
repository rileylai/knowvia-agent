from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class ConversationRecallKind(str, Enum):
    PREVIOUS_USER_UTTERANCE = "previous_user_utterance"
    PREVIOUS_ASSISTANT_ANSWER = "previous_assistant_answer"
    PREVIOUS_CHOICE = "previous_choice"
    PREVIOUS_RECOMMENDATION_REASON = "previous_recommendation_reason"


_RECALL_PATTERNS = (
    (
        ConversationRecallKind.PREVIOUS_USER_UTTERANCE,
        (
            re.compile(
                r"\bwhat did i say(?:\s+(?:before|previously|earlier))?\??$"
            ),
            re.compile(r"\bwhat was my previous (?:message|question|utterance)\??$"),
        ),
    ),
    (
        ConversationRecallKind.PREVIOUS_ASSISTANT_ANSWER,
        (
            re.compile(
                r"\bwhat did you say(?:\s+(?:before|previously|earlier))?\??$"
            ),
            re.compile(r"\bwhat was your previous (?:answer|response)\??$"),
        ),
    ),
    (
        ConversationRecallKind.PREVIOUS_CHOICE,
        (
            re.compile(
                r"\bwhich\s+(?:option|pattern|choice)\s+did\s+you\s+"
                r"(?:choose|pick|select)"
                r"(?:\s+(?:in\s+your\s+previous\s+answer|before|previously))?\??$"
            ),
            re.compile(
                r"\bwhat\s+(?:option|pattern|choice)\s+did\s+you\s+"
                r"(?:choose|pick|select)(?:\s+before|\s+previously)?\??$"
            ),
            re.compile(r"\bwhat did you recommend(?:\s+before|\s+previously)?\??$"),
            re.compile(r"\bwhat was your previous recommendation\??$"),
        ),
    ),
    (
        ConversationRecallKind.PREVIOUS_RECOMMENDATION_REASON,
        (
            re.compile(
                r"\b(?:so\s+)?why\s+(?:(?:did\s+you\s+recommend)|"
                r"(?:you\s+recommended))\s+(?:that|this|it)"
                r"(?:\s+(?:pattern|option|approach|choice))?\??$"
            ),
            re.compile(
                r"\bwhy did you choose\s+(?:that|this|it)"
                r"(?:\s+(?:pattern|option|approach|choice))?\??$"
            ),
        ),
    ),
)


def classify_conversation_recall(question: str) -> Optional[ConversationRecallKind]:
    normalized_question = " ".join(question.casefold().split())
    if not normalized_question:
        return None

    for kind, patterns in _RECALL_PATTERNS:
        if any(pattern.fullmatch(normalized_question) for pattern in patterns):
            return kind
    return None


__all__ = ["ConversationRecallKind", "classify_conversation_recall"]
