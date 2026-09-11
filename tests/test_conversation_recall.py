from __future__ import annotations

import pytest

from src.conversation_recall import (
    ConversationRecallKind,
    ConversationTransformKind,
    classify_conversation_recall,
    classify_conversation_transform,
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


@pytest.mark.parametrize(
    "question",
    [
        "聽不懂",
        "我聽不懂",
        "看不懂",
        "我看不懂",
        "不太懂",
        "我不太懂",
        "沒看懂",
        "I don't understand",
        "I do not understand",
        "I don't get it",
        "I didn't understand that",
        "詳細解釋",
        "詳細說明",
        "再詳細一點",
        "深入解釋",
        "展開說明",
        "explain in more detail",
        "elaborate",
        "elaborate on that",
        "go into more detail",
        "白話一點",
        "再白話一點",
        "講白話一點",
        "再講白話一點",
        "剛剛講的再白話一點",
        "你剛剛講的再白話一點",
        "講簡單一點",
        "換個方式說",
        "可以簡單解釋嗎",
        "rephrase that",
        "explain more simply",
        "剛剛第一點是什麼意思",
        "詳細解釋剛剛第二點",
        "剛剛第三點可以講白話一點嗎",
        "你剛剛講的第二點我聽不懂",
        "explain the second point",
        "elaborate on the third point above",
        "用英文解釋",
        "summarize previous answer",
        "簡單一點",
        "rephrase that more simply",
    ],
)
def test_classify_bounded_previous_assistant_transform_questions(
    question: str,
) -> None:
    assert (
        classify_conversation_transform(question)
        is ConversationTransformKind.PREVIOUS_ASSISTANT_TRANSFORM
    )


@pytest.mark.parametrize(
    "question",
    [
        "我不懂 OAuth 是什麼",
        "請詳細解釋 agentic system",
        "解釋 PostgreSQL transaction isolation",
        "OpenAI 的第二個產品是什麼",
        "第二點資料是從哪個 PDF 來的",
        "白話文是什麼意思",
        "請用白話解釋 agentic system",
        "再解釋一下 OAuth 是什麼",
        "公司的白話版文件在哪裡",
    ],
)
def test_classify_fresh_substantive_questions_as_non_transforms(question: str) -> None:
    assert classify_conversation_transform(question) is None
