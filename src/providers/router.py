from __future__ import annotations

from typing import Dict, List

from src.providers.base import LLMProvider
from src.providers.models import LLMRequest, LLMResponse


class ProviderRouterError(Exception):
    """Base error for provider router failures."""


class ProviderNameInvalidError(ProviderRouterError):
    def __init__(self, provider_name: str) -> None:
        super().__init__(f"Provider name is invalid: '{provider_name}'")


class ProviderAlreadyRegisteredError(ProviderRouterError):
    def __init__(self, provider_name: str) -> None:
        super().__init__(f"Provider is already registered: '{provider_name}'")


class ProviderNotFoundError(ProviderRouterError):
    def __init__(self, provider_name: str) -> None:
        super().__init__(f"Provider is not registered: '{provider_name}'")


def _normalize_provider_name(provider_name: str) -> str:
    normalized = provider_name.strip().lower()
    if not normalized:
        raise ProviderNameInvalidError(provider_name)
    return normalized


class ProviderRouter:
    def __init__(self) -> None:
        self._providers: Dict[str, LLMProvider] = {}

    def register_provider(self, provider: LLMProvider) -> None:
        provider_name = _normalize_provider_name(provider.name)
        if provider_name in self._providers:
            raise ProviderAlreadyRegisteredError(provider_name)
        self._providers[provider_name] = provider

    def get_provider(self, provider_name: str) -> LLMProvider:
        normalized_name = _normalize_provider_name(provider_name)
        provider = self._providers.get(normalized_name)
        if provider is None:
            raise ProviderNotFoundError(normalized_name)
        return provider

    def has_provider(self, provider_name: str) -> bool:
        normalized_name = _normalize_provider_name(provider_name)
        return normalized_name in self._providers

    def list_provider_names(self) -> List[str]:
        return sorted(self._providers.keys())

    async def route(self, provider_name: str, request: LLMRequest) -> LLMResponse:
        provider = self.get_provider(provider_name)
        return await provider.generate(request)
