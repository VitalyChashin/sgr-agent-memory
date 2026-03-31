# Contract: MetricsProcessor Plugin Interface

**Feature**: 190-metrics-processors
**Date**: 2026-03-26

## Overview

The `MetricsProcessor` is the public plugin interface for custom metrics collection. Developers create subclasses, implement the hooks they need, and register via YAML config.

## Base Class Contract

### Constructor

```
MetricsProcessor(processor_config: dict | None = None)
```

- `processor_config` is the `config` dict from the YAML definition
- Auto-registers concrete subclasses in `MetricsProcessorRegistry` via `__init_subclass__`

### Hook Methods (all optional, all async)

#### `on_trace_start(**kwargs) -> None`

Called after the root trace is created, before the execution loop starts.

| Kwarg | Type | Description |
|-------|------|-------------|
| trace_handle | TraceHandle | Root trace handle |
| context | AgentContext | Agent context with request metadata |
| config | AgentConfig | Agent configuration |
| provider | ObservabilityProvider | For attaching scores/metadata |

#### `on_iteration_end(**kwargs) -> None`

Called after each iteration completes (after tool execution).

| Kwarg | Type | Description |
|-------|------|-------------|
| iter_span_handle | SpanHandle | Iteration span handle |
| context | AgentContext | Agent context (iteration count, state) |
| config | AgentConfig | Agent configuration |
| provider | ObservabilityProvider | For attaching scores |
| trace_handle | TraceHandle | Root trace handle |

#### `on_tool_end(**kwargs) -> None`

Called after each tool execution.

| Kwarg | Type | Description |
|-------|------|-------------|
| tool_span_handle | SpanHandle | Tool span handle |
| tool_name | str | Name of the executed tool |
| tool_result | str | Tool execution result |
| context | AgentContext | Agent context |
| config | AgentConfig | Agent configuration |
| provider | ObservabilityProvider | For attaching scores |
| trace_handle | TraceHandle | Root trace handle |

#### `on_generation_end(**kwargs) -> None`

Called after each LLM generation span is created.

| Kwarg | Type | Description |
|-------|------|-------------|
| gen_handle | GenerationHandle | Generation handle |
| llm_info | dict | LLM call info (model, usage, etc.) |
| context | AgentContext | Agent context |
| config | AgentConfig | Agent configuration |
| provider | ObservabilityProvider | For attaching scores |
| trace_handle | TraceHandle | Root trace handle |

#### `on_trace_end(**kwargs) -> None`

Called before the root trace is closed. Final opportunity to emit scores.

| Kwarg | Type | Description |
|-------|------|-------------|
| trace_handle | TraceHandle | Root trace handle |
| context | AgentContext | Agent context (final state) |
| config | AgentConfig | Agent configuration |
| provider | ObservabilityProvider | For attaching scores |

## Chain Contract

### `MetricsProcessorChain.run_hook(hook_name, **kwargs) -> None`

- Iterates processors in order
- Calls `getattr(processor, hook_name)` if it exists
- Wraps each call in try/except — failures logged as warnings
- Never raises — always returns None

## YAML Configuration

```yaml
observability:
  metrics_processors:
    - class: "ClassName"        # Registry name or import path
      config:                    # Passed to constructor
        key: value
```

## Discovery Rules

1. Built-in processors auto-register via `__init_subclass__`
2. External processors resolved via `importlib.import_module()`
3. Unknown class name → warning logged, processor skipped (server continues)
