from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


MEMORY_TYPE_DECISION = "decision"
MEMORY_TYPE_PREFERENCE = "preference"
MEMORY_TYPE_PROJECT_CONTEXT = "project_context"
ALLOWED_MEMORY_TYPES = frozenset(
    {
        MEMORY_TYPE_DECISION,
        MEMORY_TYPE_PREFERENCE,
        MEMORY_TYPE_PROJECT_CONTEXT,
    }
)
MAX_MEMORY_CONTENT_CHARS = 2000

_EXPLICIT_SAVE_PATTERNS = (
    re.compile(r"^\s*(?:記住|請記住|請幫我記住)\s*[,，:：]?\s*(?P<content>.+?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*以後請記得\s*[,，:：]?\s*(?P<content>.+?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:remember that|please remember)\s*[:,]?\s*(?P<content>.+?)\s*$", re.IGNORECASE),
)

# These are deliberately bounded personal/decision question shapes, not a
# general intent classifier. Other enterprise questions keep the existing QA path.
_DIRECT_MEMORY_RECALL_PATTERNS = (
    re.compile(r"^\s*(?:你記得)?我的[^?？\n]{1,64}[?？]\s*$", re.IGNORECASE),
    re.compile(
        r"^\s*what\s+(?:is|'s|are)\s+(?:my|our)\b[^?!.]{1,80}[?!.]\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*what\s+do\s+i\b[^?!.]{1,80}[?!.]\s*$", re.IGNORECASE),
    re.compile(
        r"^\s*what\s+did\s+we\s+decide\b[^?!.]{0,80}[?!.]\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*do\s+you\s+remember\s+my\b[^?!.]{1,80}[?!.]\s*$",
        re.IGNORECASE,
    ),
)

_BROAD_MEMORY_RECALL_PATTERNS = (
    re.compile(
        r"^\s*你記得我(?:的)?(?:哪些事情|什麼(?:事情)?)(?:嗎)?\s*[?？]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*你有記住我(?:的)?什麼資訊(?:嗎)?\s*[?？]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*what\s+do\s+you\s+(?:remember|know)\s+about\s+me\s*[?!.]*\s*$",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ExplicitSaveIntent:
    content: str
    memory_type: str


def normalize_memory_content(content: str) -> str:
    normalized = " ".join(content.strip().split())
    if not normalized:
        raise ValueError("memory content must not be empty")
    if len(normalized) > MAX_MEMORY_CONTENT_CHARS:
        raise ValueError(
            f"memory content must be at most {MAX_MEMORY_CONTENT_CHARS} characters"
        )
    return normalized


def normalize_memory_duplicate_key(content: str) -> str:
    return normalize_memory_content(content).casefold()


def classify_memory_type(content: str) -> str:
    normalized = content.casefold()
    if any(
        marker in normalized
        for marker in (
            "偏好",
            "喜歡",
            "習慣",
            "preference",
            "prefer",
            "like to",
        )
    ):
        return MEMORY_TYPE_PREFERENCE
    if any(
        marker in normalized
        for marker in (
            "決定",
            "選擇",
            "採用",
            "確定",
            "decision",
            "decided",
            "choose",
            "chose",
            "selected",
            "adopt",
        )
    ):
        return MEMORY_TYPE_DECISION
    return MEMORY_TYPE_PROJECT_CONTEXT


def detect_explicit_save_intent(value: str) -> Optional[ExplicitSaveIntent]:
    if not isinstance(value, str):
        return None
    for pattern in _EXPLICIT_SAVE_PATTERNS:
        match = pattern.match(value)
        if match is None:
            continue
        content = normalize_memory_content(match.group("content"))
        return ExplicitSaveIntent(
            content=content,
            memory_type=classify_memory_type(content),
        )
    return None


def is_memory_recall_query(value: str) -> bool:
    if any(pattern.match(value) for pattern in _DIRECT_MEMORY_RECALL_PATTERNS):
        return True

    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in (
            "記住",
            "記得",
            "保存",
            "memory",
            "remember",
            "what do you know about me",
            "偏好",
            "喜歡",
            "習慣",
            "決定",
            "選擇",
            "preference",
            "decision",
        )
    )


def memory_recall_type_filter(value: str) -> Optional[str]:
    normalized = " ".join(value.casefold().split())
    if "偏好" in normalized or "preference" in normalized:
        return MEMORY_TYPE_PREFERENCE
    return None


def is_broad_memory_recall_query(value: str) -> bool:
    normalized = " ".join(value.casefold().split())
    if memory_recall_type_filter(normalized) == MEMORY_TYPE_PREFERENCE:
        return any(marker in normalized for marker in ("什麼", "哪些", "preferences"))
    is_zh_saved_context_overview = (
        any(marker in normalized for marker in ("記得", "記住", "保存"))
        and any(
            marker in normalized
            for marker in ("什麼資訊", "哪些資訊", "什麼事情", "哪些事情")
        )
    )
    if is_zh_saved_context_overview:
        return True
    return any(
        pattern.fullmatch(normalized)
        for pattern in _BROAD_MEMORY_RECALL_PATTERNS
    )
