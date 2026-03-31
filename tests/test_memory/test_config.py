"""Tests for MemoryConfig validation and defaults."""

import textwrap

import pytest
from pydantic import ValidationError

from sgr_agent_core.memory.config import MemoryConfig


class TestMemoryConfigDefaults:
    def test_disabled_by_default(self):
        config = MemoryConfig()
        assert config.enabled is False

    def test_default_service_url(self):
        config = MemoryConfig()
        assert config.service_url == "http://localhost:9100"

    def test_default_timeout(self):
        config = MemoryConfig()
        assert config.timeout == 0.3

    def test_default_max_messages(self):
        config = MemoryConfig()
        assert config.max_messages == 50


class TestMemoryConfigValidation:
    def test_timeout_must_be_positive(self):
        with pytest.raises(ValidationError):
            MemoryConfig(timeout=0)

    def test_timeout_must_not_be_negative(self):
        with pytest.raises(ValidationError):
            MemoryConfig(timeout=-1)

    def test_max_messages_must_be_positive(self):
        with pytest.raises(ValidationError):
            MemoryConfig(max_messages=0)

    def test_max_messages_must_not_be_negative(self):
        with pytest.raises(ValidationError):
            MemoryConfig(max_messages=-5)

    def test_valid_custom_config(self):
        config = MemoryConfig(
            enabled=True,
            service_url="http://memory:9200",
            timeout=0.5,
            max_messages=100,
        )
        assert config.enabled is True
        assert config.service_url == "http://memory:9200"
        assert config.timeout == 0.5
        assert config.max_messages == 100


class TestMemoryConfigFromYaml:
    """T030-T032 — Config loading from YAML and environment variables."""

    def test_global_config_with_memory_section(self, tmp_path):
        """T030: GlobalConfig.from_yaml() with memory section populates MemoryConfig."""
        from sgr_agent_core.agent_config import GlobalConfig

        # Reset singleton
        GlobalConfig._instance = None
        GlobalConfig._initialized = False

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
                memory:
                  enabled: true
                  service_url: "http://memory-svc:9200"
                  timeout: 0.5
                  max_messages: 100
            """)
        )

        try:
            config = GlobalConfig.from_yaml(str(config_file))
            assert config.memory.enabled is True
            assert config.memory.service_url == "http://memory-svc:9200"
            assert config.memory.timeout == 0.5
            assert config.memory.max_messages == 100
        finally:
            GlobalConfig._instance = None
            GlobalConfig._initialized = False

    def test_global_config_without_memory_section(self, tmp_path):
        """T031: GlobalConfig without memory section has memory.enabled == False."""
        from sgr_agent_core.agent_config import GlobalConfig

        GlobalConfig._instance = None
        GlobalConfig._initialized = False

        config_file = tmp_path / "config.yaml"
        config_file.write_text("agents: {}\n")

        try:
            config = GlobalConfig.from_yaml(str(config_file))
            assert config.memory.enabled is False
        finally:
            GlobalConfig._instance = None
            GlobalConfig._initialized = False

    def test_env_variable_overrides_memory_enabled(self, tmp_path, monkeypatch):
        """T032: SGR__MEMORY__ENABLED=true activates memory."""
        from sgr_agent_core.agent_config import GlobalConfig

        GlobalConfig._instance = None
        GlobalConfig._initialized = False

        monkeypatch.setenv("SGR__MEMORY__ENABLED", "true")

        config_file = tmp_path / "config.yaml"
        config_file.write_text("agents: {}\n")

        try:
            config = GlobalConfig.from_yaml(str(config_file))
            assert config.memory.enabled is True
        finally:
            GlobalConfig._instance = None
            GlobalConfig._initialized = False
