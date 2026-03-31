# Data Model: Enrich Langfuse Observability Traces

**Feature**: 189-enrich-langfuse-traces
**Date**: 2026-03-26

## New Entity: GenerationHandle

Opaque handle for LLM generation observations, analogous to TraceHandle/SpanHandle.

| Field | Type | Description |
|-------|------|-------------|
| (implementation-specific) | -- | NoOp: singleton. Langfuse: wraps StatefulGenerationClient |

## New Entity: LLMCallInfo

Lightweight data class populated by agent subclass phase methods to communicate LLM call details back to the base class.

| Field | Type | Description |
|-------|------|-------------|
| model | str | Model name (e.g., "gpt-4o-mini") |
| model_parameters | dict | Temperature, max_tokens, etc. |
| input_messages | list[dict] | Messages sent to the LLM (truncated) |
| output_content | str or dict | LLM response content or tool calls (truncated) |
| usage | dict or None | Token counts: `{"input": N, "output": M}` |

## Extended Entity: ObservabilityProvider (from feature 188)

New methods added to the abstract interface:

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| start_generation | name, model, model_parameters, input, metadata, _parent | GenerationHandle | Start an LLM generation observation |
| end_generation | handle, output, usage, level, status | None | End a generation and record output/usage |

## Enriched Data at Each Trace Level

### Root Trace (start_trace input)
| Field | Before | After |
|-------|--------|-------|
| task | (not captured) | User's task message text (truncated) |
| agent_type | class name | class name (unchanged) |
| messages_count | (not captured) | Number of task messages |

### Root Trace (end_trace output)
| Field | Before | After |
|-------|--------|-------|
| result_preview | execution_result[:1000] | execution_result[:2000] (unchanged logic, just works now) |

### Iteration Span (end_span output)
| Field | Before | After |
|-------|--------|-------|
| state_after | state value | state value (unchanged) |
| tool_selected | (not captured) | Name of the tool selected in this iteration |
| reasoning | (not captured) | Reasoning summary (current_situation, plan_status, remaining_steps, enough_data) — only for agents with explicit reasoning |

### Tool Span (start_span input)
| Field | Before | After |
|-------|--------|-------|
| tool | tool name only | tool name + full arguments from model_dump() |

### Tool Span (end_span output)
| Field | Before | After |
|-------|--------|-------|
| result_preview | null (for non-terminal tools) | Actual tool return value (truncated) |

### Generation Span (NEW)
| Field | Description |
|-------|-------------|
| name | "reasoning" or "action-selection" |
| model | LLM model name |
| model_parameters | temperature, max_tokens, etc. |
| input | Messages sent to the LLM |
| output | LLM response (tool calls or content) |
| usage | {input: N, output: M} token counts |
