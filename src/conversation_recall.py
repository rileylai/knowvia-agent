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
_ZH_BARE_COMPREHENSION_FAILURES = (
    "聽不懂",
    "我聽不懂",
    "看不懂",
    "我看不懂",
    "不太懂",
    "我不太懂",
    "沒看懂",
)
_EN_BARE_COMPREHENSION_FAILURES = (
    "i don't understand",
    "i do not understand",
    "i don't get it",
    "i didn't understand that",
)
_ZH_BARE_ELABORATION_REQUESTS = (
    "詳細解釋",
    "詳細說明",
    "再詳細一點",
    "深入解釋",
    "展開說明",
)
_EN_BARE_ELABORATION_REQUESTS = (
    "explain in more detail",
    "elaborate",
    "elaborate on that",
    "go into more detail",
)
_ZH_BARE_REPHRASE_REQUESTS = (
    "白話一點",
    "再白話一點",
    "講白話一點",
    "再講白話一點",
    "講簡單一點",
    "換個方式說",
    "可以簡單解釋嗎",
)
_EN_BARE_REPHRASE_REQUESTS = (
    "rephrase that",
    "explain more simply",
)
_ZH_ORDINAL_POINT = r"第(?:[一二三四五六七八九十]+|\d+)點"
_ZH_PREVIOUS_ANSWER_REPHRASE_PATTERNS = (
    re.compile(r"^(?:你)?剛剛講的(?:再)?白話一點$"),
)
_ZH_NUMBERED_TRANSFORM_PATTERNS = (
    re.compile(rf"^剛剛{_ZH_ORDINAL_POINT}是什麼意思$"),
    re.compile(
        rf"^(?:詳細解釋|詳細說明|深入解釋|展開說明)剛剛{_ZH_ORDINAL_POINT}$"
    ),
    re.compile(
        rf"^剛剛{_ZH_ORDINAL_POINT}可以(?:講|說|解釋)"
        r"(?:白話|簡單)(?:一點|一些|一點點)(?:嗎)?$"
    ),
    re.compile(
        rf"^你剛剛(?:講的|說的){_ZH_ORDINAL_POINT}"
        r"(?:我)?(?:聽不懂|看不懂|不太懂)$"
    ),
    re.compile(
        rf"^(?:聽不懂|我聽不懂|看不懂|我看不懂|不太懂|我不太懂|沒看懂)"
        rf"你剛剛(?:講的|說的){_ZH_ORDINAL_POINT}$"
    ),
)
_EN_ORDINAL_POINT = (
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
    r"(?: point| item)"
)
_EN_NUMBERED_TRANSFORM_PATTERNS = (
    re.compile(rf"^(?:explain|clarify) the {_EN_ORDINAL_POINT}(?: above)?$"),
    re.compile(
        rf"^(?:elaborate on|go into more detail on) the {_EN_ORDINAL_POINT}"
        r"(?: above)?$"
    ),
)


def _strip_terminal_punctuation(value: str) -> str:
    return value.rstrip("?!。！？")


def _matches_bounded_phrase(value: str, phrases: tuple[str, ...]) -> bool:
    return _strip_terminal_punctuation(value) in phrases


def _matches_bounded_pattern(
    value: str,
    patterns: tuple[re.Pattern[str], ...],
) -> bool:
    return any(pattern.fullmatch(_strip_terminal_punctuation(value)) for pattern in patterns)


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
    question_without_terminal_punctuation = _strip_terminal_punctuation(
        normalized_question
    )
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
    bounded_zh_bare_transform = (
        _matches_bounded_phrase(
            question_without_terminal_punctuation,
            _ZH_BARE_COMPREHENSION_FAILURES
            + _ZH_BARE_ELABORATION_REQUESTS
            + _ZH_BARE_REPHRASE_REQUESTS,
        )
        or _matches_bounded_pattern(
            question_without_terminal_punctuation,
            _ZH_PREVIOUS_ANSWER_REPHRASE_PATTERNS
            + _ZH_NUMBERED_TRANSFORM_PATTERNS,
        )
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
        or _matches_bounded_phrase(
            question_without_terminal_punctuation,
            _EN_BARE_COMPREHENSION_FAILURES
            + _EN_BARE_ELABORATION_REQUESTS
            + _EN_BARE_REPHRASE_REQUESTS,
        )
        or _matches_bounded_pattern(
            question_without_terminal_punctuation,
            _EN_NUMBERED_TRANSFORM_PATTERNS,
        )
    )
    if is_zh_transform or bounded_zh_bare_transform or is_en_transform:
        return ConversationTransformKind.PREVIOUS_ASSISTANT_TRANSFORM
    return None


__all__ = [
    "ConversationRecallKind",
    "ConversationTransformKind",
    "classify_conversation_recall",
    "classify_conversation_transform",
]
