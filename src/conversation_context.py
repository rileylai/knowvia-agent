from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence


DEFAULT_CONVERSATION_MESSAGE_LIMIT = 6
DEFAULT_CONVERSATION_TOKEN_BUDGET = 2048
DEFAULT_CONVERSATION_TITLE = "New conversation"


@dataclass(frozen=True)
class ConversationContextMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ConversationContext:
    messages: List[ConversationContextMessage]
    estimated_tokens: int

    @property
    def rendered_text(self) -> str:
        return "\n\n".join(
            f"[{message.role}] {message.content}" for message in self.messages
        )


def estimate_conversation_tokens(content: str) -> int:
    """Use a deterministic bounded estimate without adding a tokenizer dependency."""

    return max(1, math.ceil(len(content.strip()) / 4))


def build_conversation_title(
    content: str,
    *,
    max_chars: int = 48,
) -> str:
    normalized = content.strip()
    if not normalized:
        return DEFAULT_CONVERSATION_TITLE
    return normalized[:max_chars].rstrip() or DEFAULT_CONVERSATION_TITLE


def assemble_conversation_context(
    *,
    history: Sequence[ConversationContextMessage],
    current_question: str,
    max_messages: int = DEFAULT_CONVERSATION_MESSAGE_LIMIT,
    token_budget: int = DEFAULT_CONVERSATION_TOKEN_BUDGET,
) -> ConversationContext:
    if max_messages <= 0:
        raise ValueError("max_messages must be positive")
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")

    normalized_question = current_question.strip()
    if not normalized_question:
        raise ValueError("current_question must not be empty")

    current = ConversationContextMessage(role="user", content=normalized_question)
    recent_history = (
        list(history)[-(max_messages - 1) :]
        if max_messages > 1
        else []
    )
    candidates = recent_history + [current]

    selected_reversed: List[ConversationContextMessage] = []
    used_tokens = 0
    for message in reversed(candidates):
        message_tokens = estimate_conversation_tokens(message.content)
        if not selected_reversed and message is current:
            selected_reversed.append(message)
            used_tokens = message_tokens
            continue
        if used_tokens + message_tokens > token_budget:
            continue
        selected_reversed.append(message)
        used_tokens += message_tokens

    selected = list(reversed(selected_reversed))
    return ConversationContext(messages=selected, estimated_tokens=used_tokens)
