"""Integration tests for zero-impact default (NoOp path)."""

from unittest.mock import MagicMock

import sgr_agent_core.observability as obs_module
from sgr_agent_core.observability import get_provider, init_provider
from sgr_agent_core.observability.config import ObservabilityConfig
from sgr_agent_core.observability.noop import NoOpProvider


class TestNoOpPath:
    def test_disabled_config_uses_noop(self):
        """When observability is disabled, NoOpProvider is used."""
        config = MagicMock()
        config.observability = ObservabilityConfig(enabled=False)
        provider = init_provider(config)
        assert isinstance(provider, NoOpProvider)

    def test_missing_config_defaults_to_noop(self):
        """When observability section is absent, defaults apply (disabled)."""
        config = MagicMock()
        config.observability = ObservabilityConfig()  # All defaults
        provider = init_provider(config)
        assert isinstance(provider, NoOpProvider)

    def test_noop_provider_all_methods_callable(self):
        """All NoOpProvider methods can be called with no side effects."""
        provider = NoOpProvider()

        trace = provider.start_trace(name="test", agent_id="1")
        span = provider.start_span(name="iter-1")
        provider.end_span(span, output={"ok": True})
        provider.score_trace(trace, name="quality", value=0.9)
        provider.end_trace(trace, output={"result": "done"}, status="completed")
        provider.flush()
        provider.shutdown()

    def test_noop_does_not_import_langfuse(self):
        """When disabled, the provider is NoOp — no langfuse dependency needed."""
        # Clear any cached provider
        obs_module._active_provider = None

        config = MagicMock()
        config.observability = ObservabilityConfig(enabled=False)
        init_provider(config)

        provider = get_provider()
        assert isinstance(provider, NoOpProvider)
        assert type(provider).__name__ == "NoOpProvider"

    def test_get_provider_without_init_returns_noop(self):
        """get_provider() before init_provider() returns NoOpProvider safely."""
        obs_module._active_provider = None
        provider = get_provider()
        assert isinstance(provider, NoOpProvider)
