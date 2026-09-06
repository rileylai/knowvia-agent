from __future__ import annotations

from enum import Enum


class ResponseLanguage(str, Enum):
    ENGLISH = "en"
    TRADITIONAL_CHINESE = "zh-Hant"
    SIMPLIFIED_CHINESE = "zh-Hans"


# These are script signals, not a language detection framework. Characters
# shared by both Chinese writing systems use the product default: Traditional
# Chinese. Mixed Chinese and English also follows the product default.
_TRADITIONAL_SIGNALS = frozenset(
    "這個為後來與發現說話記憶訊請問嗎還點國體驗實過時間開關門見長無學習選擇將變應該讓對於線網頁標題預設識讀寫總結構檢進"
)
_SIMPLIFIED_SIGNALS = frozenset(
    "这个为后来与发现说话记忆讯请问吗还点国体验实过时间开关门见长无学习选择将变应该让对于线网页标题预设识读写总结结构检进"
)

_EXPLICIT_ENGLISH_MARKERS = (
    "answer in english",
    "respond in english",
    "reply in english",
    "用英文",
    "以英文",
    "英文回答",
)
_EXPLICIT_TRADITIONAL_MARKERS = (
    "traditional chinese",
    "繁體中文",
    "用繁體",
    "以繁體",
)
_EXPLICIT_SIMPLIFIED_MARKERS = (
    "simplified chinese",
    "简体中文",
    "用简体",
    "以简体",
)
_EXPLICIT_TRADITIONAL_CHINESE_MARKERS = ("in chinese", "用中文", "以中文")


def resolve_response_language(message: str) -> ResponseLanguage:
    normalized = " ".join(message.casefold().split())
    explicit = _resolve_explicit_language(normalized)
    if explicit is not None:
        return explicit

    chinese_chars = [character for character in normalized if _is_cjk(character)]
    if not chinese_chars:
        return ResponseLanguage.ENGLISH

    if any(character.isascii() and character.isalpha() for character in normalized):
        return ResponseLanguage.TRADITIONAL_CHINESE

    has_traditional_signal = any(
        character in _TRADITIONAL_SIGNALS for character in chinese_chars
    )
    has_simplified_signal = any(
        character in _SIMPLIFIED_SIGNALS for character in chinese_chars
    )
    if has_traditional_signal:
        return ResponseLanguage.TRADITIONAL_CHINESE
    if has_simplified_signal:
        return ResponseLanguage.SIMPLIFIED_CHINESE
    return ResponseLanguage.TRADITIONAL_CHINESE


def response_language_instruction(language: ResponseLanguage) -> str:
    language_name = {
        ResponseLanguage.ENGLISH: "English",
        ResponseLanguage.TRADITIONAL_CHINESE: "Traditional Chinese",
        ResponseLanguage.SIMPLIFIED_CHINESE: "Simplified Chinese",
    }[language]
    return (
        "FINAL_RESPONSE_LANGUAGE: "
        f"{language_name}. Choose the final response language from the current user "
        "message only. Do not infer it from conversation history, saved memory, "
        "or retrieved knowledge. Preserve technical names and code identifiers."
    )


def insufficient_info_answer(language: ResponseLanguage) -> str:
    return {
        ResponseLanguage.ENGLISH: (
            "I do not have enough information in production notes to answer safely."
        ),
        ResponseLanguage.TRADITIONAL_CHINESE: "生產文件中沒有足夠資訊，無法安全回答。",
        ResponseLanguage.SIMPLIFIED_CHINESE: "生产文件中没有足够信息，无法安全回答。",
    }[language]


def conversation_context_unavailable_answer(language: ResponseLanguage) -> str:
    return {
        ResponseLanguage.ENGLISH: (
            "There is no earlier assistant answer in this conversation to transform."
        ),
        ResponseLanguage.TRADITIONAL_CHINESE: "此對話中沒有更早的 assistant 回答可供重述。",
        ResponseLanguage.SIMPLIFIED_CHINESE: "此对话中没有更早的 assistant 回答可供重述。",
    }[language]


def conversation_recall_unavailable_answer(language: ResponseLanguage) -> str:
    return {
        ResponseLanguage.ENGLISH: "There are no earlier user messages in this conversation.",
        ResponseLanguage.TRADITIONAL_CHINESE: "此對話中沒有更早的 user 訊息。",
        ResponseLanguage.SIMPLIFIED_CHINESE: "此对话中没有更早的 user 消息。",
    }[language]


def conversation_insufficient_info_answer(language: ResponseLanguage) -> str:
    return {
        ResponseLanguage.ENGLISH: (
            "I do not have enough earlier conversation context to answer that."
        ),
        ResponseLanguage.TRADITIONAL_CHINESE: "沒有足夠的早期對話內容可回答這個問題。",
        ResponseLanguage.SIMPLIFIED_CHINESE: "没有足够的早期对话内容可回答这个问题。",
    }[language]


def memory_confirmation(language: ResponseLanguage, status: str) -> str:
    saved = status == "saved"
    return {
        ResponseLanguage.ENGLISH: "Memory saved" if saved else "Already saved",
        ResponseLanguage.TRADITIONAL_CHINESE: "已儲存記憶" if saved else "記憶已存在",
        ResponseLanguage.SIMPLIFIED_CHINESE: "已保存记忆" if saved else "记忆已存在",
    }[language]


def _resolve_explicit_language(normalized: str) -> ResponseLanguage | None:
    if any(marker in normalized for marker in _EXPLICIT_ENGLISH_MARKERS):
        return ResponseLanguage.ENGLISH
    if any(marker in normalized for marker in _EXPLICIT_TRADITIONAL_MARKERS):
        return ResponseLanguage.TRADITIONAL_CHINESE
    if any(marker in normalized for marker in _EXPLICIT_SIMPLIFIED_MARKERS):
        return ResponseLanguage.SIMPLIFIED_CHINESE
    if any(marker in normalized for marker in _EXPLICIT_TRADITIONAL_CHINESE_MARKERS):
        return ResponseLanguage.TRADITIONAL_CHINESE
    return None


def _is_cjk(character: str) -> bool:
    return "\u4e00" <= character <= "\u9fff"


__all__ = [
    "ResponseLanguage",
    "conversation_context_unavailable_answer",
    "conversation_insufficient_info_answer",
    "conversation_recall_unavailable_answer",
    "insufficient_info_answer",
    "memory_confirmation",
    "resolve_response_language",
    "response_language_instruction",
]
