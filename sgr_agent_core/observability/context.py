"""Shared observability plumbing between the agent loop, the tool layer, and processors.

Holds two neutral primitives (no imports from ``base_agent``/``base_tool``, so it
breaks the agent↔tool import cycle):

1. Task-local contextvars that let the tool layer (``base_tool``) signal the agent
   loop (``base_agent``) without threading parameters through every ``_action_phase``
   implementation. They are task-local (each agent runs in its own asyncio task), so
   concurrent agents in the server never collide.
2. ``processor_span_start`` / ``processor_span_end`` — wrap a processor invocation in a
   real (timed) span. No-ops when ``provider`` is ``None`` (which is how callers disable
   span emission), so they are always safe to call.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

# Set True by ``MCPBaseTool.__call__`` when an MCP call raises and the error is
# swallowed into the result string. Read + reset by ``BaseAgent`` right after the
# tool executes so the tool span can be marked ERROR even though no exception
# propagated.
mcp_call_errored: ContextVar[bool] = ContextVar("mcp_call_errored", default=False)

# Set by ``BaseAgent`` to the active tool span before each tool executes; read by
# ``MCPBaseTool.__call__`` so MCP payload-processor spans nest under the tool span
# without threading a parent through every ``_action_phase`` implementation.
current_tool_span: ContextVar[Any] = ContextVar("current_tool_span", default=None)


def processor_span_start(provider: Any, parent_span: Any, *, name: str, input: dict[str, Any] | None = None) -> Any:
    """Start a timed span wrapping a processor invocation. Returns the handle or None.

    No-op (returns None) when ``provider`` is None — callers pass ``provider=None`` to
    disable span emission (e.g. ``span_mode="off"``).
    """
    if provider is None:
        return None
    try:
        return provider.start_span(name=name, span_type="span", input=input, metadata=input, _parent=parent_span)
    except Exception as e:
        logger.warning("processor_span_start(%s) failed: %s: %s", name, type(e).__name__, e)
        return None


def processor_span_end(
    provider: Any, span: Any, *, output: dict[str, Any] | None = None, level: str = "DEFAULT", status: str | None = None
) -> None:
    """End a span started by :func:`processor_span_start`. Safe when provider/span is None."""
    if provider is None or span is None:
        return
    try:
        provider.end_span(span, output=output, level=level, status=status)
    except Exception as e:
        logger.warning("processor_span_end failed: %s: %s", type(e).__name__, e)
