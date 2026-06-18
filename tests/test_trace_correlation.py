"""Integration tests for trace correlation with user/session metadata.

The v2 provider passes ``user_id`` / ``session_id`` straight to ``client.trace()``
so Langfuse can correlate traces by user and session.
"""

from unittest.mock import MagicMock

from sgr_agent_core.observability.config import LangfuseConfig
from sgr_agent_core.observability.langfuse_provider import LangfuseProvider, LangfuseTraceHandle


class TestTraceCorrelation:
    def _make_provider(self):
        """Create a LangfuseProvider with a mocked SDK client."""
        mock_client = MagicMock()
        mock_client.trace = MagicMock(side_effect=lambda **kw: MagicMock(name="trace"))
        config = LangfuseConfig(public_key="pk", secret_key="sk")

        provider = LangfuseProvider.__new__(LangfuseProvider)
        provider._config = config
        provider._client = mock_client
        return provider, mock_client

    def test_user_id_propagated_to_trace(self):
        provider, mock_client = self._make_provider()

        provider.start_trace(
            name="agent",
            agent_id="agent-1",
            user_id="user-42",
            session_id="session-abc",
        )

        mock_client.trace.assert_called_once()
        kwargs = mock_client.trace.call_args.kwargs
        assert kwargs["user_id"] == "user-42"
        assert kwargs["session_id"] == "session-abc"

    def test_different_users_get_different_attributes(self):
        """Two separate traces with different user/session IDs."""
        provider1, client1 = self._make_provider()
        provider2, client2 = self._make_provider()

        provider1.start_trace(name="agent", agent_id="1", user_id="alice", session_id="s1")
        assert client1.trace.call_args.kwargs["user_id"] == "alice"

        provider2.start_trace(name="agent", agent_id="2", user_id="bob", session_id="s2")
        assert client2.trace.call_args.kwargs["user_id"] == "bob"

    def test_none_user_id_is_accepted(self):
        """Traces without user metadata are valid."""
        provider, mock_client = self._make_provider()

        handle = provider.start_trace(name="agent", agent_id="1", user_id=None, session_id=None)

        assert isinstance(handle, LangfuseTraceHandle)
        kwargs = mock_client.trace.call_args.kwargs
        assert kwargs["user_id"] is None
        assert kwargs["session_id"] is None
