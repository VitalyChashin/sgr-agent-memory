# Architecture Proposal: Langfuse Observability for SGR Agent Core

> **Feature:** 003-langfuse-observability  
> **Status:** Draft  
> **Created:** 2026-03-25  
> **Depends on:** None (standalone; future integration with 002-mcp-payload-processor for trace propagation is out of scope)

---

## 1. Problem Statement

SGR Agent Core currently has no structured observability layer. The framework logs to Python's `logging` module (configured via `logging_config.yaml`), but there is no way to:

1. **Trace a complete agent execution** — see the full reasoning loop as a structured tree of phases, LLM calls, and tool invocations with timing, inputs, and outputs.
2. **Track LLM token usage and cost** — every agent execution involves multiple LLM calls (reasoning phase + action selection phase per iteration, potentially 7+ iterations), but total token consumption and cost are invisible.
3. **Correlate across requests** — when the REST API or MCP endpoint serves multiple concurrent agent executions, there is no way to correlate logs to a specific request, user, or session.
4. **Debug agent decision-making** — understanding *why* an agent chose a particular tool at step 3 requires reconstructing the conversation history from logs, which is fragile and time-consuming.
5. **Measure agent quality** — there is no mechanism to attach quality scores (human or automated) to agent runs or individual steps.

As the platform evolves toward multi-agent composition (feature 001: MCP `ask` endpoint, feature 002: payload processors), the lack of structured tracing becomes a blocking issue. Operators cannot debug agent chains, identify performance bottlenecks, or track cost across composed agent calls.

**Langfuse** is an MIT-licensed observability platform purpose-built for LLM applications. Its Python SDK v4 (built on OpenTelemetry) provides context-manager-based tracing that maps naturally to SGR's iterative reasoning loop, a drop-in OpenAI wrapper for automatic LLM call instrumentation, and graceful degradation when the backend is unreachable. It can be self-hosted with no usage limits or used via Langfuse Cloud.

---

## 2. How SGR Currently Handles LLM Calls — Analysis

### 2.1 The Agent Execution Loop

`BaseAgent.execute()` drives the core loop:

```python
# base_agent.py — simplified
class BaseAgent:
    async def execute(self):
        self._state = AgentState.RESEARCHING
        while self._state not in FINISH_STATES:
            reasoning = await self._reasoning_phase()       # LLM call #1
            action_tool = await self._select_action_phase(reasoning)  # LLM call #2 (hybrid agents)
            await self._action_phase(action_tool)           # Tool execution
        return self._build_result()
```

Each iteration produces 1–2 LLM calls (depending on agent type) plus one tool invocation. A typical research agent runs 4–7 iterations, generating 8–14+ LLM calls per request.

### 2.2 How LLM Calls Are Made

All agents use the `openai.AsyncOpenAI` client (configured via `AgentConfig.llm`). The client is created during agent initialization and used throughout the execution loop:

```python
# Agent initialization (simplified)
self._openai_client = AsyncOpenAI(
    api_key=config.llm.api_key,
    base_url=config.llm.base_url,
    http_client=httpx_client,  # with optional proxy
)
```

LLM calls happen in two places:

**Reasoning phase** — `chat.completions.create()` with either `response_format` (structured output) or `tools` (function calling), depending on agent type:

```python
# SGRAgent — structured output
response = await self._openai_client.beta.chat.completions.parse(
    model=config.llm.model,
    messages=context,
    response_format=NextStepSchema,
    temperature=config.llm.temperature,
    max_tokens=config.llm.max_tokens,
    stream=True,  # SSE streaming
)

# SGRToolCallingAgent — function calling for reasoning
response = await self._openai_client.chat.completions.create(
    model=config.llm.model,
    messages=context,
    tools=[reasoning_tool_schema],
    tool_choice="required",
    stream=True,
)
```

**Select action phase** (hybrid agents only) — a second `chat.completions.create()` with the available tools:

```python
response = await self._openai_client.chat.completions.create(
    model=config.llm.model,
    messages=context_with_reasoning,
    tools=available_tool_schemas,
    tool_choice="required",
    stream=True,
)
```

### 2.3 Streaming

All LLM calls use `stream=True`. The response is an `AsyncStream` that yields chunks. Chunks are forwarded to the client via SSE (Server-Sent Events) in the FastAPI endpoint. The stream is fully consumed before the agent proceeds to tool execution.

### 2.4 Tool Execution

Tools are invoked via `tool.__call__(context, config, **kwargs)`. For MCP tools, this involves an outgoing MCP call. For built-in tools (WebSearchTool, CreateReportTool, etc.), execution is local async I/O.

### 2.5 Intervention Points

The natural instrumentation points map directly to the agent loop:

| Point | What to trace | Langfuse type |
|-------|---------------|---------------|
| `execute()` entry/exit | Full agent run | **Trace** (root) |
| Each loop iteration | Reasoning cycle | **Span** (`iteration-N`) |
| `_reasoning_phase()` | LLM call for reasoning | **Generation** (auto via OpenAI wrapper) |
| `_select_action_phase()` | LLM call for tool selection | **Generation** (auto via OpenAI wrapper) |
| `_action_phase()` | Tool execution | **Span** (`tool-{name}`) |
| MCP tool `__call__()` | Outgoing MCP call | **Span** (`mcp-{tool_name}`) |

