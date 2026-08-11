from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Self, TypeVar

from fastmcp import Client
from pydantic import BaseModel, Field

from sgr_agent_core.agent_config import GlobalConfig
from sgr_agent_core.observability.context import mcp_call_errored
from sgr_agent_core.services.registry import ToolRegistry
from sgr_agent_core.services.retry import RetryPolicy, with_mcp_retry

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig
    from sgr_agent_core.models import AgentContext

logger = logging.getLogger(__name__)


class ToolRegistryMixin:
    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__name__ not in ("BaseTool", "MCPBaseTool", "SystemBaseTool"):
            ToolRegistry.register(cls, name=cls.tool_name)


ToolConfig = TypeVar("ToolConfig", bound=BaseModel | None)


class BaseTool(BaseModel, ToolRegistryMixin):
    """Class to provide tool handling capabilities."""

    tool_name: ClassVar[str] = None
    description: ClassVar[str] = None
    isSystemTool: ClassVar[bool] = False
    # Optional: Pydantic model for tool config; agent.get_tool_config(tool_class) returns an instance of it
    config_model: ClassVar[ToolConfig] = None

    # Elicits chain-of-thought from models that have no native reasoning channel (or
    # whose reasoning is stripped in transit — see notes/tool-argument-reasoning.md).
    # Declared on the base so it is first in every tool's schema: the model writes its
    # thinking before the arguments it justifies. Subclasses may override the
    # description; MCP payloads drop it (it is not a real server argument).
    reasoning: str = Field(
        default="",
        description="Brief step-by-step thinking that justifies calling this tool with these arguments",
    )

    async def __call__(self, context: AgentContext, config: AgentConfig, **kwargs) -> str:
        """The result should be a string or dumped JSON."""
        raise NotImplementedError("Execute method must be implemented by subclass")

    def __init_subclass__(cls, **kwargs) -> None:
        if "tool_name" not in cls.__dict__:
            cls.tool_name = cls.__name__.lower()
        if "description" not in cls.__dict__:
            cls.description = cls.__doc__ or ""
        super().__init_subclass__(**kwargs)


class SystemBaseTool(BaseTool):
    """Base class for system tools that are always available and never
    filtered."""

    isSystemTool: ClassVar[bool] = True


ReasoningToolStubType = TypeVar("ReasoningToolStubType", bound=SystemBaseTool)


class MCPBaseTool(BaseTool):
    """Base model for MCP Tool schema."""

    _client: ClassVar[Client | None] = None
    _processor_chain: ClassVar[Any] = None  # MCPPayloadProcessorChain | None
    _managed_fields: ClassVar[list[str]] = []
    # True when the server's own input schema has a `reasoning` parameter, in which
    # case it is a real argument and must NOT be stripped from the payload.
    _declares_reasoning: ClassVar[bool] = False

    async def __call__(self, context: AgentContext, config: AgentConfig, **kwargs) -> str:
        from sgr_agent_core.observability import get_provider
        from sgr_agent_core.observability.context import current_tool_span

        global_config = GlobalConfig()
        # BaseTool.reasoning is a prompting device, not an MCP argument — the server
        # would reject it as unexpected input, so it never leaves the process.
        payload = self.model_dump(mode="json", exclude=None if self._declares_reasoning else {"reasoning"})

        # Provider + active tool span (set by BaseAgent) so payload-processor spans
        # nest under the MCP call they wrap. Both safe/no-op when unset.
        provider = get_provider()
        parent_span = current_tool_span.get()

        # Apply processor chain before MCP call
        if self._processor_chain:
            payload = await self._processor_chain.run_pre_call(
                payload, context, config, provider=provider, parent_span=parent_span, **kwargs
            )

        async def _invoke() -> str:
            async with self._client:
                result = await self._client.call_tool(self.tool_name, payload)
                return json.dumps([m.model_dump_json() for m in result.content], ensure_ascii=False)[
                    : global_config.execution.mcp_context_limit
                ]

        retry_cfg = global_config.execution.mcp_retry
        policy = RetryPolicy(
            attempts=retry_cfg.attempts,
            base_delay=retry_cfg.base_delay,
            max_delay=retry_cfg.max_delay,
            backoff_factor=retry_cfg.backoff_factor,
        )

        try:
            # Retry wraps only the connect+call (transient transport errors); the
            # processor chain runs once on the final successful result.
            result_str = await with_mcp_retry(_invoke, policy, what=f"call_tool({self.tool_name})")

            # Apply processor chain after MCP call
            if self._processor_chain:
                result_str = await self._processor_chain.run_post_call(
                    result_str, payload, context, config, provider=provider, parent_span=parent_span, **kwargs
                )

            return result_str
        except Exception as e:
            logger.error(f"Error processing MCP tool {self.tool_name}: {e}")
            # The error is swallowed into the result string so the loop continues, but
            # we still mark it for Langfuse: tag the trace (filterable) and flag the
            # tool span so the agent loop can set it to ERROR level.
            context.error_tags.update({"error", "error:mcp_tool"})
            mcp_call_errored.set(True)
            return f"Error: {e}"

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, **kwargs) -> Self:
        return super().model_validate_json(json_data=json_data or "{}", **kwargs)
