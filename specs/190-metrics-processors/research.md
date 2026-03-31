# Research: Metrics Processor Plugin System

**Feature**: 190-metrics-processors
**Date**: 2026-03-26

## R1: Plugin Architecture — Follow MCP Processor Pattern

**Decision**: Model the metrics processor system directly after the existing `MCPPayloadProcessor` pattern: ABC with `__init_subclass__` auto-registration, `MetricsProcessorRegistry`, chain execution, and YAML config.

**Rationale**: The MCP Payload Processor is a proven, well-tested plugin architecture in this codebase. Reusing the same pattern reduces cognitive overhead for developers, ensures consistency, and leverages the existing `Registry` base class.

**Key differences from MCP Processors**:
- **Fail-silent** (not fail-fast): metrics failures never block execution
- **Stateful per execution**: processors accumulate data across iterations (new instance per agent run)
- **5 hooks** (not 2): trace_start, iteration_end, tool_end, generation_end, trace_end
- **Side-effect only**: processors attach scores, they don't transform data

**Alternatives considered**:
- Event emitter pattern: Rejected — more complex, no clear benefit over direct hook calls
- Middleware pattern: Rejected — implies data transformation, which is MCP processor territory

---

## R2: Chain Lifecycle — New Instance Per Execution

**Decision**: Create a new `MetricsProcessorChain` (with fresh processor instances) for each agent execution, not shared globally.

**Rationale**: Processors are stateful (they accumulate data like token counts across iterations). A shared chain would mix data from concurrent agent executions. Creating new instances per execution ensures isolation.

**Implementation**: In `BaseAgent._execute()`, build the chain from config at the start of each execution. The chain builder reads `GlobalConfig().observability.metrics_processors` and instantiates fresh processor objects.

**Alternatives considered**:
- Global singleton chain with thread-local state: Rejected — over-engineered, error-prone in async context
- Passing accumulated state as parameters: Rejected — complicates hook signatures

---

## R3: Hook Invocation — Async with Per-Processor Try/Except

**Decision**: Each hook call runs all processors sequentially, with individual try/except per processor. Failures are logged as warnings and do not stop other processors.

**Rationale**: The chain's `run_hook()` method iterates processors, calls the named hook via `getattr`, and wraps each call in try/except. This ensures one failing processor doesn't block others.

**Implementation**:
```python
async def run_hook(self, hook_name: str, **kwargs) -> None:
    for processor in self.processors:
        try:
            hook = getattr(processor, hook_name, None)
            if hook:
                await hook(**kwargs)
        except Exception as e:
            logger.warning("Metrics processor %s.%s failed: %s", ...)
```

---

## R4: Score Naming Convention

**Decision**: Built-in processors use descriptive snake_case names without a prefix: `total_tokens`, `token_efficiency_ratio`, `unique_tools_used`, `total_tool_calls`.

**Rationale**: Langfuse score names are free-form strings. A prefix like `sgr.` would add noise in the dashboard without clear benefit since all scores from SGR are already scoped to the trace. Custom processors should document their score names.

**Alternatives considered**:
- `sgr.` prefix: Rejected — adds visual noise, no collision risk within same trace
- Configurable prefix: Over-engineered for v1

---

## R5: Config Location

**Decision**: Place metrics processor config under `observability.metrics_processors` in the existing YAML config.

**Rationale**: Metrics processors are an observability concern, and the `observability` section already exists from feature 188. This keeps related config together.

**Schema**:
```yaml
observability:
  enabled: true
  provider: "langfuse"
  metrics_processors:
    - class: "TokenEfficiencyProcessor"
      config: {}
    - class: "ToolUsageProcessor"
      config: {}
    - class: "myproject.CustomProcessor"
      config:
        custom_param: "value"
```
