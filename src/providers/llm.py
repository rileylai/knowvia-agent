from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional
from urllib import request as urllib_request

from src.observability.redaction import sanitize_sensitive_text
from src.providers.base import LLMProvider
from src.providers.models import LLMRequest, LLMResponse


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

    async def generate(self, request: LLMRequest) -> LLMResponse:
        model_name = request.model.strip() or self._default_model
        if not model_name:
            raise LLMClientError("model must not be empty")

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": [message.model_dump() for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

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
            output_text = _extract_output_text(choice["message"])
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
        )


def _extract_output_text(message_payload: Dict[str, Any]) -> str:
    content = message_payload.get("content")
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
