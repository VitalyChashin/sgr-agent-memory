"""Built-in metrics processor: token usage efficiency."""

from typing import Any

from sgr_agent_core.observability.metrics.processor import MetricsProcessor


class TokenEfficiencyProcessor(MetricsProcessor):
    """Tracks token usage across LLM calls and emits trace-level scores.

    Scores emitted on trace end:
    - total_tokens: Total input + output tokens across all LLM calls
    - token_efficiency_ratio: Output tokens / input tokens (0 if no input tokens)
    """

    def __init__(self, processor_config: dict[str, Any] | None = None):
        super().__init__(processor_config)
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    async def on_generation_end(self, **kwargs: Any) -> None:
        llm_info = kwargs.get("llm_info") or {}
        usage = llm_info.get("usage") or {}
        self._total_input_tokens += usage.get("input", 0) or 0
        self._total_output_tokens += usage.get("output", 0) or 0

    async def on_trace_end(self, **kwargs: Any) -> None:
        provider = kwargs.get("provider")
        trace_handle = kwargs.get("trace_handle")
        if provider is None or trace_handle is None:
            return

        total = self._total_input_tokens + self._total_output_tokens
        if total > 0:
            provider.score_trace(trace_handle, name="total_tokens", value=float(total))

        if self._total_input_tokens > 0:
            ratio = self._total_output_tokens / self._total_input_tokens
            provider.score_trace(trace_handle, name="token_efficiency_ratio", value=round(ratio, 4))
