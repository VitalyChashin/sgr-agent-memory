"""MCP Payload Processor — middleware for transforming MCP tool call payloads."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from sgr_agent_core.services.registry import Registry

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig
    from sgr_agent_core.models import AgentContext

logger = logging.getLogger(__name__)


class ProcessorRegistry(Registry["MCPPayloadProcessor"]):
    """Registry for MCP payload processor classes."""


class MCPPayloadProcessor(ABC):
    """Abstract base for MCP payload processors.

    A processor can modify the outgoing payload before an MCP call
    and/or transform the response after. Processors form an ordered
    chain: pre_call runs top-down, post_call runs bottom-up.
    """

    def __init__(self, processor_config: dict[str, Any] | None = None):
        self.processor_config = processor_config or {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Only register concrete classes (not intermediate ABCs)
        if not getattr(cls, "__abstractmethods__", set()):
            ProcessorRegistry.register(cls, name=cls.__name__)

    @abstractmethod
    async def pre_call(
        self,
        payload: dict[str, Any],
        context: AgentContext,
        config: AgentConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Transform the payload before the MCP tool call."""
        ...

    async def post_call(
        self,
        result: str,
        payload: dict[str, Any],
        context: AgentContext,
        config: AgentConfig,
        **kwargs: Any,
    ) -> str:
        """Transform the result after the MCP tool call (optional, default: pass-through)."""
        return result


class MCPPayloadProcessorChain:
    """Ordered chain of payload processors."""

    def __init__(self, processors: list[MCPPayloadProcessor]):
        self.processors = processors

    async def run_pre_call(
        self,
        payload: dict[str, Any],
        context: AgentContext,
        config: AgentConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        for processor in self.processors:
            payload = await processor.pre_call(payload, context, config, **kwargs)
        return payload

    async def run_post_call(
        self,
        result: str,
        payload: dict[str, Any],
        context: AgentContext,
        config: AgentConfig,
        **kwargs: Any,
    ) -> str:
        for processor in reversed(self.processors):
            result = await processor.post_call(result, payload, context, config, **kwargs)
        return result


class PayloadProcessorDefinition(BaseModel, extra="allow"):
    """Definition of a single payload processor in YAML config."""

    class_name: str = Field(alias="class", description="Processor class name or import string")
    config: dict[str, Any] = Field(default_factory=dict)
    managed_fields: list[str] = Field(default_factory=list)
