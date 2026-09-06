from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class ConversationRecallKind(str, Enum):
    PREVIOUS_USER_UTTERANCE = "previous_user_utterance"
    PREVIOUS_ASSISTANT_ANSWER = "previous_assistant_answer"
    PREVIOUS_CHOICE = "previous_choice"
    PREVIOUS_RECOMMENDATION_REASON = "previous_recommendation_reason"


class ConversationTransformKind(str, Enum):
    PREVIOUS_ASSISTANT_TRANSFORM = "previous_assistant_transform"


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


_ZH_PREVIOUS_REFERENCE_MARKERS = ("剛剛", "剛才", "上一個", "上一則", "前一個")
_ZH_ASSISTANT_ANSWER_MARKERS = ("回答", "答案", "說的")
_ZH_TRANSFORM_MARKERS = (
    "用中文",
    "用英文",
    "翻譯",
    "重述",
    "改寫",
    "摘要",
    "總結",
    "簡單",
    "簡化",
)
_ZH_LANGUAGE_SWITCH_MARKERS = ("用中文", "用英文", "翻成中文", "翻成英文")
_ZH_LANGUAGE_ACTION_MARKERS = ("說", "講", "回答", "重述", "解釋")
_ZH_SIMPLIFY_MARKERS = ("簡單", "簡化")
_ZH_NEW_CLAIM_MARKERS = ("文件", "公司", "政策", "規範", "制度")
_EN_PREVIOUS_REFERENCE_MARKERS = ("previous answer", "previous response", "that")
_EN_TRANSFORM_MARKERS = (
    "summarize",
    "explain",
    "rephrase",
    "translate",
    "simplify",
    "more simply",
)
_EN_LANGUAGE_SWITCH_MARKERS = ("in english", "in chinese")
_EN_TARGET_MARKERS = ("previous", "that", "it", "answer", "response")


def classify_conversation_recall(question: str) -> Optional[ConversationRecallKind]:
    normalized_question = " ".join(question.casefold().split())
    if not normalized_question:
        return None

    for kind, patterns in _RECALL_PATTERNS:
        if any(pattern.fullmatch(normalized_question) for pattern in patterns):
            return kind
    return None


def classify_conversation_transform(
    question: str,
) -> Optional[ConversationTransformKind]:
    """Bounded safety fallback for context-only transforms.

    This recognizes a bounded combination of a previous-answer reference and a
    transformation behavior. It is not an enterprise question classifier and
    must never authorize a knowledge claim without a same-session assistant
    answer.
    """

    normalized_question = " ".join(question.casefold().split())
    has_zh_previous_reference = any(
        marker in normalized_question for marker in _ZH_PREVIOUS_REFERENCE_MARKERS
    )
    has_zh_answer_reference = any(
        marker in normalized_question for marker in _ZH_ASSISTANT_ANSWER_MARKERS
    )
    has_zh_transform_action = any(
        marker in normalized_question for marker in _ZH_TRANSFORM_MARKERS
    )
    explicit_zh_transform = (
        has_zh_previous_reference
        and has_zh_answer_reference
        and has_zh_transform_action
    )
    implicit_zh_language_switch = (
        any(marker in normalized_question for marker in _ZH_LANGUAGE_SWITCH_MARKERS)
        and any(marker in normalized_question for marker in _ZH_LANGUAGE_ACTION_MARKERS)
        and not any(marker in normalized_question for marker in _ZH_NEW_CLAIM_MARKERS)
    )
    implicit_zh_simplify = (
        any(marker in normalized_question for marker in _ZH_SIMPLIFY_MARKERS)
        and any(marker in normalized_question for marker in ("一點", "一些", "一點點"))
        and not any(marker in normalized_question for marker in _ZH_NEW_CLAIM_MARKERS)
    )
    is_zh_transform = (
        explicit_zh_transform
        or implicit_zh_language_switch
        or implicit_zh_simplify
    )
    is_en_transform = (
        (
            any(marker in normalized_question for marker in _EN_PREVIOUS_REFERENCE_MARKERS)
            and any(marker in normalized_question for marker in _EN_TRANSFORM_MARKERS)
        )
        or any(marker in normalized_question for marker in _EN_LANGUAGE_SWITCH_MARKERS)
        or (
            any(marker in normalized_question for marker in _EN_TRANSFORM_MARKERS)
            and any(marker in normalized_question for marker in _EN_TARGET_MARKERS)
        )
    )
    if is_zh_transform or is_en_transform:
        return ConversationTransformKind.PREVIOUS_ASSISTANT_TRANSFORM
    return None


__all__ = [
    "ConversationRecallKind",
    "ConversationTransformKind",
    "classify_conversation_recall",
    "classify_conversation_transform",
]
