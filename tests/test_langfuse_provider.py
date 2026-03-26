"""Integration tests for LangfuseProvider with mocked Langfuse SDK."""

from unittest.mock import MagicMock, patch

import pytest

from sgr_agent_core.observability.config import LangfuseConfig
from sgr_agent_core.observability.langfuse_provider import LangfuseProvider, LangfuseSpanHandle, LangfuseTraceHandle


@pytest.fixture
def mock_langfuse():
    """Create a mock Langfuse client and patch the import."""
    mock_client = MagicMock()

    # Mock start_as_current_observation to return a context manager
    def make_cm(**kwargs):
        cm = MagicMock()
        obs = MagicMock()
        cm.__enter__ = MagicMock(return_value=obs)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    mock_client.start_as_current_observation = MagicMock(side_effect=make_cm)
    mock_client.flush = MagicMock()
    mock_client.shutdown = MagicMock()

    return mock_client


@pytest.fixture
def provider(mock_langfuse):
    """Create a LangfuseProvider with mocked SDK."""
    config = LangfuseConfig(
        public_key="pk-test",
        secret_key="sk-test",
        base_url="http://localhost:3000",
        environment="test",
    )
    with patch("sgr_agent_core.observability.langfuse_provider.Langfuse", return_value=mock_langfuse):
        # Need to patch at import time
        import sgr_agent_core.observability.langfuse_provider as lp_module

        with patch.object(lp_module, "Langfuse", create=True):
            # Direct construction with mocked client
            p = LangfuseProvider.__new__(LangfuseProvider)
            p._config = config
            p._client = mock_langfuse
            return p


class TestLangfuseProviderTracing:
    def test_start_trace_returns_handle(self, provider):
        with patch("sgr_agent_core.observability.langfuse_provider.propagate_attributes") as mock_prop:
            mock_prop_cm = MagicMock()
            mock_prop_cm.__enter__ = MagicMock()
            mock_prop_cm.__exit__ = MagicMock()
            mock_prop.return_value = mock_prop_cm

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
            assert handle.observation is not None

    def test_start_span_returns_handle(self, provider):
        handle = provider.start_span(
            name="iteration-1",
            span_type="span",
            input={"iteration": 1},
        )
        assert isinstance(handle, LangfuseSpanHandle)
        assert handle.observation is not None

    def test_end_span_calls_update_and_exit(self, provider):
        handle = provider.start_span(name="test-span")
        provider.end_span(handle, output={"result": "ok"}, status="done")
        handle.observation.update.assert_called_once()
        handle.context_manager.__exit__.assert_called_once()

    def test_end_trace_closes_context_managers(self, provider):
        with patch("sgr_agent_core.observability.langfuse_provider.propagate_attributes") as mock_prop:
            mock_prop_cm = MagicMock()
            mock_prop_cm.__enter__ = MagicMock()
            mock_prop_cm.__exit__ = MagicMock()
            mock_prop.return_value = mock_prop_cm

            trace = provider.start_trace(name="agent", agent_id="1")
            provider.end_trace(trace, output={"result": "done"}, status="completed")
            trace.observation.update.assert_called_once()

    def test_flush_calls_client(self, provider, mock_langfuse):
        provider.flush()
        mock_langfuse.flush.assert_called_once()

    def test_shutdown_calls_client(self, provider, mock_langfuse):
        provider.shutdown()
        mock_langfuse.flush.assert_called_once()
        mock_langfuse.shutdown.assert_called_once()

    def test_score_trace(self, provider):
        with patch("sgr_agent_core.observability.langfuse_provider.propagate_attributes") as mock_prop:
            mock_prop_cm = MagicMock()
            mock_prop_cm.__enter__ = MagicMock()
            mock_prop_cm.__exit__ = MagicMock()
            mock_prop.return_value = mock_prop_cm

            trace = provider.start_trace(name="agent", agent_id="1")
            provider.score_trace(trace, name="accuracy", value=0.95, comment="good")
            trace.observation.score.assert_called_once_with(name="accuracy", value=0.95, comment="good")


    def test_propagate_ctx_stored_on_handle_not_provider(self, provider):
        """Concurrent safety: propagation context is per-trace, not per-provider."""
        with patch("sgr_agent_core.observability.langfuse_provider.propagate_attributes") as mock_prop:
            mock_prop_cm = MagicMock()
            mock_prop_cm.__enter__ = MagicMock()
            mock_prop_cm.__exit__ = MagicMock()
            mock_prop.return_value = mock_prop_cm

            handle = provider.start_trace(name="agent", agent_id="1")
            assert handle.propagate_ctx is not None
            assert not hasattr(provider, "_propagate_ctx")


class TestLangfuseProviderFailSilent:
    def test_start_trace_failure_returns_handle(self, provider, mock_langfuse):
        mock_langfuse.start_as_current_observation.side_effect = RuntimeError("SDK error")
        handle = provider.start_trace(name="test", agent_id="1")
        assert isinstance(handle, LangfuseTraceHandle)
        assert handle.observation is None

    def test_start_span_failure_returns_handle(self, provider, mock_langfuse):
        mock_langfuse.start_as_current_observation.side_effect = RuntimeError("SDK error")
        handle = provider.start_span(name="test")
        assert isinstance(handle, LangfuseSpanHandle)
        assert handle.observation is None

    def test_end_span_with_none_observation_is_safe(self, provider):
        handle = LangfuseSpanHandle(None)
        provider.end_span(handle, output={"test": True})  # Should not raise

    def test_flush_failure_is_silent(self, provider, mock_langfuse):
        mock_langfuse.flush.side_effect = RuntimeError("flush error")
        provider.flush()  # Should not raise

    def test_shutdown_failure_is_silent(self, provider, mock_langfuse):
        mock_langfuse.flush.side_effect = RuntimeError("flush error")
        provider.shutdown()  # Should not raise
