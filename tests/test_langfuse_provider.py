"""Integration tests for LangfuseProvider with a mocked Langfuse SDK (v2 imperative API).

The provider wraps the Langfuse v2 SDK: ``client.trace()`` creates a root trace,
``trace.span()`` / ``client.span()`` create spans, and ``span.end()`` /
``trace.update()`` record output. Handles wrap the SDK objects: ``LangfuseTraceHandle.trace``,
``LangfuseSpanHandle.span``, ``LangfuseGenerationHandle.generation``.
"""

from unittest.mock import MagicMock

import pytest

from sgr_agent_core.observability.config import LangfuseConfig
from sgr_agent_core.observability.langfuse_provider import (
    LangfuseGenerationHandle,
    LangfuseProvider,
    LangfuseSpanHandle,
    LangfuseTraceHandle,
)


@pytest.fixture
def mock_client():
    """A mocked Langfuse v2 client. trace()/span()/generation() return fresh mocks."""
    client = MagicMock()
    client.trace = MagicMock(side_effect=lambda **kw: MagicMock(name="trace"))
    client.span = MagicMock(side_effect=lambda **kw: MagicMock(name="span"))
    client.generation = MagicMock(side_effect=lambda **kw: MagicMock(name="generation"))
    client.flush = MagicMock()
    client.shutdown = MagicMock()
    return client


@pytest.fixture
def provider(mock_client):
    """A LangfuseProvider with the SDK client mocked (bypasses __init__)."""
    p = LangfuseProvider.__new__(LangfuseProvider)
    p._config = LangfuseConfig(
        public_key="pk-test",
        secret_key="sk-test",
        base_url="http://localhost:3000",
        environment="test",
    )
    p._client = mock_client
    return p


class TestLangfuseProviderTracing:
    def test_start_trace_returns_handle(self, provider, mock_client):
        handle = provider.start_trace(
            name="test_agent",
            agent_id="agent-1",
            input={"task": "hello"},
            user_id="user-42",
            session_id="session-abc",
            tags=["TestAgent"],
            metadata={"model": "gpt-4o"},
        )
        assert isinstance(handle, LangfuseTraceHandle)
        assert handle.trace is not None
        kwargs = mock_client.trace.call_args.kwargs
        assert kwargs["name"] == "test_agent"
        assert kwargs["user_id"] == "user-42"
        assert kwargs["session_id"] == "session-abc"
        assert kwargs["tags"] == ["TestAgent"]
        # agent_id is merged into metadata
        assert kwargs["metadata"]["agent_id"] == "agent-1"
        assert kwargs["metadata"]["model"] == "gpt-4o"

    def test_start_span_returns_handle(self, provider):
        handle = provider.start_span(name="iteration-1", span_type="span", input={"iteration": 1})
        assert isinstance(handle, LangfuseSpanHandle)
        assert handle.span is not None

    def test_start_span_nests_under_parent_trace(self, provider, mock_client):
        trace_handle = provider.start_trace(name="agent", agent_id="1")
        provider.start_span(name="child", _parent=trace_handle)
        # Parent provided → span created off the parent trace, not the client.
        trace_handle.trace.span.assert_called_once()
        mock_client.span.assert_not_called()

    def test_end_span_calls_end(self, provider):
        handle = provider.start_span(name="test-span")
        provider.end_span(handle, output={"result": "ok"}, status="done", level="DEFAULT")
        handle.span.end.assert_called_once()
        end_kwargs = handle.span.end.call_args.kwargs
        assert end_kwargs["output"] == {"result": "ok"}
        assert end_kwargs["level"] == "DEFAULT"
        assert end_kwargs["status_message"] == "done"

    def test_end_trace_calls_update_with_tags(self, provider):
        handle = provider.start_trace(name="agent", agent_id="1")
        provider.end_trace(handle, output={"result": "done"}, status="completed", tags=["completed", "error:mcp_tool"])
        handle.trace.update.assert_called_once()
        kwargs = handle.trace.update.call_args.kwargs
        assert kwargs["output"] == {"result": "done"}
        assert kwargs["status_message"] == "completed"
        assert kwargs["tags"] == ["completed", "error:mcp_tool"]

    def test_start_generation_returns_handle(self, provider):
        handle = provider.start_generation(name="reasoning", model="gpt-4o", input=[{"role": "user"}])
        assert isinstance(handle, LangfuseGenerationHandle)
        assert handle.generation is not None

    def test_end_generation_calls_end(self, provider):
        handle = provider.start_generation(name="reasoning")
        provider.end_generation(handle, output={"text": "hi"}, usage={"total_tokens": 10})
        handle.generation.end.assert_called_once()

    def test_flush_calls_client(self, provider, mock_client):
        provider.flush()
        mock_client.flush.assert_called_once()

    def test_shutdown_calls_client(self, provider, mock_client):
        provider.shutdown()
        mock_client.flush.assert_called_once()
        mock_client.shutdown.assert_called_once()

    def test_score_trace(self, provider):
        trace = provider.start_trace(name="agent", agent_id="1")
        provider.score_trace(trace, name="accuracy", value=0.95, comment="good")
        trace.trace.score.assert_called_once_with(name="accuracy", value=0.95, comment="good")


class TestLangfuseProviderFailSilent:
    def test_start_trace_failure_returns_handle(self, provider, mock_client):
        mock_client.trace.side_effect = RuntimeError("SDK error")
        handle = provider.start_trace(name="test", agent_id="1")
        assert isinstance(handle, LangfuseTraceHandle)
        assert handle.trace is None

    def test_start_span_failure_returns_handle(self, provider, mock_client):
        mock_client.span.side_effect = RuntimeError("SDK error")
        handle = provider.start_span(name="test")
        assert isinstance(handle, LangfuseSpanHandle)
        assert handle.span is None

    def test_end_span_with_none_span_is_safe(self, provider):
        provider.end_span(LangfuseSpanHandle(None), output={"test": True})  # Should not raise

    def test_end_trace_with_none_trace_is_safe(self, provider):
        provider.end_trace(LangfuseTraceHandle(None), output={}, tags=["error"])  # Should not raise

    def test_flush_failure_is_silent(self, provider, mock_client):
        mock_client.flush.side_effect = RuntimeError("flush error")
        provider.flush()  # Should not raise

    def test_shutdown_failure_is_silent(self, provider, mock_client):
        mock_client.flush.side_effect = RuntimeError("flush error")
        provider.shutdown()  # Should not raise
