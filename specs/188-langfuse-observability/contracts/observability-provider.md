# Contract: ObservabilityProvider Interface

**Feature**: 188-langfuse-observability
**Date**: 2026-03-26

## Overview

The `ObservabilityProvider` is the public interface for all observability tracing in SGR Agent Core. It is consumed by `BaseAgent` and the server lifecycle. Implementations must be fail-silent — observability must never crash or slow down agent execution.

## Interface Contract

### `start_trace(**kwargs) → TraceHandle`

Start a root trace for an agent execution.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| name | str | Yes | Human-readable trace name (typically agent name) |
| agent_id | str | Yes | Unique agent instance identifier |
| input | dict | No | Trace input data (task text, agent type) |
| user_id | str | No | User identifier from request metadata |
| session_id | str | No | Session identifier from request metadata |
| tags | list[str] | No | Searchable tags (agent class name, agent name) |
| metadata | dict | No | Additional metadata (model, max_iterations) |

**Returns**: `TraceHandle` — opaque reference to the trace. Must be passed to `end_trace()`.

**Guarantees**:
- Must never raise an exception
- Must return a valid handle even on failure (NoOp or error-fallback handle)
- Only one trace may be active per async context at a time

---

### `start_span(**kwargs) → SpanHandle`

Start a child span within the current trace context.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| name | str | Yes | Span name (e.g., `"iteration-1"`, `"tool-web_search"`) |
| span_type | str | No | Type hint: `"span"`, `"tool"`, `"agent"`. Default: `"span"` |
| input | dict | No | Span input data |
| metadata | dict | No | Additional metadata |

**Returns**: `SpanHandle` — opaque reference to the span.

**Guarantees**:
- Must never raise an exception
- Spans nest under the active trace/span via context propagation
- Multiple spans can be open simultaneously (e.g., iteration span + tool span)

---

### `end_span(handle, **kwargs) → None`

Close a span and record its output.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| handle | SpanHandle | Yes | Handle from `start_span()` |
| output | dict | No | Span output data |
| status | str | No | Status message |
| level | str | No | Severity: `"DEBUG"`, `"DEFAULT"`, `"WARNING"`, `"ERROR"`. Default: `"DEFAULT"` |

**Guarantees**:
- Must never raise an exception
- Safe to call with an invalid or no-op handle
- Idempotent — calling twice on the same handle is safe

---

### `end_trace(handle, **kwargs) → None`

Close the root trace.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| handle | TraceHandle | Yes | Handle from `start_trace()` |
| output | dict | No | Trace output data (result preview) |
| status | str | No | Final status (`"completed"`, `"failed: ..."`, `"max_iterations"`) |

**Guarantees**:
- Must never raise an exception
- Must be called exactly once per `start_trace()`, in both success and error paths

---

### `score_trace(handle, **kwargs) → None`

Attach a quality score to a trace.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| handle | TraceHandle | Yes | Handle from `start_trace()` |
| name | str | Yes | Score name (e.g., `"accuracy"`, `"relevance"`) |
| value | float, str, or bool | Yes | Score value |
| comment | str | No | Optional annotation |

---

### `flush() → None`

Flush pending events to the backend. Called at the end of each agent execution.

---

### `shutdown() → None`

Graceful shutdown. Flushes remaining events and releases resources. Called during server shutdown.

---

### `create_openai_client(**kwargs) → AsyncOpenAI`

Create an OpenAI client, optionally instrumented for observability.

**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| api_key | str | Yes | OpenAI API key |
| base_url | str | Yes | OpenAI base URL |
| http_client | Any | No | Optional httpx client (for proxy support) |

**Returns**: `AsyncOpenAI` instance. When using `LangfuseProvider`, returns `langfuse.openai.AsyncOpenAI` which auto-instruments all `chat.completions.create()` calls.

## Implementation Requirements

### NoOpProvider
- All methods are zero-cost no-ops
- `start_trace()` and `start_span()` return shared singleton handles
- `create_openai_client()` returns standard `openai.AsyncOpenAI`

### LangfuseProvider
- All methods are wrapped in try/except — failures logged as warnings
- Uses Langfuse SDK v4 context-manager API for span nesting
- `create_openai_client()` returns `langfuse.openai.AsyncOpenAI`
- Background event export via SDK's built-in batch processor
