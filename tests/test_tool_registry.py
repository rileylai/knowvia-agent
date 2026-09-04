import asyncio
from typing import Any, Dict, Optional

import pytest

from src.tools import (
    Tool,
    ToolAlreadyRegisteredError,
    ToolContext,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


class FakeTool(Tool):
    def __init__(self, name: str) -> None:
        self._spec = ToolSpec(name=name, description="Fake tool for tests")
        self.last_context: Optional[ToolContext] = None
        self.last_arguments: Optional[Dict[str, Any]] = None

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def run(self, context: ToolContext, arguments: Dict[str, Any]) -> ToolResult:
        self.last_context = context
        self.last_arguments = arguments
        text = str(arguments.get("text", ""))
        return ToolResult.success(
            content=f"echo: {text}",
            structured_content={"echo": text},
        )


def test_tool_registry_registers_and_calls_tool() -> None:
    registry = ToolRegistry()
    fake_tool = FakeTool(name="Echo")
    registry.register_tool(fake_tool)

    context = ToolContext(workflow_id="wf-001")
    result = asyncio.run(
        registry.call_tool("echo", context=context, arguments={"text": "hello tool"})
    )

    assert result.is_error is False
    assert result.content == "echo: hello tool"
    assert result.structured_content == {"echo": "hello tool"}
    assert fake_tool.last_context == context
    assert fake_tool.last_arguments == {"text": "hello tool"}


def test_tool_registry_rejects_duplicate_tool_name() -> None:
    registry = ToolRegistry()
    registry.register_tool(FakeTool(name="echo"))

    with pytest.raises(ToolAlreadyRegisteredError):
        registry.register_tool(FakeTool(name="ECHO"))


def test_tool_registry_returns_deterministic_missing_tool_error() -> None:
    registry = ToolRegistry()
    context = ToolContext(workflow_id="wf-001")

    with pytest.raises(ToolNotFoundError):
        asyncio.run(
            registry.call_tool("missing-tool", context=context, arguments={"text": "x"})
        )
