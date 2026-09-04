from __future__ import annotations

from abc import ABC, abstractmethod

from src.providers.models import LLMRequest, LLMResponse


class LLMProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError
