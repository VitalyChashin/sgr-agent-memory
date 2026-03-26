"""Built-in metrics processor: tool usage patterns."""

from typing import Any

from sgr_agent_core.observability.metrics.processor import MetricsProcessor


class ToolUsageProcessor(MetricsProcessor):
    """Tracks tool call counts and emits trace-level scores.

    Scores emitted on trace end:
    - unique_tools_used: Number of distinct tools called
    - total_tool_calls: Total number of tool invocations
    """

    def __init__(self, processor_config: dict[str, Any] | None = None):
        super().__init__(processor_config)
        self._tool_counts: dict[str, int] = {}

    async def on_tool_end(self, **kwargs: Any) -> None:
        tool_name = kwargs.get("tool_name", "unknown")
        self._tool_counts[tool_name] = self._tool_counts.get(tool_name, 0) + 1

    async def on_trace_end(self, **kwargs: Any) -> None:
        provider = kwargs.get("provider")
        trace_handle = kwargs.get("trace_handle")
        if provider is None or trace_handle is None:
            return

        if self._tool_counts:
            provider.score_trace(trace_handle, name="unique_tools_used", value=float(len(self._tool_counts)))
            provider.score_trace(trace_handle, name="total_tool_calls", value=float(sum(self._tool_counts.values())))
