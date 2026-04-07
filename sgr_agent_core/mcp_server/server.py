"""MCP server setup and ask tool registration."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from sgr_agent_core.agent_factory import AgentFactory
from sgr_agent_core.mcp_server.models import AskResponse
from sgr_agent_core.mcp_server.session_store import get_session_store

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
    from sgr_agent_core.server.endpoints import get_memory_middleware

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
        sessionId: str = "",
    ) -> str:
        """Send a research query to an SGR Agent and receive a structured response.

        Args:
            query: The research question or task for the agent.
            traceId: Trace identifier for request correlation (default: trace-default-001).
            userId: User identifier for the request (default: user-default-001).
            sessionId: Session identifier for memory-enabled conversations (default: empty = no memory).

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

        # Include sessionId in raw payload for field mapping
        # Build request_metadata from configurable field mapping
        field_mapping = config.observability.mcp_field_mapping
        raw_payload = {"query": query, "traceId": traceId, "userId": userId, "sessionId": sessionId}

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

        # Build conversation history for the agent.
        # MCP clients send a single query (unlike REST where the client replays
        # full history), so the server accumulates turns in a session store when
        # a sessionId is provided.  This gives rolling memory (and the agent in
        # general) multi-turn context identical to the REST path.
        user_message: dict = {"role": "user", "content": query}
        session_history: list[dict] = []
        if sessionId:
            try:
                store = get_session_store()
                session_history = await store.get_history(sessionId)
            except Exception:
                logger.warning("Session store error, proceeding without history", exc_info=True)
                session_history = []

        # Memory preprocessing — only when sessionId is non-empty and middleware is active
        memory_result = None
        mw = get_memory_middleware()
        if sessionId and mw is not None:
            memory_result = await mw.preprocess(
                messages=[*session_history, user_message],
                session_id=sessionId,
                user_id=userId,
            )
        elif mw is not None and not sessionId:
            logger.debug("Memory enabled but sessionId not provided in MCP ask call — skipping memory")
        elif mw is None and sessionId:
            logger.debug("sessionId provided, using in-memory session store for conversation history")

        if memory_result:
            task_messages = memory_result.messages
        elif session_history:
            task_messages = [*session_history, user_message]
        else:
            task_messages = [user_message]

        agent = None
        try:
            agent = await AgentFactory.create(
                agent_def=agent_def,
                task_messages=task_messages,
                request_metadata=request_metadata,
            )
            result = await agent.execute()
        except Exception as e:
            logger.error("Agent execution failed: %s", e, exc_info=True)
            raise ValueError("Agent execution failed") from e
        finally:
            if agent is not None and hasattr(agent, "cleanup"):
                try:
                    await agent.cleanup()
                except Exception:
                    logger.warning("Agent cleanup failed", exc_info=True)

        # Store this turn in session history (user msg + assistant response).
        # The session store serves as the primary conversation history for MCP,
        # and as a fallback when the topic-aware memory service is unavailable.
        assistant_content = str(result) if result is not None else ""
        if sessionId and assistant_content:
            try:
                store = get_session_store()
                await store.append(sessionId, [user_message, {"role": "assistant", "content": assistant_content}])
            except Exception:
                logger.warning("Session store append failed for session=%s", sessionId, exc_info=True)

        # Memory postprocessing — store assistant response synchronously
        if memory_result and memory_result.used_memory and mw is not None:
            try:
                await mw.postprocess(
                    session_id=sessionId,
                    user_message_id=memory_result.user_message_id or "",
                    assistant_content=assistant_content,
                    user_id=userId,
                )
            except Exception:
                logger.warning("Memory postprocess failed for session=%s", sessionId, exc_info=True)

        # Build response with optional topic metadata and rolling memory fields
        response_kwargs: dict = {
            "response": assistant_content,
            "traceId": request_metadata.get("traceId", traceId),
        }
        if memory_result and memory_result.topic_metadata:
            tm = memory_result.topic_metadata
            response_kwargs["topicId"] = tm.topic_id
            response_kwargs["topicLabel"] = tm.topic_label
            response_kwargs["topicShift"] = tm.topic_shift
        if agent is not None:
            if getattr(agent._context, "conversation_summary", None):
                response_kwargs["conversationSummary"] = agent._context.conversation_summary
            if getattr(agent._context, "recent_messages", None) is not None:
                response_kwargs["recentMessages"] = agent._context.recent_messages

        response = AskResponse(**response_kwargs)
        return json.dumps(response.model_dump(exclude_none=True))

    return mcp