### 2.6 Key Constraint: OpenAI Client Ownership

The `AsyncOpenAI` client is created inside the agent (or by `AgentFactory`). To use Langfuse's drop-in OpenAI wrapper, the import path must change from `openai.AsyncOpenAI` to `langfuse.openai.AsyncOpenAI`. This is the single most impactful change — it auto-instruments all LLM calls with zero additional code, capturing model, messages, response, token usage, latency, and streaming time-to-first-token.

---

## 3. Proposed Architecture: LangfuseObservabilityProvider

### 3.1 Core Concept

Introduce an **ObservabilityProvider** abstraction with a Langfuse implementation. The provider is initialized once at server startup and injected into agents via `AgentConfig`. Agents call provider methods at the defined intervention points. When observability is disabled, a **NoOpProvider** is used — all calls become zero-cost no-ops.

The design separates two concerns:

1. **LLM call instrumentation** — handled automatically by the Langfuse OpenAI drop-in wrapper. No agent code changes required beyond swapping the import.
2. **Agent loop instrumentation** — handled by explicit `ObservabilityProvider` calls in `BaseAgent`, tracing the iteration structure, tool executions, and agent lifecycle.

```
┌─────────────────────────────────────────────────────────┐
│                    BaseAgent.execute()                   │
│                                                         │
│  provider.start_trace(agent_id, task, user_id, ...)     │
│  │                                                      │
│  │  for each iteration:                                 │
│  │    provider.start_span("iteration-N")                │
│  │    │                                                 │
│  │    │  _reasoning_phase()                             │
│  │    │    └─ AsyncOpenAI.create()  ← auto-instrumented │
│  │    │       (Langfuse wrapper captures generation)    │
│  │    │                                                 │
│  │    │  _select_action_phase()                         │
│  │    │    └─ AsyncOpenAI.create()  ← auto-instrumented │
│  │    │                                                 │
│  │    │  _action_phase()                                │
│  │    │    └─ provider.start_span("tool-{name}")        │
│  │    │       tool.__call__()                           │
│  │    │       provider.end_span(output, status)         │
│  │    │                                                 │
│  │    provider.end_span(iteration_result)               │
│  │                                                      │
│  provider.end_trace(output, status)                     │
│  provider.flush()                                       │
└─────────────────────────────────────────────────────────┘
```

### 3.2 ObservabilityProvider Abstract Base

```python
# sgr_agent_core/observability/provider.py

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING
from contextlib import asynccontextmanager

if TYPE_CHECKING:
    from sgr_agent_core.agent_definition import AgentConfig


class ObservabilityProvider(ABC):
    """
    Abstract base for observability providers.

    Provides structured tracing for agent execution loops.
    Implementations must be async-safe and fail-silent —
    observability must never crash agent execution.
    """

    @abstractmethod
    def start_trace(
        self,
        *,
        name: str,
        agent_id: str,
        input: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceHandle:
        """Start a root trace for an agent execution."""
        ...

    @abstractmethod
    def start_span(
        self,
        *,
        name: str,
        span_type: str = "span",  # "span", "tool", "agent"
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanHandle:
        """Start a child span within the current trace context."""
        ...

    @abstractmethod
    def end_span(
        self,
        handle: SpanHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
        level: str = "DEFAULT",  # "DEBUG", "DEFAULT", "WARNING", "ERROR"
    ) -> None:
        """End a span and record its output."""
        ...

    @abstractmethod
    def end_trace(
        self,
        handle: TraceHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> None:
        """End the root trace."""
        ...

    @abstractmethod
    def score_trace(
        self,
        handle: TraceHandle,
        *,
        name: str,
        value: float | str | bool,
        comment: str | None = None,
    ) -> None:
        """Attach a quality score to a trace."""
        ...

    @abstractmethod
    def flush(self) -> None:
        """Flush pending events. Call at end of request."""
        ...

    @abstractmethod
    def shutdown(self) -> None:
        """Graceful shutdown. Flush and release resources."""
        ...

    def create_openai_client(
        self,
        *,
        api_key: str,
        base_url: str,
        http_client: Any = None,
    ) -> Any:
        """
        Create an OpenAI client with auto-instrumentation.
        
        Default: returns a standard AsyncOpenAI client.
        Langfuse override: returns a langfuse.openai.AsyncOpenAI client.
        """
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
        )


class TraceHandle:
    """Opaque handle to a trace. Implementation-specific."""
    pass


class SpanHandle:
    """Opaque handle to a span. Implementation-specific."""
    pass
```

### 3.3 NoOpProvider (Default)

