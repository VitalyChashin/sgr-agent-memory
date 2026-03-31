# Report: Custom Metrics Plugin System for Agent Observability

**Date**: 2026-03-26
**Branch**: 189-enrich-langfuse-traces
**Status**: Research complete

## Goal

Design a plugin system that lets users attach custom metrics to agent executions, with metrics saved to Langfuse. Modeled after the existing MCP Payload Processor pattern.

---

## Part 1: Langfuse Scoring/Metrics Capabilities

### Score Types

Langfuse supports three score types:

| Type | Value | Example |
|------|-------|---------|
| NUMERIC | `float` | `{"name": "latency_ms", "value": 150.5}` |
| BOOLEAN | `float` (0 or 1) | `{"name": "task_completed", "value": 1}` |
| CATEGORICAL | `str` | `{"name": "quality", "value": "excellent"}` |

Type is auto-inferred from the value if `data_type` is not set.

### Scoring at Every Level

Scores can be attached to **any observation**, not just traces:

| Target | Method | Auto-links |
|--------|--------|-----------|
| Trace | `trace.score(name=..., value=...)` | Sets `trace_id` automatically |
| Span | `span.score(name=..., value=...)` | Sets `trace_id` + `observation_id` automatically |
| Generation | `generation.score(name=..., value=...)` | Sets `trace_id` + `observation_id` automatically |
| By ID (async) | `langfuse.score(trace_id=..., observation_id=..., name=..., value=...)` | Explicit IDs, works after trace completes |

### Arbitrary Key-Value Metrics

No dedicated arbitrary metrics API, but two patterns work:

1. **Multiple named scores**: Unlimited scores per observation, each with `name` + `value`. Model custom metrics as separate scores (e.g., `name="context_tokens"`, `name="tool_retries"`)
2. **Metadata dict**: `observation.update(metadata={...})` accepts any JSON-serializable dict. Good for non-numeric context but not queryable in Langfuse dashboards

### Batch Efficiency

The SDK automatically batches all events (including scores) via `TaskManager` with configurable `flush_at` and `flush_interval`. Calling `.score()` many times is efficient — no per-call HTTP overhead.

### Async/Deferred Scoring

Fully supported. `langfuse.score(trace_id="...", name=..., value=...)` works at any time, even from a different process after the trace completes. Ideal for human review, LLM-as-judge, or post-processing pipelines.

---

## Part 2: Existing MCP Processor Pattern (Reference Architecture)

The MCP Payload Processor system provides a proven plugin pattern in this codebase:

### Pattern Summary

| Aspect | MCP Processors |
|--------|---------------|
| Base class | `MCPPayloadProcessor(ABC)` with `pre_call()` and `post_call()` |
| Registration | Auto-register via `__init_subclass__` → `ProcessorRegistry` |
| Discovery | Registry lookup + `importlib` fallback for custom classes |
| Configuration | YAML per-MCP-server: `payload_processors: [{class: "...", config: {...}}]` |
| Execution | Chain pattern: pre_call top-down, post_call bottom-up |
| Context | Receives `AgentContext` + `AgentConfig` — full access to request metadata |
| Error semantics | Exceptions propagate (fail-fast) |

### Key Files

| File | Purpose |
|------|---------|
| `sgr_agent_core/mcp_payload_processor.py` | ABC + Chain + Registry + Config model |
| `sgr_agent_core/processors/trace_context.py` | Concrete: injects traceId |
| `sgr_agent_core/processors/auth_context.py` | Concrete: injects userId |
| `sgr_agent_core/services/mcp_service.py` | Chain builder with two-tier resolution |

---

## Part 3: Proposed Metrics Plugin Design

### Architecture: ObservabilityMetricsProcessor

A plugin system that runs at defined hook points in the agent execution lifecycle, collecting custom metrics and attaching them as Langfuse scores/metadata.

### Base Class

