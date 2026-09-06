import asyncio
from typing import Any, Dict

import pytest

from src.providers import (
    LLMClientError,
    LLMMessage,
    LLMRequest,
    LLMToolCall,
    OpenAIClient,
    ProviderRouter,
)


def test_openai_client_returns_llm_response_with_mock_transport() -> None:
    captured_payload: Dict[str, Any] = {}
    captured_headers: Dict[str, str] = {}
    captured_url = ""

    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        nonlocal captured_url
        captured_url = url
        captured_headers.update(headers)
        captured_payload.update(payload)
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "answer text"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 21, "completion_tokens": 9},
        }

    client = OpenAIClient(
        api_key="test-key",
        default_model="gpt-4o-mini",
        transport=fake_transport,
    )
    request = LLMRequest(
        model="gpt-4o-mini",
        messages=[LLMMessage(role="user", content="what is attention?")],
        temperature=0.1,
        max_tokens=200,
    )

    response = asyncio.run(client.generate(request))

    assert captured_url == "https://api.openai.com/v1/chat/completions"
    assert captured_headers["Authorization"] == "Bearer test-key"
    assert captured_payload["model"] == "gpt-4o-mini"
    assert captured_payload["messages"][0]["content"] == "what is attention?"
    assert captured_payload["temperature"] == 0.1
    assert captured_payload["max_tokens"] == 200

    assert response.provider == "openai"
    assert response.model == "gpt-4o-mini"
    assert response.output_text == "answer text"
    assert response.finish_reason == "stop"
    assert response.token_input == 21
    assert response.token_output == 9


def test_openai_client_supports_content_part_array_output() -> None:
    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers, payload
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "line one"},
                            {"type": "image_url", "image_url": "ignored"},
                            {"type": "text", "text": "line two"},
                        ],
                    },
                    "finish_reason": "stop",
                }
            ]
        }

    client = OpenAIClient(api_key="test-key", transport=fake_transport)
    request = LLMRequest(
        model="gpt-4o-mini",
        messages=[LLMMessage(role="user", content="hello")],
    )

    response = asyncio.run(client.generate(request))

    assert response.output_text == "line one\nline two"


def test_openai_client_parses_bounded_tool_calls() -> None:
    captured_payload: Dict[str, Any] = {}

    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers
        captured_payload.update(payload)
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "search_memory",
                                    "arguments": '{"query":"What did we decide?"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }

    client = OpenAIClient(api_key="test-key", transport=fake_transport)
    request = LLMRequest(
        model="gpt-4o-mini",
        messages=[LLMMessage(role="user", content="What did we decide?")],
        tools=[
            {
                "type": "function",
                "function": {"name": "search_memory", "parameters": {}},
            }
        ],
    )

    response = asyncio.run(client.generate(request))

    assert captured_payload["tools"][0]["function"]["name"] == "search_memory"
    assert response.output_text == ""
    assert response.tool_calls[0].name == "search_memory"
    assert response.tool_calls[0].arguments == {"query": "What did we decide?"}


def test_openai_client_serializes_follow_up_tool_messages_for_api_wire_format() -> None:
    captured_payload: Dict[str, Any] = {}

    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers
        captured_payload.update(payload)
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ]
        }

    client = OpenAIClient(api_key="test-key", transport=fake_transport)
    request = LLMRequest(
        model="gpt-4o-mini",
        messages=[
            LLMMessage(
                role="assistant",
                tool_calls=[
                    LLMToolCall(
                        id="call-1",
                        name="search_memory",
                        arguments={"query": "決策"},
                    )
                ],
            ),
            LLMMessage(
                role="tool",
                name="search_memory",
                tool_call_id="call-1",
                content="[saved_memory]",
            ),
        ],
    )

    asyncio.run(client.generate(request))

    wire_call = captured_payload["messages"][0]["tool_calls"][0]
    assert wire_call["type"] == "function"
    assert wire_call["function"]["arguments"] == '{"query":"決策"}'


def test_openai_client_raises_error_for_invalid_transport_output() -> None:
    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers, payload
        return {"unexpected": "shape"}

    client = OpenAIClient(
        api_key="test-key",
        transport=fake_transport,
    )
    request = LLMRequest(
        model="gpt-4o-mini",
        messages=[LLMMessage(role="user", content="hello")],
    )

    with pytest.raises(LLMClientError):
        asyncio.run(client.generate(request))


def test_openai_client_rejects_empty_api_key() -> None:
    with pytest.raises(LLMClientError):
        OpenAIClient(api_key="  ")


def test_provider_router_routes_with_openai_client() -> None:
    def fake_transport(
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        _ = url, headers, payload
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "router output"},
                    "finish_reason": "stop",
                }
            ]
        }

    router = ProviderRouter()
    router.register_provider(OpenAIClient(api_key="test-key", transport=fake_transport))
    request = LLMRequest(
        model="gpt-4o-mini",
        messages=[LLMMessage(role="user", content="route me")],
    )

    response = asyncio.run(router.route("openai", request))
    assert response.output_text == "router output"
    assert response.provider == "openai"
