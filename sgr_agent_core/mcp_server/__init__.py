"""MCP Server package for SGR Agent Core."""

from sgr_agent_core.mcp_server.config import MCPServerConfig
from sgr_agent_core.mcp_server.models import AskRequest, AskResponse


def create_mcp_server(config):
    """Create and configure the MCP server with the ask tool.

    Lazy import to avoid circular dependency with agent_config -> agent_factory.

    Args:
        config: GlobalConfig instance with mcp_server and agents configuration.

    Returns:
        Configured FastMCP server instance.
    """
    from sgr_agent_core.mcp_server.server import create_mcp_server as _create

    return _create(config)


__all__ = [
    "AskRequest",
    "AskResponse",
    "MCPServerConfig",
    "create_mcp_server",
]