```python
class ObservabilityMetricsProcessor(ABC):
    """Abstract base for metrics processors.

    Runs at defined hook points during agent execution.
    All methods are fail-silent — metrics must never crash agent execution.
    """

    def __init__(self, processor_config: dict[str, Any] | None = None):
        self.processor_config = processor_config or {}

    async def on_trace_start(self, trace_handle, context, config, **kwargs) -> None:
        """Called when an agent execution starts. Attach trace-level metadata."""
        pass

    async def on_iteration_end(self, iter_span_handle, context, config, **kwargs) -> None:
        """Called after each iteration completes. Attach iteration metrics."""
        pass

    async def on_tool_end(self, tool_span_handle, tool_name, tool_result, context, config, **kwargs) -> None:
        """Called after each tool execution. Attach tool-level metrics."""
        pass

    async def on_generation_end(self, gen_handle, llm_info, context, config, **kwargs) -> None:
        """Called after each LLM call. Attach generation-level metrics."""
        pass

    async def on_trace_end(self, trace_handle, context, config, **kwargs) -> None:
        """Called when agent execution completes. Attach final metrics/scores."""
        pass
```

### Hook Points (Where Processors Run)

| Hook | When | Available Data | Use Cases |
|------|------|---------------|-----------|
| `on_trace_start` | After `start_trace()` | Agent config, task messages, request metadata | Tag traces with environment, user segment, experiment ID |
| `on_iteration_end` | After iteration span closes | Iteration number, reasoning result, tool selected | Track reasoning quality per iteration, detect loops |
| `on_tool_end` | After tool span closes | Tool name, arguments, result | Measure tool-specific metrics (search relevance, API latency) |
| `on_generation_end` | After generation span closes | Model, tokens, usage, messages | Track token efficiency, prompt length trends |
| `on_trace_end` | Before `end_trace()` | Full execution result, iteration count, state | Total quality score, task completion rate, total cost annotation |

### Example Concrete Processors

#### 1. Token Efficiency Processor

```python
class TokenEfficiencyProcessor(ObservabilityMetricsProcessor):
    """Tracks token usage efficiency across the agent run."""

    def __init__(self, config=None):
        super().__init__(config)
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    async def on_generation_end(self, gen_handle, llm_info, context, config, **kwargs):
        usage = llm_info.get("usage") or {}
        self._total_input_tokens += usage.get("input", 0)
        self._total_output_tokens += usage.get("output", 0)

    async def on_trace_end(self, trace_handle, context, config, **kwargs):
        provider = kwargs.get("provider")
        if provider and self._total_input_tokens > 0:
            ratio = self._total_output_tokens / self._total_input_tokens
            provider.score_trace(trace_handle, name="token_efficiency_ratio", value=ratio)
            provider.score_trace(trace_handle, name="total_tokens", value=float(self._total_input_tokens + self._total_output_tokens))
```

#### 2. Tool Usage Tracker

```python
class ToolUsageProcessor(ObservabilityMetricsProcessor):
    """Tracks which tools were used and how many times."""

    def __init__(self, config=None):
        super().__init__(config)
        self._tool_counts = {}

    async def on_tool_end(self, tool_span_handle, tool_name, tool_result, context, config, **kwargs):
        self._tool_counts[tool_name] = self._tool_counts.get(tool_name, 0) + 1

    async def on_trace_end(self, trace_handle, context, config, **kwargs):
        provider = kwargs.get("provider")
        if provider:
            provider.score_trace(trace_handle, name="unique_tools_used", value=float(len(self._tool_counts)))
            provider.score_trace(trace_handle, name="total_tool_calls", value=float(sum(self._tool_counts.values())))
```

#### 3. Iteration Loop Detector

```python
class LoopDetectorProcessor(ObservabilityMetricsProcessor):
    """Detects if agent is stuck in a loop (same tool called repeatedly)."""

    def __init__(self, config=None):
        super().__init__(config)
        self._recent_tools = []

    async def on_tool_end(self, tool_span_handle, tool_name, tool_result, context, config, **kwargs):
        self._recent_tools.append(tool_name)
        # Check for loop: same tool 3+ times in a row
        if len(self._recent_tools) >= 3 and len(set(self._recent_tools[-3:])) == 1:
            provider = kwargs.get("provider")
            if provider:
                provider.score_trace(kwargs.get("trace_handle"), name="loop_detected", value=1.0,
                                     comment=f"Tool '{tool_name}' called 3+ times consecutively")
```

### Configuration (YAML)

```yaml
observability:
  enabled: true
  provider: "langfuse"
  langfuse:
    public_key: "pk-lf-..."
    secret_key: "sk-lf-..."
    base_url: "http://localhost:3000"

  # Metrics processors: custom metrics attached to Langfuse traces
  metrics_processors:
    - class: "TokenEfficiencyProcessor"
      config: {}
    - class: "LoopDetectorProcessor"
      config:
        threshold: 3
    - class: "myproject.metrics.CustomBusinessMetrics"  # import string for custom processors
      config:
        kpi_target: 0.95
```