```python
# sgr_agent_core/observability/noop.py

from sgr_agent_core.observability.provider import (
    ObservabilityProvider, TraceHandle, SpanHandle,
)

_NOOP_TRACE = TraceHandle()
_NOOP_SPAN = SpanHandle()


class NoOpProvider(ObservabilityProvider):
    """Zero-cost no-op provider. Used when observability is disabled."""

    def start_trace(self, **kwargs) -> TraceHandle:
        return _NOOP_TRACE

    def start_span(self, **kwargs) -> SpanHandle:
        return _NOOP_SPAN

    def end_span(self, handle, **kwargs) -> None:
        pass

    def end_trace(self, handle, **kwargs) -> None:
        pass

    def score_trace(self, handle, **kwargs) -> None:
        pass

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
```

### 3.4 LangfuseProvider

```python
# sgr_agent_core/observability/langfuse_provider.py

from __future__ import annotations
import logging
from typing import Any

from sgr_agent_core.observability.provider import (
    ObservabilityProvider, TraceHandle, SpanHandle,
)

logger = logging.getLogger(__name__)


class LangfuseTraceHandle(TraceHandle):
    """Wraps a Langfuse observation context manager."""
    def __init__(self, observation):
        self.observation = observation


class LangfuseSpanHandle(SpanHandle):
    """Wraps a Langfuse child observation."""
    def __init__(self, observation):
        self.observation = observation


class LangfuseProvider(ObservabilityProvider):
    """
    Langfuse-backed observability provider.

    Uses the Langfuse Python SDK v4 context-manager API for
    structured tracing. LLM calls are auto-instrumented via
    the langfuse.openai drop-in wrapper.

    All operations are fail-silent: if Langfuse is unreachable,
    the SDK queues events in memory and never throws exceptions.
    """

    def __init__(self, config: LangfuseConfig):
        from langfuse import Langfuse

        self._config = config
        self._client = Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            base_url=config.base_url,
            flush_at=config.flush_at,
            flush_interval=config.flush_interval,
            sample_rate=config.sample_rate,
            debug=config.debug,
            tracing_enabled=config.enabled,
            environment=config.environment,
        )
        self._active_trace = None  # Current root observation context manager
        logger.info(
            "Langfuse observability initialized (base_url=%s, environment=%s)",
            config.base_url, config.environment,
        )

    def start_trace(
        self,
        *,
        name: str,
        agent_id: str,
        input: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LangfuseTraceHandle:
        try:
            from langfuse import propagate_attributes

            merged_metadata = {"agent_id": agent_id, **(metadata or {})}
            merged_tags = list(tags or [])

            obs = self._client.start_as_current_observation(
                as_type="agent",
                name=name,
                input=input,
                metadata=merged_metadata,
            )
            # Enter the context manager
            observation = obs.__enter__()

            # Propagate trace-level attributes
            self._propagate_ctx = propagate_attributes(
                user_id=user_id,
                session_id=session_id,
                tags=merged_tags,
                metadata=merged_metadata,
            )
            self._propagate_ctx.__enter__()

            self._active_trace = obs
            return LangfuseTraceHandle(observation)

        except Exception as e:
            logger.warning("Langfuse start_trace failed (non-fatal): %s", e)
            return LangfuseTraceHandle(None)

    def start_span(
        self,
        *,
        name: str,
        span_type: str = "span",
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LangfuseSpanHandle:
        try:
            obs = self._client.start_as_current_observation(
                as_type=span_type,
                name=name,
                input=input,
                metadata=metadata,
            )
            observation = obs.__enter__()
            return LangfuseSpanHandle(observation)

        except Exception as e:
            logger.warning("Langfuse start_span failed (non-fatal): %s", e)
            return LangfuseSpanHandle(None)

    def end_span(
        self,
        handle: LangfuseSpanHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
        level: str = "DEFAULT",
    ) -> None:
        try:
            if handle.observation is not None:
                handle.observation.update(
                    output=output,
                    level=level,
                    status_message=status,
                )
                # Exit the context manager
                handle.observation.__exit__(None, None, None)
        except Exception as e:
            logger.warning("Langfuse end_span failed (non-fatal): %s", e)

    def end_trace(
        self,
        handle: LangfuseTraceHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> None:
        try:
            if handle.observation is not None:
                handle.observation.update(output=output, status_message=status)

            if hasattr(self, '_propagate_ctx'):
                self._propagate_ctx.__exit__(None, None, None)

            if self._active_trace is not None:
                self._active_trace.__exit__(None, None, None)
                self._active_trace = None

        except Exception as e:
            logger.warning("Langfuse end_trace failed (non-fatal): %s", e)

    def score_trace(
        self,
        handle: LangfuseTraceHandle,
        *,
        name: str,
        value: float | str | bool,
        comment: str | None = None,
    ) -> None:
        try:
            if handle.observation is not None:
                handle.observation.score(
                    name=name,
                    value=value,
                    comment=comment,
                )
        except Exception as e:
            logger.warning("Langfuse score_trace failed (non-fatal): %s", e)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as e:
            logger.warning("Langfuse flush failed (non-fatal): %s", e)

    def shutdown(self) -> None:
        try:
            self._client.flush()
            self._client.shutdown()
        except Exception as e:
            logger.warning("Langfuse shutdown failed (non-fatal): %s", e)

    def create_openai_client(
        self,
        *,
        api_key: str,
        base_url: str,
        http_client: Any = None,
    ) -> Any:
        """
        Returns a Langfuse-instrumented AsyncOpenAI client.

        All chat.completions.create() calls made through this client
        are automatically traced as Langfuse generations with model,
        messages, response, token usage, latency, and streaming
        time-to-first-token.

        When called within an active trace context (start_trace /
        start_span), generations auto-nest as children via OTel
        context propagation.
        """
        from langfuse.openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
        )
```

