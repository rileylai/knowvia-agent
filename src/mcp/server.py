"""Native MCP protocol server backed by the bounded Knowvia tool registry."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from src.agent.tools import AgentToolNotAllowedError, AgentToolRegistry, build_agent_tool_registry
from src.rag import ProductionChunkRetriever
from src.repositories import ChunkRepository
from src.services import MemoryService
from src.tools import ToolContext, ToolResult


DEFAULT_MCP_OWNER_ID = "local"
DEFAULT_MCP_SERVER_NAME = "knowvia-agent"
DEFAULT_MCP_SERVER_VERSION = "5.0.2"
MAX_MCP_RESULT_CHARS = 4000
UNTRUSTED_CONTEXT_ARGUMENTS = frozenset(
    {
        "owner_id",
        "user_id",
        "tenant_id",
        "explicit_save",
        "explicit_save_allowed",
        "explicit_save_content",
        "explicit_save_memory_type",
    }
)


class NativeMCPServer:
    """Expose only the bounded Agent registry through the standard MCP server API.

    The owner and any save authorization are server-side context. They are never
    read from MCP tool arguments, so a client cannot change scope or grant itself
    persistence permission.
    """

    def __init__(
        self,
        *,
        registry: AgentToolRegistry,
        owner_id: str = DEFAULT_MCP_OWNER_ID,
        trusted_metadata: Optional[Dict[str, Any]] = None,
        server_name: str = DEFAULT_MCP_SERVER_NAME,
        server_version: str = DEFAULT_MCP_SERVER_VERSION,
        tool_timeout_seconds: float = 8.0,
        cleanup: Optional[Callable[[], None]] = None,
    ) -> None:
        normalized_owner_id = owner_id.strip()
        if not normalized_owner_id:
            raise ValueError("owner_id must not be empty")
        if tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be positive")

        self.registry = registry
        self.owner_id = normalized_owner_id
        self.trusted_metadata = dict(trusted_metadata or {})
        self.tool_timeout_seconds = tool_timeout_seconds
        self._cleanup = cleanup
        self.protocol_server = Server(
            server_name,
            version=server_version,
            instructions=(
                "Knowvia bounded knowledge and saved-memory tools. "
                "Owner scope and save authorization are backend-controlled."
            ),
        )
        self.protocol_server.list_tools()(self._list_tools)
        # Existing Pydantic tool contracts remain the validation authority. The
        # protocol SDK's JSON Schema validation would otherwise return a generic
        # result before the bounded ToolResult error mapping can run.
        self.protocol_server.call_tool(validate_input=False)(self._call_tool)

    async def _list_tools(self) -> List[types.Tool]:
        tools: List[types.Tool] = []
        for tool_spec in self.registry.tool_specs():
            function = tool_spec["function"]
            tools.append(
                types.Tool(
                    name=function["name"],
                    description=function["description"],
                    inputSchema={
                        **function["parameters"],
                        "additionalProperties": False,
                    },
                )
            )
        return tools

    async def _call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]],
    ) -> types.CallToolResult:
        call_arguments = arguments or {}
        try:
            tool = self.registry.get_tool(name)
        except AgentToolNotAllowedError:
            return self._to_mcp_result(
                ToolResult.failure(
                    "invalid_tool",
                    "The requested tool is not allowed.",
                )
            )

        allowed_arguments = set(tool.spec.input_schema.get("properties", {}))
        unknown_arguments = set(call_arguments).difference(allowed_arguments)
        invalid_context_arguments = sorted(
            set(call_arguments).intersection(UNTRUSTED_CONTEXT_ARGUMENTS)
        )
        if unknown_arguments or invalid_context_arguments:
            return self._to_mcp_result(
                ToolResult.failure(
                    "invalid_arguments",
                    "Tool arguments cannot provide owner or authorization context.",
                )
            )

        context_metadata = dict(self.trusted_metadata)
        context_metadata["owner_id"] = self.owner_id
        context = ToolContext(
            workflow_id=f"mcp-{uuid4().hex}",
            actor="native_mcp",
            owner_id=self.owner_id,
            metadata=context_metadata,
        )
        try:
            result = await asyncio.wait_for(
                self.registry.call_tool(
                    name,
                    context=context,
                    arguments=call_arguments,
                ),
                timeout=self.tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = ToolResult.failure(
                "tool_timeout",
                "Tool execution timed out.",
            )
        except Exception:
            result = ToolResult.failure(
                "tool_error",
                "Tool execution failed.",
            )
        return self._to_mcp_result(result)

    @staticmethod
    def _to_mcp_result(result: ToolResult) -> types.CallToolResult:
        if result.is_error:
            error_code = result.error_code or "tool_error"
            message = result.error.message if result.error else "Tool execution failed."
            structured_content: Dict[str, Any] = {
                "error_code": error_code,
                "message": _bounded_text(message),
            }
            text = _bounded_text(result.safe_text or message)
        else:
            structured_content = result.structured_content or {}
            text = _bounded_text(result.safe_text or result.content or "")

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structuredContent=structured_content,
            isError=result.is_error,
        )

    async def run_stdio(self) -> None:
        """Run this server over the one supported local stdio transport."""

        try:
            async with stdio_server() as (read_stream, write_stream):
                await self.protocol_server.run(
                    read_stream,
                    write_stream,
                    self.protocol_server.create_initialization_options(),
                )
        finally:
            if self._cleanup is not None:
                self._cleanup()


def build_production_native_mcp_server(
    *,
    owner_id: str = DEFAULT_MCP_OWNER_ID,
) -> NativeMCPServer:
    """Build the local stdio server from existing Knowvia service boundaries."""

    from src.app.config import get_settings
    from src.app.dependencies import get_embedding_client
    from src.db.session import get_db_session_factory
    from src.db.unit_of_work import SqlAlchemyUnitOfWork

    session_factory = get_db_session_factory()
    db_session = session_factory()
    embedding_client = get_embedding_client()
    memory_service = MemoryService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        embedding_client=embedding_client,
    )
    registry = build_agent_tool_registry(
        retriever=ProductionChunkRetriever(
            chunk_repository=ChunkRepository(db_session),
        ),
        embedding_client=embedding_client,
        memory_service=memory_service,
    )
    settings = get_settings()
    return NativeMCPServer(
        registry=registry,
        owner_id=owner_id,
        tool_timeout_seconds=settings.agent_tool_timeout_seconds,
        cleanup=db_session.close,
    )


def _bounded_text(value: str, limit: int = MAX_MCP_RESULT_CHARS) -> str:
    return value[:limit]


def main() -> None:
    server = build_production_native_mcp_server()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":  # pragma: no cover
    main()
