"""Metrics processor plugin system for agent observability.

Provides an extensible hook-based system for attaching custom metrics
to agent execution traces. Modeled after the MCP Payload Processor pattern.
"""

from __future__ import annotations

import logging
from abc import ABC
from typing import Any

from pydantic import BaseModel, Field

from sgr_agent_core.services.registry import Registry

logger = logging.getLogger(__name__)


class MetricsProcessorRegistry(Registry["MetricsProcessor"]):
    """Registry for metrics processor classes."""


class MetricsProcessor(ABC):
    """Abstract base for metrics processors.

    Runs at defined hook points during agent execution.
    All methods are fail-silent — metrics must never crash agent execution.

    Subclasses auto-register in MetricsProcessorRegistry on definition.
    Override only the hooks you need; unneeded hooks default to no-op.
    """

    def __init__(self, processor_config: dict[str, Any] | None = None):
        self.processor_config = processor_config or {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstractmethods__", set()):
            MetricsProcessorRegistry.register(cls, name=cls.__name__)

    async def on_trace_start(self, **kwargs: Any) -> None:
        """Called after root trace is created, before execution loop."""
        pass

    async def on_iteration_end(self, **kwargs: Any) -> None:
        """Called after each iteration completes."""
        pass

    async def on_tool_end(self, **kwargs: Any) -> None:
        """Called after each tool execution."""
        pass

    async def on_generation_end(self, **kwargs: Any) -> None:
        """Called after each LLM generation span is created."""
        pass

    async def on_trace_end(self, **kwargs: Any) -> None:
        """Called before root trace is closed. Final opportunity to emit scores."""
        pass


class MetricsProcessorChain:
    """Ordered chain of metrics processors. Fail-silent per processor."""

    def __init__(self, processors: list[MetricsProcessor]):
        self.processors = processors

    async def run_hook(self, hook_name: str, **kwargs: Any) -> None:
        """Run a named hook on all processors. Never raises."""
        for processor in self.processors:
            try:
                hook = getattr(processor, hook_name, None)
                if hook:
                    await hook(**kwargs)
            except Exception as e:
                logger.warning(
                    "Metrics processor %s.%s failed: %s: %s",
                    type(processor).__name__,
                    hook_name,
                    type(e).__name__,
                    e,
                )


class MetricsProcessorDefinition(BaseModel, extra="allow"):
    """Definition of a single metrics processor in YAML config."""

    class_name: str = Field(alias="class", description="Processor class name or import string")
    config: dict[str, Any] = Field(default_factory=dict)
