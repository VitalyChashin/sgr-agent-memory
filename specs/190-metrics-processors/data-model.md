# Data Model: Metrics Processor Plugin System

**Feature**: 190-metrics-processors
**Date**: 2026-03-26

## Entities

### MetricsProcessor (Abstract Base)

Plugin interface for custom metrics collection during agent execution.

| Attribute | Type | Description |
|-----------|------|-------------|
| processor_config | dict | Custom parameters from YAML config |

| Hook Method | Parameters | When Called |
|------------|-----------|------------|
| on_trace_start | trace_handle, context, config, provider | After start_trace() |
| on_iteration_end | iter_span_handle, context, config, provider, trace_handle | After each iteration |
| on_tool_end | tool_span_handle, tool_name, tool_result, context, config, provider, trace_handle | After each tool |
| on_generation_end | gen_handle, llm_info, context, config, provider, trace_handle | After each LLM call |
| on_trace_end | trace_handle, context, config, provider | Before end_trace() |

All hooks are optional (default: no-op). All hooks are async.

### MetricsProcessorChain

Ordered collection of processor instances. Created fresh per agent execution.

| Attribute | Type | Description |
|-----------|------|-------------|
| processors | list[MetricsProcessor] | Ordered list of processor instances |

| Method | Parameters | Description |
|--------|-----------|-------------|
| run_hook | hook_name, **kwargs | Calls named hook on each processor, fail-silent per processor |

### MetricsProcessorDefinition (Pydantic)

Configuration model for a single processor in YAML.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| class_name | str (alias: "class") | required | Processor class name or fully-qualified import path |
| config | dict | {} | Custom parameters passed to processor constructor |

### MetricsProcessorRegistry

Registry of known processor classes. Follows `Registry[MetricsProcessor]` pattern.

| Method | Description |
|--------|-------------|
| get(name) | Lookup by class name |
| register(cls, name) | Register a processor class |
| list_items() | List all registered processors |

## Built-in Processors

### TokenEfficiencyProcessor

Accumulates token usage across LLM calls and emits trace-level scores.

| Internal State | Type | Description |
|---------------|------|-------------|
| _total_input_tokens | int | Accumulated input tokens |
| _total_output_tokens | int | Accumulated output tokens |

| Score Emitted | Type | When |
|--------------|------|------|
| total_tokens | NUMERIC | on_trace_end |
| token_efficiency_ratio | NUMERIC | on_trace_end (output/input ratio) |

### ToolUsageProcessor

Tracks tool call counts and emits trace-level scores.

| Internal State | Type | Description |
|---------------|------|-------------|
| _tool_counts | dict[str, int] | Count per tool name |

| Score Emitted | Type | When |
|--------------|------|------|
| unique_tools_used | NUMERIC | on_trace_end |
| total_tool_calls | NUMERIC | on_trace_end |

## Config Extension (ObservabilityConfig)

New field added to existing `ObservabilityConfig`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| metrics_processors | list[dict] | [] | List of processor definitions |

## Relationships

```text
ObservabilityConfig
└── metrics_processors: list[MetricsProcessorDefinition]

MetricsProcessorRegistry
├── TokenEfficiencyProcessor (built-in, auto-registered)
└── ToolUsageProcessor (built-in, auto-registered)

BaseAgent._execute()
├── builds MetricsProcessorChain from config (fresh per execution)
├── chain.run_hook("on_trace_start", ...)
├── per iteration:
│   ├── chain.run_hook("on_generation_end", ...)  (after each LLM call)
│   ├── chain.run_hook("on_tool_end", ...)
│   └── chain.run_hook("on_iteration_end", ...)
└── chain.run_hook("on_trace_end", ...)
```
