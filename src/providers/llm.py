from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional
from urllib import request as urllib_request

from src.observability.redaction import sanitize_sensitive_text
from src.providers.base import LLMProvider
from src.providers.models import LLMRequest, LLMResponse, LLMToolCall


class LLMClientError(Exception):
    pass


class BaseLLMClient(LLMProvider, ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError


TransportFn = Callable[[str, Dict[str, str], Dict[str, Any]], Dict[str, Any]]


class OpenAIClient(BaseLLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o-mini",
        transport: Optional[TransportFn] = None,
    ) -> None:
        normalized_key = api_key.strip()
        if not normalized_key:
            raise LLMClientError("api_key must not be empty")

        normalized_model = default_model.strip()
        if not normalized_model:
            raise LLMClientError("default_model must not be empty")

        self._api_key = normalized_key
        self._base_url = base_url.rstrip("/")
        self._default_model = normalized_model
        self._transport = transport or _default_transport

    @property
    def name(self) -> str:
        return "openai"

    @property
    def supports_tool_calling(self) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        model_name = request.model.strip() or self._default_model
        if not model_name:
            raise LLMClientError("model must not be empty")

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": [
                _serialize_message(message) for message in request.messages
            ],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools is not None:
            payload["tools"] = request.tools
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/chat/completions"

        try:
            raw_response = await asyncio.to_thread(self._transport, url, headers, payload)
        except Exception as exc:
            raise LLMClientError(
                f"LLM request failed: {sanitize_sensitive_text(str(exc))}"
            ) from exc

        try:
            choice = raw_response["choices"][0]
            message = choice["message"]
            output_text = _extract_output_text(message)
            tool_calls = _extract_tool_calls(message.get("tool_calls"))
            finish_reason = choice.get("finish_reason")
            usage = raw_response.get("usage", {})
            token_input = usage.get("prompt_tokens")
            token_output = usage.get("completion_tokens")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMClientError("LLM response schema is invalid") from exc

        return LLMResponse(
            provider=self.name,
            model=model_name,
            output_text=output_text,
            finish_reason=finish_reason,
            token_input=token_input,
            token_output=token_output,
            raw_response=raw_response,
            tool_calls=tool_calls,
        )


def _extract_output_text(message_payload: Dict[str, Any]) -> str:
    content = message_payload.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text_value = item.get("text")
            if isinstance(text_value, str):
                text_parts.append(text_value)
        if text_parts:
            return "\n".join(text_parts)
    raise ValueError("LLM output text is missing")


def _serialize_message(message) -> Dict[str, Any]:
    payload = message.model_dump(exclude_none=True)
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(
                        tool_call.arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }
            for tool_call in message.tool_calls
        ]
    return payload


def _extract_tool_calls(value: Any) -> List[LLMToolCall]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("LLM tool_calls must be a list")

    tool_calls: List[LLMToolCall] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("LLM tool_call is invalid")
        function = item.get("function")
        if not isinstance(function, dict):
            raise ValueError("LLM tool_call function is invalid")
        raw_arguments = function.get("arguments", "{}")
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, ValueError) as exc:
                raise ValueError("LLM tool_call arguments are invalid") from exc
        else:
            arguments = raw_arguments
        if not isinstance(arguments, dict):
            raise ValueError("LLM tool_call arguments must be an object")
        tool_calls.append(
            LLMToolCall(
                id=str(item.get("id") or "tool-call"),
                name=str(function.get("name") or ""),
                arguments=arguments,
            )
        )
    return tool_calls


def _default_transport(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(url=url, data=body, headers=headers, method="POST")
    with urllib_request.urlopen(req, timeout=60) as response:
        response_body = response.read().decode("utf-8")
    return json.loads(response_body)
