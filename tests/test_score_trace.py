"""Unit tests for score_trace() on both NoOp and Langfuse providers."""

from unittest.mock import MagicMock

from sgr_agent_core.observability.langfuse_provider import LangfuseProvider, LangfuseTraceHandle
from sgr_agent_core.observability.noop import NoOpProvider
from sgr_agent_core.observability.provider import TraceHandle


class TestNoOpScoreTrace:
    def test_score_trace_is_noop(self):
        """NoOpProvider.score_trace() does nothing and doesn't raise."""
        provider = NoOpProvider()
        handle = provider.start_trace(name="test", agent_id="1")
        provider.score_trace(handle, name="accuracy", value=0.95, comment="good")

    def test_score_trace_with_string_value(self):
        provider = NoOpProvider()
        handle = provider.start_trace(name="test", agent_id="1")
        provider.score_trace(handle, name="category", value="excellent")

    def test_score_trace_with_bool_value(self):
        provider = NoOpProvider()
        handle = provider.start_trace(name="test", agent_id="1")
        provider.score_trace(handle, name="passed", value=True)


class TestLangfuseScoreTrace:
    def _make_provider(self):
        provider = LangfuseProvider.__new__(LangfuseProvider)
        provider._config = MagicMock()
        provider._client = MagicMock()
        provider._propagate_ctx = None
        return provider

    def test_score_calls_sdk(self):
        """LangfuseProvider.score_trace() calls observation.score()."""
        provider = self._make_provider()
        obs = MagicMock()
        handle = LangfuseTraceHandle(obs)

        provider.score_trace(handle, name="accuracy", value=0.95, comment="good")
        obs.score.assert_called_once_with(name="accuracy", value=0.95, comment="good")

    def test_score_with_string_value(self):
        provider = self._make_provider()
        obs = MagicMock()
        handle = LangfuseTraceHandle(obs)

        provider.score_trace(handle, name="category", value="excellent")
        obs.score.assert_called_once_with(name="category", value="excellent", comment=None)

    def test_score_with_none_observation_is_safe(self):
        """score_trace with None observation doesn't raise."""
        provider = self._make_provider()
        handle = LangfuseTraceHandle(None)
        provider.score_trace(handle, name="test", value=1.0)  # Should not raise

    def test_score_sdk_failure_is_silent(self):
        """SDK error is caught and logged, not raised."""
        provider = self._make_provider()
        obs = MagicMock()
        obs.score.side_effect = RuntimeError("SDK error")
        handle = LangfuseTraceHandle(obs)

        provider.score_trace(handle, name="test", value=1.0)  # Should not raise

    def test_score_with_plain_trace_handle_is_safe(self):
        """Passing a base TraceHandle (not Langfuse) is safe."""
        provider = self._make_provider()
        handle = TraceHandle()  # Plain base handle
        provider.score_trace(handle, name="test", value=1.0)  # Should not raise
