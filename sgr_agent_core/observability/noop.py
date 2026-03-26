"""No-op observability provider. Zero-cost default when observability is disabled."""

from typing import Any

from sgr_agent_core.observability.provider import GenerationHandle, ObservabilityProvider, SpanHandle, TraceHandle

_NOOP_TRACE = TraceHandle()
_NOOP_SPAN = SpanHandle()
_NOOP_GENERATION = GenerationHandle()


class NoOpProvider(ObservabilityProvider):
    """Zero-cost no-op provider. Used when observability is disabled."""

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
        return _NOOP_TRACE

    def start_span(
        self,
        *,
        name: str,
        span_type: str = "span",
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        _parent: TraceHandle | SpanHandle | None = None,
    ) -> SpanHandle:
        return _NOOP_SPAN

    def end_span(
        self,
        handle: SpanHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
        level: str = "DEFAULT",
    ) -> None:
        pass

    def end_trace(
        self,
        handle: TraceHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> None:
        pass

    def score_trace(
        self,
        handle: TraceHandle,
        *,
        name: str,
        value: float | str | bool,
        comment: str | None = None,
    ) -> None:
        pass

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
        return _NOOP_GENERATION

    def end_generation(
        self,
        handle: GenerationHandle,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        level: str = "DEFAULT",
        status: str | None = None,
    ) -> None:
        pass

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