### 3.5 Provider Registry and Factory

```python
# sgr_agent_core/observability/__init__.py

from sgr_agent_core.observability.provider import (
    ObservabilityProvider,
    TraceHandle,
    SpanHandle,
)
from sgr_agent_core.observability.noop import NoOpProvider

_PROVIDER_MAP = {
    "langfuse": "sgr_agent_core.observability.langfuse_provider.LangfuseProvider",
    "noop": None,  # Sentinel — uses NoOpProvider directly
}

_active_provider: ObservabilityProvider | None = None


def init_provider(config) -> ObservabilityProvider:
    """
    Initialize the global observability provider from config.
    Called once at server startup.
    """
    global _active_provider

    if not config.observability.enabled:
        _active_provider = NoOpProvider()
        return _active_provider

    provider_type = config.observability.provider
    if provider_type == "langfuse":
        import importlib
        module_path, class_name = _PROVIDER_MAP["langfuse"].rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        _active_provider = cls(config.observability.langfuse)
    else:
        _active_provider = NoOpProvider()

    return _active_provider


def get_provider() -> ObservabilityProvider:
    """Get the active observability provider. Returns NoOp if not initialized."""
    global _active_provider
    if _active_provider is None:
        _active_provider = NoOpProvider()
    return _active_provider
```

---

## 4. Instrumented BaseAgent

The key modification is in `BaseAgent.execute()` and the phase methods. The changes are minimal — 6 call sites in the execution loop:

```python
# base_agent.py — modified (showing instrumentation points)

from sgr_agent_core.observability import get_provider

class BaseAgent:

    async def execute(self):
        provider = get_provider()

        # 1. Start root trace for this agent execution
        trace = provider.start_trace(
            name=self._agent_name,
            agent_id=self._agent_id,
            input={"task": self._task_text, "agent_type": self.__class__.__name__},
            user_id=self._context.request_metadata.get("userId"),
            session_id=self._context.request_metadata.get("sessionId"),
            tags=[self.__class__.__name__, self._agent_name],
            metadata={
                "model": self._config.llm.model,
                "max_iterations": self._config.execution.max_iterations,
            },
        )

        try:
            self._state = AgentState.RESEARCHING

            while self._state not in FINISH_STATES:
                iteration = self._context.iteration

                # 2. Start iteration span
                iter_span = provider.start_span(
                    name=f"iteration-{iteration}",
                    span_type="span",
                    input={"iteration": iteration, "state": self._state.value},
                    metadata={"searches_used": self._context.searches_used},
                )

                try:
                    # 3. Reasoning phase (LLM calls auto-instrumented via OpenAI wrapper)
                    reasoning = await self._reasoning_phase()

                    # 4. Select action phase (LLM call auto-instrumented)
                    action_tool = await self._select_action_phase(reasoning)

                    # 5. Tool execution span
                    tool_name = getattr(action_tool, 'tool_name', str(action_tool))
                    tool_span = provider.start_span(
                        name=f"tool-{tool_name}",
                        span_type="tool",
                        input={"tool": tool_name, "args": _safe_dump(action_tool)},
                    )

                    try:
                        await self._action_phase(action_tool)
                        provider.end_span(
                            tool_span,
                            output={"result_preview": self._last_tool_result[:500]
                                    if self._last_tool_result else None},
                        )
                    except Exception as tool_err:
                        provider.end_span(
                            tool_span,
                            output={"error": str(tool_err)},
                            level="ERROR",
                            status=f"Tool execution failed: {tool_err}",
                        )
                        raise

                    provider.end_span(
                        iter_span,
                        output={
                            "tool_selected": tool_name,
                            "state_after": self._state.value,
                        },
                    )

                except Exception as iter_err:
                    provider.end_span(
                        iter_span,
                        output={"error": str(iter_err)},
                        level="ERROR",
                    )
                    raise

            # 6. End trace
            result = self._build_result()
            provider.end_trace(
                trace,
                output={"result_preview": str(result)[:1000]},
                status="completed" if self._state == AgentState.COMPLETED else self._state.value,
            )
            provider.flush()
            return result

        except Exception as e:
            provider.end_trace(
                trace,
                output={"error": str(e)},
                status=f"failed: {e}",
            )
            provider.flush()
            raise
```

### 4.1 OpenAI Client Creation Change

The only other code change: replace direct `AsyncOpenAI` construction with the provider's factory method.

**Before:**
```python
from openai import AsyncOpenAI

self._openai_client = AsyncOpenAI(
    api_key=config.llm.api_key,
    base_url=config.llm.base_url,
    http_client=httpx_client,
)
```

**After:**
```python
from sgr_agent_core.observability import get_provider

provider = get_provider()
self._openai_client = provider.create_openai_client(
    api_key=config.llm.api_key,
    base_url=config.llm.base_url,
    http_client=httpx_client,
)
```

When the provider is `LangfuseProvider`, this returns `langfuse.openai.AsyncOpenAI` — which auto-instruments every `chat.completions.create()` call as a Langfuse generation, capturing model, messages, response, token usage (`input_tokens`, `output_tokens`), latency, streaming time-to-first-token, tool calls, and structured output schemas. The generations automatically nest under the active span via OTel context propagation.

When the provider is `NoOpProvider`, this returns the standard `openai.AsyncOpenAI` — zero overhead, identical behavior to current code.

---

## 5. Configuration Design

### 5.1 YAML Config Schema

```yaml
# config.yaml — new section
observability:
  enabled: true
  provider: "langfuse"    # "langfuse" | "noop"

  langfuse:
    public_key: "pk-lf-..."       # or LANGFUSE_PUBLIC_KEY env var
    secret_key: "sk-lf-..."       # or LANGFUSE_SECRET_KEY env var
    base_url: "http://localhost:3000"  # or LANGFUSE_BASE_URL env var
    environment: "development"    # "development" | "staging" | "production"
    flush_at: 512                 # events per batch before auto-flush
    flush_interval: 5.0           # seconds between flushes
    sample_rate: 1.0              # 0.0–1.0 trace sampling rate
    debug: false                  # enable SDK debug logging
```

### 5.2 Pydantic Config Models

```python
# sgr_agent_core/observability/config.py

from pydantic import BaseModel, Field


class LangfuseConfig(BaseModel):
    """Langfuse-specific configuration."""
    public_key: str | None = Field(
        default=None,
        description="Langfuse public key. Falls back to LANGFUSE_PUBLIC_KEY env var.",
    )
    secret_key: str | None = Field(
        default=None,
        description="Langfuse secret key. Falls back to LANGFUSE_SECRET_KEY env var.",
    )
    base_url: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse server URL. Falls back to LANGFUSE_BASE_URL env var.",
    )
    environment: str = "development"
    flush_at: int = 512
    flush_interval: float = 5.0
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    debug: bool = False


class ObservabilityConfig(BaseModel):
    """Top-level observability configuration."""
    enabled: bool = Field(
        default=False,
        description="Enable observability tracing. When false, all tracing is no-op.",
    )
    provider: str = Field(
        default="langfuse",
        description="Observability provider. Currently supported: 'langfuse', 'noop'.",
    )
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
```

### 5.3 GlobalConfig Integration

```python
# Modification to GlobalConfig (or equivalent)
class GlobalConfig(BaseModel):
    # ... existing fields ...
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
```

Missing `observability:` section in YAML results in defaults (`enabled=False`) — zero behavior change for existing deployments.

### 5.4 Environment Variable Fallback

Langfuse's SDK natively reads `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL` when constructor params are `None`. This means a minimal deployment can skip YAML config entirely:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="http://langfuse:3000"
```

And in `config.yaml`, just:
```yaml
observability:
  enabled: true
```

---

## 6. Trace Structure

A single agent execution produces the following Langfuse trace tree:

```
Trace: "sgr_tools_agent" (as_type=agent)
├── user_id: "user-42"
├── session_id: "session-abc"
├── tags: ["SGRToolCallingAgent", "sgr_tools_agent"]
├── metadata: {agent_id: "sgr_agent_12345", model: "gpt-4.1-mini", max_iterations: 7}
├── input: {task: "Research AI market trends in 2025"}
│
├── Span: "iteration-1" (as_type=span)
│   ├── Generation: "reasoning-phase" (as_type=generation)  ← auto-captured
│   │   ├── model: "gpt-4.1-mini"
│   │   ├── input: {messages: [...], tools: [ReasoningTool]}
│   │   ├── output: {tool_calls: [{function: "reasoning", arguments: {...}}]}
│   │   ├── usage: {input_tokens: 1250, output_tokens: 180}
│   │   └── latency: 1.2s
│   │
│   ├── Generation: "select-action" (as_type=generation)  ← auto-captured
│   │   ├── model: "gpt-4.1-mini"
│   │   ├── output: {tool_calls: [{function: "web_search_tool", arguments: {...}}]}
│   │   ├── usage: {input_tokens: 1400, output_tokens: 95}
│   │   └── latency: 0.8s
│   │
│   └── Span: "tool-web_search_tool" (as_type=tool)
│       ├── input: {tool: "web_search_tool", args: {query: "AI market trends 2025"}}
│       ├── output: {result_preview: "..."}
│       └── latency: 2.1s
│
├── Span: "iteration-2"
│   └── ... (same structure)
│
├── Span: "iteration-3"
│   └── ... GeneratePlanTool, then CreateReportTool ...
│
└── output: {result_preview: "## AI Market Trends 2025\n..."}
```

### 6.1 What Gets Captured Automatically (Zero Code)

The Langfuse OpenAI wrapper captures all of these for every LLM call with no additional instrumentation:

| Field | Source |
|-------|--------|
| `model` | From `chat.completions.create(model=...)` |
| `input` (messages) | Full conversation context sent to LLM |
| `output` (response) | Complete LLM response including tool calls |
| `usage.input_tokens` | From OpenAI response `usage` field |
| `usage.output_tokens` | From OpenAI response `usage` field |
| `latency` | Wall-clock time of the API call |
| `completion_start_time` | Time-to-first-token (streaming only) |
| `model_parameters` | temperature, max_tokens, tool_choice, etc. |
| Structured output schema | Extracted from `response_format` Pydantic models |
| Tool call arguments | Assembled from streamed chunks |

### 6.2 What Gets Captured via Provider Calls (Explicit Code)

| Field | Source |
|-------|--------|
| Agent run trace (root) | `start_trace()` / `end_trace()` |
| Iteration spans | `start_span()` / `end_span()` per loop iteration |
| Tool execution spans | `start_span()` / `end_span()` per tool call |
| Agent metadata | agent_id, agent_type, model, max_iterations |
| Request context | user_id, session_id (from `request_metadata`) |
| Error attribution | Error messages + ERROR level on failed spans |
| Quality scores | `score_trace()` (future: automated scoring) |

### 6.3 Cost Tracking

Langfuse automatically calculates cost when token usage is present and a matching model definition exists. Langfuse ships with predefined pricing for OpenAI, Anthropic, and Google models. For custom/local models, define pricing in the Langfuse UI or via API:

```python
langfuse.create_model(
    name="gpt-4.1-mini",
    input_price=0.40 / 1_000_000,   # per token
    output_price=1.60 / 1_000_000,
)
```

Cost aggregates across all generations in a trace, giving per-agent-run cost visibility.

---

## 7. Streaming SSE Compatibility

SGR Agent Core streams LLM responses to clients via SSE. The Langfuse OpenAI wrapper handles streaming transparently:

1. `chat.completions.create(stream=True)` returns a `LangfuseResponseGeneratorAsync` wrapping the OpenAI async stream.
2. The wrapper accumulates chunks as they are yielded — the agent's existing stream-consumption code works unmodified.
3. After the stream is fully consumed, the wrapper logs the complete generation with assembled content, token usage, and timing.
4. Token usage in streams requires `stream_options={"include_usage": True}` on the OpenAI call — this should be added to all LLM calls if not already present.

**Action required:** Verify that SGR's LLM calls include `stream_options={"include_usage": True}`. If not, add it to ensure accurate token tracking. Without it, Langfuse estimates tokens using built-in tokenizers (less accurate for non-OpenAI models).

---

## 8. Async Context Propagation

Langfuse SDK v4 uses OpenTelemetry's context, which is built on Python's `contextvars`. This has direct implications for SGR:

### 8.1 What Works Automatically

| Pattern | Status | Notes |
|---------|--------|-------|
| `async`/`await` in agent loop | ✅ | `contextvars` propagate across `await` boundaries |
| Sequential tool execution | ✅ | Same async task, same context |
| `asyncio.gather()` for parallel tools | ✅ | Each coroutine copies parent context |

### 8.2 What Needs Care

| Pattern | Status | Notes |
|---------|--------|-------|
| `ThreadPoolExecutor` | ⚠️ | Context not auto-propagated. Use `contextvars.copy_context()` |
| `ProcessPoolExecutor` | ❌ | Cannot propagate OTel context across processes |

SGR Agent Core is pure `asyncio` — the agent loop, LLM calls, and tool execution are all `async`/`await`. There are no `ThreadPoolExecutor` calls in the critical path. This means **context propagation works correctly out of the box** with no workarounds.

If future tools use `run_in_executor()` for CPU-bound work, the context must be manually copied:

```python
import contextvars
ctx = contextvars.copy_context()
result = await loop.run_in_executor(executor, ctx.run, blocking_fn, *args)
```

---

## 9. Server Lifecycle Integration

### 9.1 Startup

The observability provider is initialized during server startup, before any agents are created:

```python
# server/server.py — startup hook

from sgr_agent_core.observability import init_provider

@app.on_event("startup")
async def startup():
    config = GlobalConfig.from_yaml(...)
    init_provider(config)
    # ... rest of startup
```

### 9.2 Shutdown

Graceful shutdown flushes pending Langfuse events:

```python
from sgr_agent_core.observability import get_provider

@app.on_event("shutdown")
async def shutdown():
    get_provider().shutdown()
```

### 9.3 MCP `ask` Tool (Feature 001)

The MCP `ask` handler propagates user context to the observability layer via `request_metadata`:

```python
# mcp_server/server.py — sketch
@mcp.tool()
async def ask(query: str, traceId: str = ..., userId: str = ...):
    agent = await AgentFactory.create(
        agent_def=...,
        task_messages=[{"role": "user", "content": query}],
        request_metadata={
            "traceId": traceId,
            "userId": userId,
        },
    )
    # Agent's execute() will call start_trace() with userId from request_metadata
    result = await agent.execute()
    ...
```

---

## 10. Graceful Degradation

The design is fail-silent at every layer:

| Failure Mode | Behavior |
|--------------|----------|
| Langfuse server unreachable | SDK queues events in memory, retries on flush, never throws |
| `langfuse` package not installed | `init_provider()` catches ImportError → falls back to NoOpProvider |
| `observability.enabled: false` | NoOpProvider used — all calls are zero-cost no-ops |
| Individual tracing call fails | Caught by try/except in LangfuseProvider, logged as warning |
| All `LANGFUSE_*` env vars missing + no YAML config | SDK initializes with None keys → tracing silently disabled |

**Critical guarantee:** Observability must never crash or slow down agent execution. The Langfuse SDK's in-memory event queue and background-thread export ensure near-zero latency overhead. The LangfuseProvider wraps every call in try/except as a second safety net.

---

## 11. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SGR Agent Core Server                           │
│                                                                     │
│  ┌───────────────┐    ┌─────────────────────────────────────────┐   │
│  │  GlobalConfig  │───▶│  ObservabilityConfig                    │   │
│  │                │    │    enabled: true                        │   │
│  │                │    │    provider: "langfuse"                 │   │
│  │                │    │    langfuse:                            │   │
│  │                │    │      public_key: "pk-lf-..."            │   │
│  │                │    │      base_url: "http://langfuse:3000"   │   │
│  └───────────────┘    └──────────────┬──────────────────────────┘   │
│                                      │                              │
│                              init_provider()                        │
│                                      │                              │
│                                      ▼                              │
│                        ┌──────────────────────────┐                 │
│                        │  LangfuseProvider         │                │
│                        │  (implements               │                │
│                        │   ObservabilityProvider)   │                │
│                        └──────┬───────────────┬────┘                │
│                               │               │                     │
│              create_openai_client()    start_trace / start_span     │
│                               │               │                     │
│                               ▼               ▼                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                     BaseAgent.execute()                         │ │
│  │                                                                 │ │
│  │  Trace: agent-run                                               │ │
│  │  ├── Span: iteration-1                                          │ │
│  │  │   ├── Generation: reasoning (auto — langfuse.openai wrapper) │ │
│  │  │   ├── Generation: select-action (auto)                       │ │
│  │  │   └── Span: tool-web_search_tool (explicit)                  │ │
│  │  ├── Span: iteration-2                                          │ │
│  │  │   └── ...                                                    │ │
│  │  └── Span: iteration-N                                          │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  Background thread (OTel BatchSpanProcessor)
                          │  Non-blocking, async HTTP export
                          ▼
                ┌───────────────────────┐
                │  Langfuse Server      │
                │  (self-hosted or      │
                │   cloud.langfuse.com) │
                │                       │
                │  ┌─────────────────┐  │
                │  │ Trace Explorer  │  │
                │  │ Cost Dashboard  │  │
                │  │ Token Analytics │  │
                │  │ Score Tracking  │  │
                │  └─────────────────┘  │
                └───────────────────────┘
```

---

## 12. Alignment with Platform Principles

| Principle | Alignment | Notes |
|-----------|-----------|-------|
| **P1: Deterministic First, Semantic Second** | ✅ | Tracing is purely deterministic instrumentation — no LLM involvement |
| **P2: Prefer Agent-as-Tool** | ✅ Neutral | Does not affect coordination patterns. Traces compose naturally when agents call agents |
| **P3: Workspace Isolation** | ✅ | Langfuse projects provide tenant-level isolation; `environment` tag separates stages |
| **P4: Gateway as Single Control Point** | ✅ Neutral | Observability is not an enforcement layer — it passively collects data |
| **P5: Evolution, Not Revolution** | ✅ | Extends existing `BaseAgent` with 6 call sites. No methods renamed, no interfaces changed. NoOpProvider = zero behavior change for existing deployments |
| **P6: Self-Improving Loop** | ✅✅ | **This is the foundation for the self-improving loop.** Traces provide the data (latency, cost, token usage, tool selection patterns) that future Failure Attribution and Entropy Management components will consume. Score API enables automated quality grading |

### Layer Positioning

Observability sits in **Layer 1: Foundation Services** — alongside Auth, RBAC, LLM Routing, and Storage. It is a cross-cutting concern consumed by all upper layers but not on the critical path of any.

---

## 13. Dependencies

### 13.1 New Package Dependency

```toml
# pyproject.toml — addition
[project.optional-dependencies]
observability = [
    "langfuse>=4.0.0",
]
```

Langfuse is an **optional** dependency. The framework works without it — `init_provider()` catches `ImportError` and falls back to `NoOpProvider`. Install with:

```bash
pip install sgr-agent-core[observability]
```

### 13.2 Dependency Compatibility

| SGR Dependency | Langfuse Requirement | Compatible |
|----------------|---------------------|------------|
| `pydantic` ≥ 2.0 | Pydantic v2 | ✅ |
| `openai` ≥ 1.0 | OpenAI SDK ≥ 1.0 | ✅ |
| Python ≥ 3.11 | Python ≥ 3.8 | ✅ |
| `httpx` ≥ 0.25.0 | No direct conflict | ✅ |

