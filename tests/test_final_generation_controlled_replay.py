from __future__ import annotations

import asyncio
from typing import List

import pytest

from scripts.final_generation_controlled_replay import (
    CapturingProvider,
    ControlledReplayError,
    ReplayOutcome,
    build_fresh_authority_request,
    build_without_prior_substantive_user_requests,
    build_without_previous_assistant_request,
    build_without_transform_user_requests,
    describe_user_history,
    replay_request,
)
from src.providers import LLMMessage, LLMProvider, LLMRequest, LLMResponse
from src.services import format_untrusted_prompt_block


class RecordingProvider(LLMProvider):
    supports_tool_calling = True
    supports_structured_output = True

    def __init__(self, outputs: List[str] | None = None) -> None:
        self.outputs = list(outputs or ["answer"])
        self.requests: List[LLMRequest] = []

    @property
    def name(self) -> str:
        return "recording"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request.model_copy(deep=True))
        return LLMResponse(
            provider=self.name,
            model=request.model,
            output_text=self.outputs.pop(0),
        )


def _final_request(*, previous_answer: str = "previous answer") -> LLMRequest:
    query = "current repeated user"
    conversation_context = "\n\n".join(
        [
            "[user] earlier user",
            "[assistant] earlier assistant",
            "[user] previous mixed user",
            f"[assistant] {previous_answer}",
            f"[user] {query}",
        ]
    )
    user_content = "\n\n".join(
        [
            format_untrusted_prompt_block(label="USER_MESSAGE", value=query),
            format_untrusted_prompt_block(
                label="CONVERSATION_CONTEXT",
                value=conversation_context,
            ),
        ]
    )
    return LLMRequest(
        model="fixture-model",
        messages=[
            LLMMessage(role="system", content="final system contract"),
            LLMMessage(role="user", content=user_content),
            LLMMessage(
                role="system",
                content="KNOWLEDGE_CONTEXT=fixed\nMEMORY_CONTEXT=fixed",
            ),
        ],
        temperature=0.2,
        max_tokens=500,
        metadata={"operation": "bounded_agent_final"},
    )


def _isolated_final_request(*, include_transform: bool = True) -> LLMRequest:
    query = "current repeated user"
    history = [query, query]
    if include_transform:
        history.append("translate previous answer")
    history.append(query)
    user_content = "\n\n".join(
        [
            format_untrusted_prompt_block(label="USER_MESSAGE", value=query),
            format_untrusted_prompt_block(
                label="CONVERSATION_CONTEXT",
                value="\n\n".join(f"[user] {value}" for value in history),
            ),
        ]
    )
    return LLMRequest(
        model="fixture-model",
        messages=[
            LLMMessage(role="system", content="final system contract"),
            LLMMessage(role="user", content=user_content),
            LLMMessage(
                role="system",
                content="KNOWLEDGE_CONTEXT=fixed\nMEMORY_CONTEXT=fixed",
            ),
        ],
        temperature=0.2,
        max_tokens=500,
        metadata={"operation": "bounded_agent_final"},
    )


def test_capture_is_disabled_by_default_and_delegates_unchanged() -> None:
    provider = RecordingProvider()
    wrapper = CapturingProvider(provider)
    request = _final_request()

    asyncio.run(wrapper.generate(request))

    assert wrapper.captured_count == 0
    assert provider.requests == [request]


def test_capture_is_memory_only_does_not_log_content_and_returns_a_deep_copy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = RecordingProvider()
    wrapper = CapturingProvider(provider)
    wrapper.enable_capture()
    request = _final_request()

    asyncio.run(wrapper.generate(request))
    captured = wrapper.take_latest()
    captured.messages[-1].content = "mutated"

    assert wrapper.captured_count == 0
    assert provider.requests[0].messages[-1].content.endswith("fixed")
    assert not hasattr(wrapper, "persist")
    assert "KNOWLEDGE_CONTEXT=fixed" not in caplog.text


