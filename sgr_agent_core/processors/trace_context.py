"""Trace context processor — injects traceId into MCP call payloads."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sgr_agent_core.mcp_payload_processor import MCPPayloadProcessor

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig
    from sgr_agent_core.models import AgentContext


class TraceContextProcessor(MCPPayloadProcessor):
    """Injects or propagates traceId into outgoing MCP payloads.

    Priority: request_metadata > processor config default > auto-generated.
    Always overwrites the LLM-provided value.
    """

    async def pre_call(
        self,
        payload: dict[str, Any],
        context: AgentContext,
        config: AgentConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        trace_id = (
            context.request_metadata.get("traceId")
            or self.processor_config.get("default_trace_id")
            or f"trace-{uuid.uuid4().hex[:12]}"
        )
        payload["traceId"] = trace_id
        return payload
