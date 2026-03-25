"""Built-in MCP payload processors. Import to auto-register in ProcessorRegistry."""

from sgr_agent_core.processors.auth_context import AuthContextProcessor
from sgr_agent_core.processors.trace_context import TraceContextProcessor

__all__ = [
    "TraceContextProcessor",
    "AuthContextProcessor",
]