def test_variant_b_removes_only_the_selected_previous_assistant() -> None:
    request = _final_request()

    variant = build_without_previous_assistant_request(
        request,
        previous_answer="previous answer",
        current_query="current repeated user",
    )

    expected = request.model_copy(deep=True)
    expected.messages[1].content = expected.messages[1].content.replace(
        "\n\n[assistant] previous answer\n\n[user] current repeated user",
        "\n\n[user] current repeated user",
        1,
    )
    assert variant == expected
    assert variant.messages[-1] == request.messages[-1]
    assert variant.model_dump(exclude={"messages"}) == request.model_dump(
        exclude={"messages"}
    )


def test_variant_b_fails_closed_when_target_is_not_unique() -> None:
    request = _final_request(previous_answer="same")
    request.messages[1].content = request.messages[1].content.replace(
        "[assistant] earlier assistant",
        "[assistant] same\n\n[user] current repeated user",
    )

    with pytest.raises(ControlledReplayError):
        build_without_previous_assistant_request(
            request,
            previous_answer="same",
            current_query="current repeated user",
        )


def test_variant_c_preserves_fresh_authority_and_removes_conversation_history() -> None:
    request = _final_request()

    variant = build_fresh_authority_request(
        request,
        current_query="current repeated user",
    )

    assert "CONVERSATION_CONTEXT" not in variant.messages[1].content
    assert variant.messages[1].content == format_untrusted_prompt_block(
        label="USER_MESSAGE",
        value="current repeated user",
    )
    assert variant.messages[0] == request.messages[0]
    assert variant.messages[-1] == request.messages[-1]


def test_user_history_variant_removes_only_prior_substantive_requests() -> None:
    request = _isolated_final_request()

    variant, removed_count = build_without_prior_substantive_user_requests(
        request,
        current_query="current repeated user",
    )

    assert removed_count == 2
    assert variant.messages[1].content.count("[user] current repeated user") == 1
    assert "[user] translate previous answer" in variant.messages[1].content
    assert variant.messages[0] == request.messages[0]
    assert variant.messages[-1] == request.messages[-1]


def test_transform_history_variant_removes_only_known_transform_request() -> None:
    request = _isolated_final_request()

    variant, removed_count = build_without_transform_user_requests(
        request,
        transform_query="translate previous answer",
    )

    assert variant is not None
    assert removed_count == 1
    assert "translate previous answer" not in variant.messages[1].content
    assert variant.messages[1].content.count("[user] current repeated user") == 3
    assert variant.messages[-1] == request.messages[-1]


def test_transform_history_variant_is_not_applicable_without_transform() -> None:
    request = _isolated_final_request(include_transform=False)

    variant, removed_count = build_without_transform_user_requests(
        request,
        transform_query="translate previous answer",
    )

    assert variant is None
    assert removed_count == 0


def test_user_history_description_exposes_only_bounded_categories() -> None:
    request = _isolated_final_request()

    description = describe_user_history(
        request,
        current_query="current repeated user",
        transform_query="translate previous answer",
    )

    assert description == {
        "message_count": 4,
        "role_sequence": ["user", "user", "user", "user"],
        "previous_user_categories": [
            "substantive",
            "substantive",
            "transform",
        ],
    }
    assert "current repeated user" not in str(description)


def test_replay_is_bounded_and_does_not_call_tools() -> None:
    provider = RecordingProvider(["answer", "INSUFFICIENT_INFO", ""])
    request = _final_request()

    outcomes = asyncio.run(replay_request(provider, request, attempts=3))

    assert outcomes == [
        ReplayOutcome.FINAL_TEXT,
        ReplayOutcome.INSUFFICIENT_SENTINEL,
        ReplayOutcome.MALFORMED,
    ]
    assert provider.requests == [request, request, request]
    with pytest.raises(ControlledReplayError):
        asyncio.run(replay_request(provider, request, attempts=4))
