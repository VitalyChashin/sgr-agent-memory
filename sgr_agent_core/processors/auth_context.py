"""Auth context processor — injects userId into MCP call payloads."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sgr_agent_core.mcp_payload_processor import MCPPayloadProcessor

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig
    from sgr_agent_core.models import AgentContext

logger = logging.getLogger(__name__)


class AuthContextProcessor(MCPPayloadProcessor):
    """Injects userId into outgoing MCP payloads.

    Priority: request_metadata > processor config default.
    If neither is available, skips injection and logs a warning.
    Always overwrites the LLM-provided value when a source is available.
    """

    async def pre_call(
        self,
        payload: dict[str, Any],
        context: AgentContext,
        config: AgentConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        user_id = context.request_metadata.get("userId") or self.processor_config.get("default_user_id")
        if user_id:
            payload["userId"] = user_id
        else:
            logger.warning("AuthContextProcessor: no userId available from metadata or config; skipping injection")
        return payload
