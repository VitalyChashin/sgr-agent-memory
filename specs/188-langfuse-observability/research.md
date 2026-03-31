# Research: Langfuse Observability Integration

**Feature**: 188-langfuse-observability
**Date**: 2026-03-26

## R1: Langfuse SDK v4 OpenAI Wrapper — Streaming Compatibility

**Decision**: Use `langfuse.openai.AsyncOpenAI` as a drop-in replacement for `openai.AsyncOpenAI`.

**Rationale**: The Langfuse SDK v4 provides a transparent wrapper that intercepts all `chat.completions.create()` calls and automatically captures model, messages, response, token usage, latency, and time-to-first-token. The wrapper returns a `LangfuseResponseGeneratorAsync` that wraps the standard OpenAI async stream — existing stream-consumption code works unmodified. The wrapper accumulates chunks during iteration and logs the complete generation only after the stream is fully consumed.

**Alternatives considered**:
- Manual instrumentation of each LLM call site: Rejected — requires modifying every agent subclass and is fragile as new agent types are added.
- OpenTelemetry direct instrumentation: Rejected — requires more boilerplate and doesn't provide Langfuse-specific features (cost tracking, scoring, prompt management).

**Key finding**: SGR's LLM calls must include `stream_options={"include_usage": True}` to get accurate token counts from streaming responses. Without this, Langfuse falls back to tokenizer-based estimation. This parameter should be added to `LLMConfig.to_openai_client_kwargs()`.

---

## R2: Context Propagation in Async Agent Execution

**Decision**: Rely on Python's `contextvars` (used by Langfuse SDK v4 via OpenTelemetry) for automatic context propagation across `await` boundaries.

**Rationale**: SGR Agent Core is pure asyncio — the agent loop, LLM calls, and tool execution all use `async`/`await`. Python's `contextvars` module automatically propagates context across `await` boundaries and `asyncio.gather()` calls. No manual context copying is needed.

**Alternatives considered**:
- Thread-local storage: Rejected — not compatible with asyncio.
- Explicit context passing as function parameters: Rejected — would require modifying every method signature in the agent chain.

**Key finding**: Agent execution is dispatched via `asyncio.create_task(agent.execute())` in the endpoint handler (endpoints.py:205). Each task gets its own copy of the context variables, so concurrent agent executions are naturally isolated. No additional work needed for concurrency safety.

---

## R3: Provider Initialization Lifecycle

**Decision**: Initialize the provider as a process-global singleton during server startup (in the FastAPI lifespan context manager), before any agents are created.

**Rationale**: The observability provider must be available before the first agent execution. The Langfuse SDK client manages its own background thread for event export, so a single instance per process is correct. The `lifespan` context manager in `server/app.py` already handles startup/shutdown lifecycle — adding `init_provider()` and `provider.shutdown()` fits naturally.

**Alternatives considered**:
- Per-request provider initialization: Rejected — wasteful (SDK client has initialization cost), and would complicate context propagation.
- Lazy initialization on first use: Partially adopted — `get_provider()` returns `NoOpProvider` if `init_provider()` was never called, providing a safe fallback.

**Key finding**: The existing lifespan in `app.py` (lines 18-86) has clear startup and shutdown phases. Provider init should happen early in startup (after config load, before MCP server start). Provider shutdown should happen in the cleanup phase alongside MCP task cancellation.

---

## R4: OpenAI Client Factory Injection Point

**Decision**: Modify `AgentFactory._create_client()` (agent_factory.py:32-45) to delegate to `provider.create_openai_client()`.

**Rationale**: This is the single point where `AsyncOpenAI` is constructed for all agent types. Changing this one factory method swaps the client for all agents simultaneously. When the provider is `LangfuseProvider`, it returns `langfuse.openai.AsyncOpenAI`; when `NoOpProvider`, it returns the standard `openai.AsyncOpenAI` — zero overhead.

**Alternatives considered**:
- Monkey-patching the `openai` module: Rejected — fragile, hard to test, confusing for developers.
- Per-agent client creation in `BaseAgent.__init__()`: Rejected — would require changing the agent constructor contract and break the factory pattern.

**Key finding**: The `_create_client()` method receives `LLMConfig` with `api_key`, `base_url`, and optional `proxy`. The provider's `create_openai_client()` must accept the same parameters and handle the `httpx.AsyncClient` proxy case.

---

## R5: Optional Dependency Strategy

**Decision**: Add `langfuse>=4.0.0` as an optional dependency under `[project.optional-dependencies] observability` in pyproject.toml.

**Rationale**: Langfuse should not be a required dependency — many deployments won't use it. The `init_provider()` factory catches `ImportError` when loading `LangfuseProvider` and falls back to `NoOpProvider` with a warning log.

**Alternatives considered**:
- Required dependency: Rejected — adds unnecessary dependency for users who don't need observability.
- Plugin-based discovery (entry points): Rejected — over-engineered for a single known provider.

**Key finding**: Langfuse SDK v4 depends on `opentelemetry-api` and `opentelemetry-sdk`, which add ~5MB to the install. Keeping it optional avoids this overhead for minimal deployments.

---

## R6: BaseAgent Instrumentation Strategy

**Decision**: Add 6 provider call sites in `BaseAgent._execute()` and `_execution_step()`:
1. `start_trace()` at `_execute()` entry
2. `start_span("iteration-N")` at the start of each loop iteration
3. `start_span("tool-{name}")` before `_action_phase()`
4. `end_span()` after `_action_phase()`
5. `end_span()` at the end of each iteration
6. `end_trace()` at `_execute()` exit (success or error)

LLM calls in `_reasoning_phase()` and `_select_action_phase()` are auto-instrumented by the OpenAI wrapper — no explicit provider calls needed.

**Rationale**: These 6 points capture the complete agent execution structure without touching any subclass code. The abstract phase methods remain unmodified — observability is a cross-cutting concern handled entirely in the base class.

**Alternatives considered**:
- Decorator-based instrumentation: Rejected — harder to control span nesting and error handling.
- Instrumentation in each agent subclass: Rejected — duplicates code and is error-prone when new agent types are added.

**Key finding**: `_execute()` has a `try/finally` block with `_save_agent_log()` in the finally (base_agent.py:298). The `end_trace()` call must happen before `_save_agent_log()` or in the same finally block to ensure traces are always closed.
