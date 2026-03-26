"""Unit tests for ObservabilityConfig and LangfuseConfig."""

import pytest

from sgr_agent_core.observability.config import LangfuseConfig, ObservabilityConfig


class TestObservabilityConfig:
    def test_defaults(self):
        config = ObservabilityConfig()
        assert config.enabled is False
        assert config.provider == "langfuse"
        assert isinstance(config.langfuse, LangfuseConfig)

    def test_enabled_true(self):
        config = ObservabilityConfig(enabled=True)
        assert config.enabled is True

    def test_provider_noop(self):
        config = ObservabilityConfig(provider="noop")
        assert config.provider == "noop"


class TestLangfuseConfig:
    def test_defaults(self):
        config = LangfuseConfig()
        assert config.public_key is None
        assert config.secret_key is None
        assert config.base_url == "https://cloud.langfuse.com"
        assert config.environment == "development"
        assert config.flush_at == 512
        assert config.flush_interval == 5.0
        assert config.sample_rate == 1.0
        assert config.debug is False

    def test_custom_values(self):
        config = LangfuseConfig(
            public_key="pk-lf-test",
            secret_key="sk-lf-test",
            base_url="http://localhost:3000",
            environment="production",
            flush_at=100,
            flush_interval=2.0,
            sample_rate=0.5,
            debug=True,
        )
        assert config.public_key == "pk-lf-test"
        assert config.secret_key == "sk-lf-test"
        assert config.base_url == "http://localhost:3000"
        assert config.environment == "production"
        assert config.flush_at == 100
        assert config.flush_interval == 2.0
        assert config.sample_rate == 0.5
        assert config.debug is True

    def test_sample_rate_validation_min(self):
        with pytest.raises(Exception):
            LangfuseConfig(sample_rate=-0.1)

    def test_sample_rate_validation_max(self):
        with pytest.raises(Exception):
            LangfuseConfig(sample_rate=1.1)

    def test_sample_rate_boundaries(self):
        config_zero = LangfuseConfig(sample_rate=0.0)
        assert config_zero.sample_rate == 0.0
        config_one = LangfuseConfig(sample_rate=1.0)
        assert config_one.sample_rate == 1.0

    def test_flush_at_must_be_positive(self):
        with pytest.raises(Exception):
            LangfuseConfig(flush_at=0)

    def test_flush_interval_must_be_positive(self):
        with pytest.raises(Exception):
            LangfuseConfig(flush_interval=0.0)


class TestObservabilityConfigFromDict:
    def test_from_yaml_like_dict(self):
        """Simulate parsing from YAML config."""
        data = {
            "enabled": True,
            "provider": "langfuse",
            "langfuse": {
                "public_key": "pk-test",
                "secret_key": "sk-test",
                "base_url": "http://langfuse:3000",
                "environment": "staging",
            },
        }
        config = ObservabilityConfig(**data)
        assert config.enabled is True
        assert config.langfuse.public_key == "pk-test"
        assert config.langfuse.environment == "staging"

    def test_missing_section_defaults_to_disabled(self):
        """When observability section is absent from YAML, defaults apply."""
        config = ObservabilityConfig()
        assert config.enabled is False
