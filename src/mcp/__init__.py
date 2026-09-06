"""MCP adapters for the bounded Knowvia agent."""

from src.agent.tools import AgentToolAdapter as MCPToolAdapter
from src.agent.tools import AgentToolRegistry


def __getattr__(name: str):
    if name in {"NativeMCPServer", "build_production_native_mcp_server"}:
        from src.mcp.server import NativeMCPServer, build_production_native_mcp_server

        return {
            "NativeMCPServer": NativeMCPServer,
            "build_production_native_mcp_server": build_production_native_mcp_server,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AgentToolRegistry",
    "MCPToolAdapter",
    "NativeMCPServer",
    "build_production_native_mcp_server",
]
