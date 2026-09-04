from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from src.tools.models import ToolContext, ToolResult, ToolSpec


class Tool(ABC):
    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        raise NotImplementedError

    @abstractmethod
    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError
