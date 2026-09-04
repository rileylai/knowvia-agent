from __future__ import annotations

from typing import Any, Dict, List

from src.tools.base import Tool
from src.tools.models import ToolContext, ToolResult


class ToolRegistryError(Exception):
    """Base error for tool registry failures."""


class ToolNameInvalidError(ToolRegistryError):
    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool name is invalid: '{tool_name}'")


class ToolAlreadyRegisteredError(ToolRegistryError):
    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool is already registered: '{tool_name}'")


class ToolNotFoundError(ToolRegistryError):
    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool is not registered: '{tool_name}'")


def _normalize_tool_name(tool_name: str) -> str:
    normalized = tool_name.strip().lower()
    if not normalized:
        raise ToolNameInvalidError(tool_name)
    return normalized


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register_tool(self, tool: Tool) -> None:
        tool_name = _normalize_tool_name(tool.spec.name)
        if tool_name in self._tools:
            raise ToolAlreadyRegisteredError(tool_name)
        self._tools[tool_name] = tool

    def get_tool(self, tool_name: str) -> Tool:
        normalized_name = _normalize_tool_name(tool_name)
        tool = self._tools.get(normalized_name)
        if tool is None:
            raise ToolNotFoundError(normalized_name)
        return tool

    def has_tool(self, tool_name: str) -> bool:
        normalized_name = _normalize_tool_name(tool_name)
        return normalized_name in self._tools

    def list_tool_names(self) -> List[str]:
        return sorted(self._tools.keys())

    async def call_tool(
        self,
        tool_name: str,
        context: ToolContext,
        arguments: Dict[str, Any],
    ) -> ToolResult:
        tool = self.get_tool(tool_name)
        return await tool.run(context=context, arguments=arguments)
