"""MCP server setup and ask tool registration."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from sgr_agent_core.agent_factory import AgentFactory
from sgr_agent_core.mcp_server.models import AskResponse

if TYPE_CHECKING:
    from sgr_agent_core.agent_config import GlobalConfig

logger = logging.getLogger(__name__)


def create_mcp_server(config: GlobalConfig) -> FastMCP:
    """Create and configure the MCP server with the ask tool.

    Args:
        config: GlobalConfig instance with mcp_server and agents configuration.

    Returns:
        Configured FastMCP server instance.
    """
    mcp = FastMCP("sgr-agent-mcp")

    tool_name = config.mcp_server.tool_name or "ask"
    tool_description = config.mcp_server.tool_description or (
        "Send a research query to an SGR Agent and receive a structured response."
    )

    @mcp.tool(name=tool_name, description=tool_description)
    async def ask(
        query: str,
        traceId: str = "trace-default-001",
        userId: str = "user-default-001",
    ) -> str:
        """Send a research query to an SGR Agent and receive a structured response.

        Args:
            query: The research question or task for the agent.
            traceId: Trace identifier for request correlation (default: trace-default-001).
            userId: User identifier for the request (default: user-default-001).

        Returns:
            JSON string with response text and traceId.
        """
        if not query.strip():
            raise ValueError("Query must not be empty")

        # Resolve agent definition
        agent_def_name = config.mcp_server.default_agent
        if agent_def_name:
            if agent_def_name not in config.agents:
                raise ValueError(f"Configured default_agent '{agent_def_name}' not found in agent definitions")
            agent_def = config.agents[agent_def_name]
        elif config.agents:
            agent_def = next(iter(config.agents.values()))
        else:
            raise ValueError("No agent definitions configured")

        # Build request_metadata from configurable field mapping
        field_mapping = config.observability.mcp_field_mapping
        raw_payload = {"query": query, "traceId": traceId, "userId": userId}

        request_metadata: dict = {}
        # Map configured field names to standard metadata keys
        if field_mapping.user_id and field_mapping.user_id in raw_payload:
            request_metadata["userId"] = raw_payload[field_mapping.user_id]
        if field_mapping.session_id and field_mapping.session_id in raw_payload:
            request_metadata["sessionId"] = raw_payload[field_mapping.session_id]
        if field_mapping.trace_id and field_mapping.trace_id in raw_payload:
            request_metadata["traceId"] = raw_payload[field_mapping.trace_id]
        # Include any extra configured fields
        for extra_field in field_mapping.extra_fields:
            if extra_field in raw_payload:
                request_metadata[extra_field] = raw_payload[extra_field]
        # Build tags from configured fields (format: "field:value")
        tags = []
        for tag_field in field_mapping.tags_fields:
            if tag_field in raw_payload and raw_payload[tag_field]:
                tags.append(f"{tag_field}:{raw_payload[tag_field]}")
        if tags:
            request_metadata["_tags"] = tags

        # Store the full request payload for trace enrichment
        request_metadata["_mcp_request_payload"] = raw_payload

        agent = None
        try:
            agent = await AgentFactory.create(
                agent_def=agent_def,
                task_messages=[{"role": "user", "content": query}],
                request_metadata=request_metadata,
            )
            result = await agent.execute()
        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            raise ValueError("Agent execution failed") from e
        finally:
            if agent is not None and hasattr(agent, "cleanup"):
                try:
                    await agent.cleanup()
                except Exception:
                    logger.warning("Agent cleanup failed", exc_info=True)

        response = AskResponse(
            response=str(result) if result is not None else "",
            traceId=request_metadata.get("traceId", traceId),
        )
        return json.dumps(response.model_dump())

    return mcp
