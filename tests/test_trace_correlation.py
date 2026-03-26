"""Integration tests for trace correlation with user/session metadata."""

from unittest.mock import MagicMock, patch

from sgr_agent_core.observability.config import LangfuseConfig
from sgr_agent_core.observability.langfuse_provider import LangfuseProvider, LangfuseTraceHandle


class TestTraceCorrelation:
    def _make_provider(self):
        """Create a LangfuseProvider with mocked SDK."""
        mock_client = MagicMock()

        def make_cm(**kwargs):
            cm = MagicMock()
            obs = MagicMock()
            cm.__enter__ = MagicMock(return_value=obs)
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        mock_client.start_as_current_observation = MagicMock(side_effect=make_cm)
        config = LangfuseConfig(public_key="pk", secret_key="sk")

        provider = LangfuseProvider.__new__(LangfuseProvider)
        provider._config = config
        provider._client = mock_client
        provider._propagate_ctx = None
        return provider, mock_client

    def test_user_id_propagated_to_trace(self):
        provider, mock_client = self._make_provider()

        with patch("sgr_agent_core.observability.langfuse_provider.propagate_attributes") as mock_prop:
            mock_prop_cm = MagicMock()
            mock_prop_cm.__enter__ = MagicMock()
            mock_prop_cm.__exit__ = MagicMock()
            mock_prop.return_value = mock_prop_cm

            provider.start_trace(
                name="agent",
                agent_id="agent-1",
                user_id="user-42",
                session_id="session-abc",
            )

            mock_prop.assert_called_once()
            call_kwargs = mock_prop.call_args
            assert call_kwargs.kwargs["user_id"] == "user-42"
            assert call_kwargs.kwargs["session_id"] == "session-abc"

    def test_different_users_get_different_attributes(self):
        """Two separate traces with different user/session IDs."""
        provider1, _ = self._make_provider()
        provider2, _ = self._make_provider()

        with patch("sgr_agent_core.observability.langfuse_provider.propagate_attributes") as mock_prop:
            mock_prop_cm = MagicMock()
            mock_prop_cm.__enter__ = MagicMock()
            mock_prop_cm.__exit__ = MagicMock()
            mock_prop.return_value = mock_prop_cm

            # First user
            provider1.start_trace(name="agent", agent_id="1", user_id="alice", session_id="s1")
            first_call = mock_prop.call_args_list[0]
            assert first_call.kwargs["user_id"] == "alice"

            # Second user
            provider2.start_trace(name="agent", agent_id="2", user_id="bob", session_id="s2")
            second_call = mock_prop.call_args_list[1]
            assert second_call.kwargs["user_id"] == "bob"

    def test_none_user_id_is_accepted(self):
        """Traces without user metadata are valid."""
        provider, _ = self._make_provider()

        with patch("sgr_agent_core.observability.langfuse_provider.propagate_attributes") as mock_prop:
            mock_prop_cm = MagicMock()
            mock_prop_cm.__enter__ = MagicMock()
            mock_prop_cm.__exit__ = MagicMock()
            mock_prop.return_value = mock_prop_cm

            handle = provider.start_trace(
                name="agent",
                agent_id="1",
                user_id=None,
                session_id=None,
            )
            assert isinstance(handle, LangfuseTraceHandle)
            call_kwargs = mock_prop.call_args.kwargs
            assert call_kwargs["user_id"] is None
            assert call_kwargs["session_id"] is None
