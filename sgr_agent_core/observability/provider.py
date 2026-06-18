"""Abstract base classes for observability providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TraceHandle:
    """Opaque handle to a trace. Implementation-specific."""

    pass


class SpanHandle:
    """Opaque handle to a span. Implementation-specific."""

    pass


class GenerationHandle:
    """Opaque handle to an LLM generation observation. Implementation-specific."""

    pass


class ObservabilityProvider(ABC):
    """Abstract base for observability providers.

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
        span_type: str = "span",
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        _parent: TraceHandle | SpanHandle | None = None,
    ) -> SpanHandle:
        """Start a child span. Pass _parent to nest under a specific trace/span."""
        ...

    @abstractmethod
    def end_span(
        self,
        handle: SpanHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
        level: str = "DEFAULT",
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
        tags: list[str] | None = None,
    ) -> None:
        """End the root trace. ``tags`` replaces the trace's tag list (merge upstream)."""
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

    @abstractmethod
    def start_generation(
        self,
        *,
        name: str,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        _parent: TraceHandle | SpanHandle | None = None,
    ) -> GenerationHandle:
        """Start an LLM generation observation."""
        ...

    @abstractmethod
    def end_generation(
        self,
        handle: GenerationHandle,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        level: str = "DEFAULT",
        status: str | None = None,
    ) -> None:
        """End a generation and record output/usage."""
        ...

    def create_openai_client(
        self,
        *,
        api_key: str,
        base_url: str,
        http_client: Any = None,
    ) -> Any:
        """Create an OpenAI client with optional auto-instrumentation.

        Default: returns a standard AsyncOpenAI client.
        Langfuse override: returns a langfuse.openai.AsyncOpenAI client.
        """
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
        if http_client is not None:
            kwargs["http_client"] = http_client
        return AsyncOpenAI(**kwargs)
