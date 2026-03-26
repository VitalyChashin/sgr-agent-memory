"""Unit tests for init_provider() and get_provider()."""

from unittest.mock import MagicMock, patch

from sgr_agent_core.observability import get_provider, init_provider
from sgr_agent_core.observability.config import LangfuseConfig, ObservabilityConfig
from sgr_agent_core.observability.noop import NoOpProvider


def _make_config(enabled: bool = False, provider: str = "langfuse") -> MagicMock:
    """Create a mock GlobalConfig with observability settings."""
    config = MagicMock()
    config.observability = ObservabilityConfig(
        enabled=enabled,
        provider=provider,
        langfuse=LangfuseConfig(
            public_key="pk-test",
            secret_key="sk-test",
            base_url="http://localhost:3000",
        ),
    )
    return config


class TestInitProvider:
    def test_disabled_returns_noop(self):
        config = _make_config(enabled=False)
        provider = init_provider(config)
        assert isinstance(provider, NoOpProvider)

    def test_unknown_provider_returns_noop(self):
        config = _make_config(enabled=True, provider="unknown_backend")
        provider = init_provider(config)
        assert isinstance(provider, NoOpProvider)

    def test_langfuse_import_error_falls_back_to_noop(self):
        config = _make_config(enabled=True, provider="langfuse")
        with patch.dict("sys.modules", {"sgr_agent_core.observability.langfuse_provider": None}):
            provider = init_provider(config)
            assert isinstance(provider, NoOpProvider)


class TestGetProvider:
    def test_returns_noop_when_not_initialized(self):
        import sgr_agent_core.observability as obs_module

        obs_module._active_provider = None
        provider = get_provider()
        assert isinstance(provider, NoOpProvider)

    def test_returns_initialized_provider(self):
        config = _make_config(enabled=False)
        init_provider(config)
        provider = get_provider()
        assert isinstance(provider, NoOpProvider)
