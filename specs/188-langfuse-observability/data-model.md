# Data Model: Langfuse Observability Integration

**Feature**: 188-langfuse-observability
**Date**: 2026-03-26

## Entities

### ObservabilityConfig

Top-level configuration for the observability system. Integrates into `GlobalConfig` as an optional section.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| enabled | bool | `false` | Master switch for observability tracing |
| provider | str | `"langfuse"` | Provider backend identifier (`"langfuse"` or `"noop"`) |
| langfuse | LangfuseConfig | (defaults) | Provider-specific configuration |

### LangfuseConfig

Configuration specific to the Langfuse provider.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| public_key | str or None | `None` | Langfuse public key. Falls back to `LANGFUSE_PUBLIC_KEY` env var |
| secret_key | str or None | `None` | Langfuse secret key. Falls back to `LANGFUSE_SECRET_KEY` env var |
| base_url | str | `"https://cloud.langfuse.com"` | Langfuse server URL. Falls back to `LANGFUSE_BASE_URL` env var |
| environment | str | `"development"` | Environment tag for trace segmentation |
| flush_at | int | `512` | Events per batch before auto-flush |
| flush_interval | float | `5.0` | Seconds between background flushes |
| sample_rate | float | `1.0` | Trace sampling rate (0.0–1.0) |
| debug | bool | `false` | Enable Langfuse SDK debug logging |

### ObservabilityProvider (Abstract)

The provider interface defining all tracing operations.

| Method | Parameters | Returns | Description |
|--------|-----------|---------|-------------|
| start_trace | name, agent_id, input, user_id, session_id, tags, metadata | TraceHandle | Start a root trace for an agent execution |
| start_span | name, span_type, input, metadata | SpanHandle | Start a child span within the current trace context |
| end_span | handle, output, status, level | None | End a span and record its output |
| end_trace | handle, output, status | None | End the root trace |
| score_trace | handle, name, value, comment | None | Attach a quality score to a trace |
| flush | (none) | None | Flush pending events |
| shutdown | (none) | None | Graceful shutdown — flush and release resources |
| create_openai_client | api_key, base_url, http_client | AsyncOpenAI | Create an OpenAI client (optionally instrumented) |

### TraceHandle

Opaque handle returned by `start_trace()`. Used to reference the trace in `end_trace()` and `score_trace()`.

| Field | Type | Description |
|-------|------|-------------|
| (implementation-specific) | — | NoOp: singleton. Langfuse: wraps Langfuse observation object |

### SpanHandle

Opaque handle returned by `start_span()`. Used to reference the span in `end_span()`.

| Field | Type | Description |
|-------|------|-------------|
| (implementation-specific) | — | NoOp: singleton. Langfuse: wraps Langfuse child observation |

## Relationships

```text
GlobalConfig
└── observability: ObservabilityConfig
    └── langfuse: LangfuseConfig

ObservabilityProvider (abstract)
├── NoOpProvider (default)
└── LangfuseProvider
    ├── uses LangfuseConfig for initialization
    ├── returns LangfuseTraceHandle (extends TraceHandle)
    └── returns LangfuseSpanHandle (extends SpanHandle)

BaseAgent._execute()
├── calls provider.start_trace() → TraceHandle
├── per iteration: calls provider.start_span() → SpanHandle
│   ├── _reasoning_phase() — auto-instrumented via OpenAI wrapper
│   ├── _select_action_phase() — auto-instrumented via OpenAI wrapper
│   └── _action_phase() — wrapped in provider.start_span()/end_span()
├── calls provider.end_trace()
└── calls provider.flush()
```

## State Transitions

The provider has no complex state machine. The lifecycle is:

```text
[Not initialized] → init_provider(config) → [Active: NoOp or Langfuse]
[Active] → start_trace() → [Tracing: root trace open]
[Tracing] → start_span()/end_span() → [Tracing: spans open/closed]
[Tracing] → end_trace() → [Active: trace closed]
[Active] → shutdown() → [Terminated]
```

## Validation Rules

- `ObservabilityConfig.sample_rate` must be between 0.0 and 1.0 (inclusive)
- `LangfuseConfig.flush_at` must be positive
- `LangfuseConfig.flush_interval` must be positive
- When `enabled: true` and `provider: "langfuse"`, the langfuse package must be importable (otherwise fallback to NoOp with warning)
- `TraceHandle` and `SpanHandle` must be treated as opaque — callers must not inspect implementation details
