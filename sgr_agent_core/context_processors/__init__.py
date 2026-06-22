"""Agent Context Processor plugin system.

Built-in processors are imported here to trigger auto-registration in
``AgentContextProcessorRegistry``.
"""

from __future__ import annotations

import sgr_agent_core.context_processors.mandatory_tool_call  # noqa: F401, E402
import sgr_agent_core.context_processors.repeated_tool_call_guard  # noqa: F401, E402
from sgr_agent_core.context_processors.base import (
    AgentContextProcessor,
    AgentContextProcessorChain,
    AgentContextProcessorRegistry,
    ContextProcessorDefinition,
    FinishDecision,
    PrepareToolsResult,
    build_context_processor_chain,
    emit_event_span,
)
from sgr_agent_core.context_processors.mandatory_tool_call import MandatoryToolCallProcessor
from sgr_agent_core.context_processors.repeated_tool_call_guard import RepeatedToolCallGuard

__all__ = [
    "AgentContextProcessor",
    "AgentContextProcessorChain",
    "AgentContextProcessorRegistry",
    "ContextProcessorDefinition",
    "FinishDecision",
    "PrepareToolsResult",
    "build_context_processor_chain",
    "emit_event_span",
    "RepeatedToolCallGuard",
    "MandatoryToolCallProcessor",
]