### Registration & Discovery

Follow the same two-tier pattern as MCP processors:

1. **Auto-registration** via `__init_subclass__` → `MetricsProcessorRegistry`
2. **Import fallback** via `importlib.import_module()` for user-defined processors
3. Built-in processors imported in `sgr_agent_core/observability/metrics/__init__.py`

### Chain Execution

```python
class MetricsProcessorChain:
    """Ordered chain of metrics processors."""

    def __init__(self, processors: list[ObservabilityMetricsProcessor]):
        self.processors = processors

    async def run_hook(self, hook_name: str, **kwargs) -> None:
        """Run a named hook on all processors. Fail-silent per processor."""
        for processor in self.processors:
            try:
                hook = getattr(processor, hook_name, None)
                if hook:
                    await hook(**kwargs)
            except Exception as e:
                logger.warning("Metrics processor %s.%s failed: %s",
                             type(processor).__name__, hook_name, e)
```

Key difference from MCP processors: **fail-silent per processor** (metrics failures never block agent execution), vs MCP processors which fail-fast (payload errors block the MCP call).

### Integration Points in BaseAgent

```python
# In _execute():
trace = provider.start_trace(...)
await metrics_chain.run_hook("on_trace_start", trace_handle=trace, context=self._context, config=self.config)

# In _execution_step() after iteration:
await metrics_chain.run_hook("on_iteration_end", iter_span_handle=iter_span, context=self._context, config=self.config)

# In _execution_step() after tool:
await metrics_chain.run_hook("on_tool_end", tool_span_handle=tool_span, tool_name=tool_name, tool_result=tool_result, ...)

# In _execution_step() after generation:
await metrics_chain.run_hook("on_generation_end", gen_handle=gen, llm_info=llm_info, ...)

# In _execute() before end_trace:
await metrics_chain.run_hook("on_trace_end", trace_handle=trace, context=self._context, config=self.config, provider=provider)
```

---

## Part 4: Comparison with MCP Processor Pattern

| Aspect | MCP Processors | Metrics Processors (proposed) |
|--------|---------------|------------------------------|
| Purpose | Transform MCP payloads | Collect and emit custom metrics |
| Hook points | 2 (pre_call, post_call) | 5 (trace_start, iteration_end, tool_end, generation_end, trace_end) |
| Error semantics | Fail-fast (blocks MCP call) | Fail-silent (never blocks execution) |
| Data flow | Transform payload/result in-place | Side-effect only (attach scores/metadata) |
| Scope | Per MCP server | Global (all agent executions) |
| Config location | `mcp.mcpServers[name].payload_processors` | `observability.metrics_processors` |
| State | Stateless (each call independent) | Stateful per execution (accumulate across iterations) |
| Registration | `ProcessorRegistry` via `__init_subclass__` | `MetricsProcessorRegistry` via `__init_subclass__` |

---

## Part 5: Implementation Complexity

| Component | Effort | Files |
|-----------|--------|-------|
| Base class + chain + registry | Low | `sgr_agent_core/observability/metrics/processor.py` (new) |
| Config model + chain builder | Low | `sgr_agent_core/observability/metrics/__init__.py` (new) |
| Integration in BaseAgent | Medium | `sgr_agent_core/base_agent.py` (modify `_execute` + `_execution_step`) |
| Config integration | Low | `sgr_agent_core/observability/config.py` (add `metrics_processors` field) |
| Built-in processors (2-3) | Low per processor | `sgr_agent_core/observability/metrics/` (new files) |
| Tests | Medium | `tests/test_metrics_processors.py` (new) |

**Total estimate**: ~15 tasks, similar scope to feature 189.

---

## Recommendation

The metrics processor plugin system is a natural extension of both the existing MCP processor pattern (proven architecture) and the observability foundation (feature 188). It fills the gap between "structured traces" (what happened) and "quality metrics" (how well it happened), enabling the self-improving loop described in the project's architectural principles (P6).

**Suggested approach**: Implement as a new feature (`190-metrics-processors`) following the spec-driven workflow. Start with 2 built-in processors (TokenEfficiency, ToolUsage) as MVP, with the plugin interface ready for user-defined processors.
