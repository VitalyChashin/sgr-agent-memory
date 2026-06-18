"""Langfuse-backed observability provider (SDK v2.x)."""

from __future__ import annotations

import logging
from typing import Any

from sgr_agent_core.observability.config import LangfuseConfig
from sgr_agent_core.observability.provider import GenerationHandle, ObservabilityProvider, SpanHandle, TraceHandle

logger = logging.getLogger(__name__)


class LangfuseTraceHandle(TraceHandle):
    """Wraps a Langfuse StatefulTraceClient."""

    def __init__(self, trace: Any):
        self.trace = trace


class LangfuseSpanHandle(SpanHandle):
    """Wraps a Langfuse StatefulSpanClient."""

    def __init__(self, span: Any):
        self.span = span


class LangfuseGenerationHandle(GenerationHandle):
    """Wraps a Langfuse StatefulGenerationClient."""

    def __init__(self, generation: Any):
        self.generation = generation


class LangfuseProvider(ObservabilityProvider):
    """Langfuse-backed observability provider.

    Uses the Langfuse Python SDK v2.x imperative API:
    - langfuse.trace() creates a root trace
    - trace.span() / span.span() creates child spans
    - span.end() / trace.update() records output

    All operations are fail-silent: observability must never
    crash or slow down agent execution.
    """

    def __init__(self, config: LangfuseConfig):
        from langfuse import Langfuse

        self._config = config

        # Unwrap SecretStr if present
        secret_key = config.secret_key.get_secret_value() if config.secret_key else None

        # Try full kwargs first, fall back to minimal if SDK version doesn't support all params
        try:
            self._client = Langfuse(
                public_key=config.public_key,
                secret_key=secret_key,
                host=config.base_url,
                debug=config.debug,
                flush_at=config.flush_at,
                flush_interval=config.flush_interval,
                sample_rate=config.sample_rate,
                enabled=True,
            )
        except TypeError:
            # Older SDK version — use only core params
            self._client = Langfuse(
                public_key=config.public_key,
                secret_key=secret_key,
                host=config.base_url,
                debug=config.debug,
            )
        logger.info(
            "Langfuse observability initialized (host=%s)",
            config.base_url,
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
            merged_metadata = {"agent_id": agent_id, **(metadata or {})}
            trace = self._client.trace(
                name=name,
                user_id=user_id,
                session_id=session_id,
                input=input,
                metadata=merged_metadata,
                tags=list(tags or []),
            )
            return LangfuseTraceHandle(trace)
        except Exception as e:
            logger.warning("Langfuse start_trace failed (non-fatal): %s: %s", type(e).__name__, e)
            return LangfuseTraceHandle(None)

    def start_span(
        self,
        *,
        name: str,
        span_type: str = "span",
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        _parent: LangfuseTraceHandle | LangfuseSpanHandle | None = None,
    ) -> LangfuseSpanHandle:
        try:
            # Determine parent from handle
            parent = None
            if isinstance(_parent, LangfuseTraceHandle) and _parent.trace is not None:
                parent = _parent.trace
            elif isinstance(_parent, LangfuseSpanHandle) and _parent.span is not None:
                parent = _parent.span

            if parent is not None:
                span = parent.span(
                    name=name,
                    input=input,
                    metadata=metadata,
                )
            else:
                # Fallback: create a top-level span (no parent available)
                span = self._client.span(
                    name=name,
                    input=input,
                    metadata=metadata,
                )
            return LangfuseSpanHandle(span)
        except Exception as e:
            logger.warning("Langfuse start_span failed (non-fatal): %s: %s", type(e).__name__, e)
            return LangfuseSpanHandle(None)

    def end_span(
        self,
        handle: SpanHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
        level: str = "DEFAULT",
    ) -> None:
        try:
            if isinstance(handle, LangfuseSpanHandle) and handle.span is not None:
                handle.span.end(
                    output=output,
                    level=level,
                    status_message=status,
                )
        except Exception as e:
            logger.warning("Langfuse end_span failed (non-fatal): %s: %s", type(e).__name__, e)

    def end_trace(
        self,
        handle: TraceHandle,
        *,
        output: dict[str, Any] | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        try:
            if isinstance(handle, LangfuseTraceHandle) and handle.trace is not None:
                # trace.update(tags=...) replaces the tag list, so callers pass the
                # full merged set (start-time tags + error tags).
                update_kwargs: dict[str, Any] = {"output": output, "status_message": status}
                if tags:
                    update_kwargs["tags"] = tags
                handle.trace.update(**update_kwargs)
        except Exception as e:
            logger.warning("Langfuse end_trace failed (non-fatal): %s: %s", type(e).__name__, e)

    def score_trace(
        self,
        handle: TraceHandle,
        *,
        name: str,
        value: float | str | bool,
        comment: str | None = None,
    ) -> None:
        try:
            if isinstance(handle, LangfuseTraceHandle) and handle.trace is not None:
                handle.trace.score(
                    name=name,
                    value=value,
                    comment=comment,
                )
        except Exception as e:
            logger.warning("Langfuse score_trace failed (non-fatal): %s: %s", type(e).__name__, e)

    def start_generation(
        self,
        *,
        name: str,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        _parent: LangfuseTraceHandle | LangfuseSpanHandle | None = None,
    ) -> LangfuseGenerationHandle:
        try:
            parent = None
            if isinstance(_parent, LangfuseTraceHandle) and _parent.trace is not None:
                parent = _parent.trace
            elif isinstance(_parent, LangfuseSpanHandle) and _parent.span is not None:
                parent = _parent.span

            if parent is not None:
                gen = parent.generation(
                    name=name,
                    model=model,
                    model_parameters=model_parameters,
                    input=input,
                    metadata=metadata,
                )
            else:
                gen = self._client.generation(
                    name=name,
                    model=model,
                    model_parameters=model_parameters,
                    input=input,
                    metadata=metadata,
                )
            return LangfuseGenerationHandle(gen)
        except Exception as e:
            logger.warning("Langfuse start_generation failed (non-fatal): %s: %s", type(e).__name__, e)
            return LangfuseGenerationHandle(None)

    def end_generation(
        self,
        handle: GenerationHandle,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        level: str = "DEFAULT",
        status: str | None = None,
    ) -> None:
        try:
            if isinstance(handle, LangfuseGenerationHandle) and handle.generation is not None:
                handle.generation.end(
                    output=output,
                    usage=usage,
                    level=level,
                    status_message=status,
                )
        except Exception as e:
            logger.warning("Langfuse end_generation failed (non-fatal): %s: %s", type(e).__name__, e)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as e:
            logger.warning("Langfuse flush failed (non-fatal): %s: %s", type(e).__name__, e)

    def shutdown(self) -> None:
        try:
            self._client.flush()
            self._client.shutdown()
        except Exception as e:
            logger.warning("Langfuse shutdown failed (non-fatal): %s: %s", type(e).__name__, e)

    # create_openai_client is inherited from ObservabilityProvider base class.
    # LLM call tracing is handled by explicit generation spans in BaseAgent,
    # not by the Langfuse drop-in OpenAI wrapper (incompatible with .stream()).
