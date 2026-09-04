import asyncio
from typing import Optional

import pytest

from src.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderAlreadyRegisteredError,
    ProviderNotFoundError,
    ProviderRouter,
)


class FakeProvider(LLMProvider):
    def __init__(self, name: str) -> None:
        self._name = name
        self.last_request: Optional[LLMRequest] = None

    @property
    def name(self) -> str:
        return self._name

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        content = request.messages[-1].content
        return LLMResponse(
            provider=self.name,
            model=request.model,
            output_text=f"echo: {content}",
        )


def test_provider_router_registers_and_routes_request() -> None:
    router = ProviderRouter()
    fake_provider = FakeProvider(name="Fake")
    router.register_provider(fake_provider)

    request = LLMRequest(
        model="fake-model",
        messages=[LLMMessage(role="user", content="hello provider router")],
    )

    response = asyncio.run(router.route("fake", request))

    assert response.provider == "Fake"
    assert response.model == "fake-model"
    assert response.output_text == "echo: hello provider router"
    assert fake_provider.last_request == request


def test_provider_router_rejects_duplicate_provider_name() -> None:
    router = ProviderRouter()
    router.register_provider(FakeProvider(name="fake"))

    with pytest.raises(ProviderAlreadyRegisteredError):
        router.register_provider(FakeProvider(name="FAKE"))


def test_provider_router_returns_deterministic_missing_provider_error() -> None:
    router = ProviderRouter()
    request = LLMRequest(
        model="fake-model",
        messages=[LLMMessage(role="user", content="hello")],
    )

    with pytest.raises(ProviderNotFoundError):
        asyncio.run(router.route("missing-provider", request))
