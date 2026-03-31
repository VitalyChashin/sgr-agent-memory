# Contract: Generation Span Provider Interface

**Feature**: 189-enrich-langfuse-traces
**Date**: 2026-03-26

## Overview

Extension to the `ObservabilityProvider` ABC adding generation-specific methods for LLM call tracing.

## New Methods

### `start_generation(**kwargs) -> GenerationHandle`

Create a generation observation for an LLM call.

| Name | Type | Required | Description |
|------|------|----------|-------------|
| name | str | Yes | Phase name: "reasoning" or "action-selection" |
| model | str | No | Model name (e.g., "gpt-4o-mini") |
| model_parameters | dict | No | {temperature, max_tokens, ...} |
| input | Any | No | Messages sent to the LLM (truncated) |
| metadata | dict | No | Additional metadata |
| _parent | TraceHandle or SpanHandle | No | Parent span for nesting |

**Returns**: `GenerationHandle` — opaque reference.

**Guarantees**: Must never raise. Returns valid handle even on failure.

### `end_generation(handle, **kwargs) -> None`

Close a generation and record output/usage.

| Name | Type | Required | Description |
|------|------|----------|-------------|
| handle | GenerationHandle | Yes | Handle from start_generation() |
| output | Any | No | LLM response content (truncated) |
| usage | dict | No | Token counts: {"input": N, "output": M} |
| level | str | No | Severity level (default: "DEFAULT") |
| status | str | No | Status message |

**Guarantees**: Must never raise. Safe with None handle.

## Implementation Requirements

### NoOpProvider
- `start_generation()` returns singleton `_NOOP_GENERATION` handle
- `end_generation()` is a no-op

### LangfuseProvider
- `start_generation()` calls `parent.generation(name=..., model=..., ...)` from Langfuse v2 SDK
- `end_generation()` calls `handle.generation.end(output=..., usage=...)`
- All calls wrapped in try/except, failures logged as warnings
