from __future__ import annotations

from src.services.conversation_context import (
    ConversationContextMessage,
    assemble_conversation_context,
    build_conversation_title,
)


def test_assemble_conversation_context_keeps_recent_six_messages_and_current_question() -> None:
    history = [
        ConversationContextMessage(role="user", content=f"message {index}")
        for index in range(1, 7)
    ]

    result = assemble_conversation_context(
        history=history,
        current_question="current question",
        max_messages=6,
        token_budget=200,
    )

    assert [message.content for message in result.messages] == [
        "message 2",
        "message 3",
        "message 4",
        "message 5",
        "message 6",
        "current question",
    ]


def test_assemble_conversation_context_drops_oldest_messages_when_budget_is_exceeded() -> None:
    history = [
        ConversationContextMessage(role="user", content="oldest context"),
        ConversationContextMessage(role="assistant", content="recent context"),
    ]

    result = assemble_conversation_context(
        history=history,
        current_question="current question",
        max_messages=6,
        token_budget=8,
    )

    assert [message.content for message in result.messages] == [
        "recent context",
        "current question",
    ]
    assert result.messages[-1].content == "current question"


def test_build_conversation_title_uses_bounded_first_user_message() -> None:
    assert build_conversation_title("  A question about agentic workflows  ") == (
        "A question about agentic workflows"
    )
    assert build_conversation_title("x" * 60) == "x" * 48
    assert build_conversation_title("   ") == "New conversation"


def test_assemble_conversation_context_with_one_message_limit_keeps_only_current_question() -> None:
    result = assemble_conversation_context(
        history=[ConversationContextMessage(role="assistant", content="old answer")],
        current_question="current question",
        max_messages=1,
        token_budget=100,
    )

    assert [message.content for message in result.messages] == ["current question"]
