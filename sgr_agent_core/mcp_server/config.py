"""Configuration model for the MCP server."""

from pydantic import BaseModel


class MCPServerConfig(BaseModel):
    """Configuration for the MCP server."""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8011
    transport: str = "sse"
    default_agent: str | None = None
