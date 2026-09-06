from __future__ import annotations

import pytest

from src.response_language import (
    ResponseLanguage,
    insufficient_info_answer,
    resolve_response_language,
    response_language_instruction,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("What are my preferences?", ResponseLanguage.ENGLISH),
        ("我有哪些偏好？", ResponseLanguage.TRADITIONAL_CHINESE),
        ("我有哪些 API response 偏好？", ResponseLanguage.TRADITIONAL_CHINESE),
        ("請解釋 pgvector retrieval 的流程", ResponseLanguage.TRADITIONAL_CHINESE),
        ("我的记忆有哪些？", ResponseLanguage.SIMPLIFIED_CHINESE),
        ("Please answer in English: 我有哪些偏好？", ResponseLanguage.ENGLISH),
        ("請用繁體中文回答：What are my preferences?", ResponseLanguage.TRADITIONAL_CHINESE),
        ("请用简体中文回答：What are my preferences?", ResponseLanguage.SIMPLIFIED_CHINESE),
        ("用英文說你剛才的回答", ResponseLanguage.ENGLISH),
    ),
)
def test_resolve_response_language_uses_current_message_only(
    message: str,
    expected: ResponseLanguage,
) -> None:
    assert resolve_response_language(message) is expected


def test_response_language_instruction_is_provider_facing_and_bounded() -> None:
    instruction = response_language_instruction(ResponseLanguage.TRADITIONAL_CHINESE)

    assert "Traditional Chinese" in instruction
    assert "current user message" in instruction
    assert "conversation history" in instruction
    assert "saved memory" in instruction
    assert "retrieved knowledge" in instruction


def test_fixed_insufficient_info_answer_follows_resolved_language() -> None:
    assert insufficient_info_answer(ResponseLanguage.ENGLISH).startswith("I do not")
    assert "足夠" in insufficient_info_answer(ResponseLanguage.TRADITIONAL_CHINESE)
    assert "足够" in insufficient_info_answer(ResponseLanguage.SIMPLIFIED_CHINESE)