No version conflicts. Langfuse SDK v4 is designed for the same dependency versions SGR already requires.

---

## 14. Future Extensions (Out of Scope)

These are explicitly **not** part of this proposal but are enabled by the observability foundation:

| Extension | Description | Enabler |
|-----------|-------------|---------|
| **MCP Trace Propagation** | Propagate Langfuse trace IDs through MCPPayloadProcessor (feature 002) for end-to-end multi-agent traces | `request_metadata.traceId` → processor → child agent |
| **Automated Scoring** | LLM-as-judge scoring of agent outputs via Langfuse evaluation pipelines | `score_trace()` API |
| **Failure Attribution Engine** | Consume Langfuse traces to identify failure patterns and auto-correct agent context | Langfuse data export API |
| **Entropy Scanner** | Background agent that queries Langfuse for quality degradation signals | Langfuse metrics API |
| **Cost Alerting** | Per-agent or per-user cost thresholds triggering alerts | Langfuse cost tracking |
| **Prompt Management** | Version and A/B test system prompts via Langfuse prompt management | Langfuse prompt API |
| **Custom Provider** | Implement `ObservabilityProvider` for OpenTelemetry, Datadog, etc. | Abstract base class |

---

## 15. Implementation Tasks

| # | Task | Dependencies | Parallel |
|---|------|-------------|----------|
| 1 | Create `ObservabilityProvider` ABC, `TraceHandle`, `SpanHandle` | — | — |
| 2 | Create `NoOpProvider` | Task 1 | — |
| 3 | Create `ObservabilityConfig`, `LangfuseConfig` Pydantic models | — | [P] |
| 4 | Integrate `ObservabilityConfig` into `GlobalConfig` + update `config.yaml.example` | Task 3 | — |
| 5 | Create `init_provider()` / `get_provider()` module with lazy init and ImportError fallback | Tasks 1, 2, 3 | — |
| 6 | Implement `LangfuseProvider` (start_trace, start_span, end_span, end_trace, score_trace, flush, shutdown, create_openai_client) | Tasks 1, 3 | — |
| 7 | Modify `BaseAgent.execute()` to call provider at 6 instrumentation points (trace start/end, iteration span start/end, tool span start/end) | Tasks 1, 5 | — |
| 8 | Modify OpenAI client creation to use `provider.create_openai_client()` instead of direct `AsyncOpenAI` import | Tasks 5, 6 | — |
| 9 | Add `stream_options={"include_usage": True}` to all LLM calls (if not already present) | — | [P] |
| 10 | Add provider `init_provider()` to server startup and `shutdown()` to server shutdown | Tasks 4, 5 | — |
| 11 | Add `langfuse` as optional dependency in `pyproject.toml` (`[observability]` extra) | — | [P] |
| 12 | Unit tests: `NoOpProvider` (all methods callable, zero side effects) | Task 2 | [P] |
| 13 | Unit tests: `ObservabilityConfig` / `LangfuseConfig` (defaults, validation, YAML parsing) | Task 3 | [P] |
| 14 | Unit tests: `init_provider()` with missing langfuse package (ImportError → NoOp), disabled config, langfuse config | Tasks 5, 6 | — |
| 15 | Integration test: full agent execution with `LangfuseProvider` using mocked Langfuse SDK (verify trace tree structure) | Tasks 6, 7, 8 | — |
| 16 | Integration test: verify no behavior change with `observability.enabled: false` | Tasks 7, 8 | [P] |

### Task Dependency Graph

```
[1] ObservabilityProvider ABC
 ├──[2] NoOpProvider ──────────────────────[12] Unit tests: NoOp
 ├──[5] init_provider / get_provider ──────[14] Unit tests: init
 │   └──[7] Instrument BaseAgent.execute() ─[15] Integration test: trace tree
 │   └──[8] OpenAI client via provider ─────[16] Integration test: no-op path
 └──[6] LangfuseProvider

[3] Config models ─────[4] GlobalConfig integration ─[10] Server lifecycle
 └──[13] Unit tests: config

[9] stream_options  (independent)
[11] pyproject.toml (independent)
```

---

## 16. Summary

The Langfuse observability integration introduces a clean, provider-abstracted tracing layer to SGR Agent Core with two mechanisms: **automatic LLM call instrumentation** via the Langfuse OpenAI drop-in wrapper (zero code changes to agent logic) and **explicit agent loop instrumentation** via 6 `ObservabilityProvider` call sites in `BaseAgent.execute()`. The design is fail-silent at every layer, adds near-zero latency (in-memory writes with background export), and has zero impact on existing deployments when disabled.

The `ObservabilityProvider` abstraction keeps Langfuse as an optional dependency behind a clean interface, allowing future providers (OpenTelemetry, Datadog) without framework changes. The `NoOpProvider` default ensures the framework works identically to current behavior until observability is explicitly enabled.

Most importantly, this lays the **foundation for the Self-Improving Loop** (Principle 6): structured traces with cost, latency, token usage, and tool selection data are the prerequisite for Failure Attribution, Entropy Management, and automated quality scoring — all of which consume observability data to drive continuous improvement.
