"""Unit tests for RollingSummaryConfig defaults, validation, and per-agent override."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sgr_agent_core.memory.config import MemoryConfig, RollingSummaryConfig


class TestRollingSummaryConfigDefaults:
    def test_enabled_defaults_false(self):
        assert RollingSummaryConfig().enabled is False

    def test_max_tokens_defaults_2000(self):
        assert RollingSummaryConfig().max_tokens_to_summarize == 2000

    def test_summarization_model_defaults_none(self):
        assert RollingSummaryConfig().summarization_model is None

    def test_timeout_defaults_10(self):
        assert RollingSummaryConfig().summarization_timeout_s == 10.0

    def test_no_parallel_field(self):
        assert not hasattr(RollingSummaryConfig(), "parallel_with_first_iteration")

    def test_memory_config_rolling_summary_disabled_by_default(self):
        assert MemoryConfig().rolling_summary.enabled is False


class TestRollingSummaryConfigValidation:
    def test_max_tokens_rejects_below_100(self):
        with pytest.raises(ValidationError):
            RollingSummaryConfig(max_tokens_to_summarize=99)

    def test_max_tokens_rejects_above_32000(self):
        with pytest.raises(ValidationError):
            RollingSummaryConfig(max_tokens_to_summarize=32001)

    def test_max_tokens_accepts_boundaries(self):
        assert RollingSummaryConfig(max_tokens_to_summarize=100).max_tokens_to_summarize == 100
        assert RollingSummaryConfig(max_tokens_to_summarize=32000).max_tokens_to_summarize == 32000

    def test_timeout_rejects_zero(self):
        with pytest.raises(ValidationError):
            RollingSummaryConfig(summarization_timeout_s=0.0)

    def test_timeout_rejects_above_120(self):
        with pytest.raises(ValidationError):
            RollingSummaryConfig(summarization_timeout_s=120.1)

    def test_timeout_accepts_boundaries(self):
        assert RollingSummaryConfig(summarization_timeout_s=0.01).summarization_timeout_s == 0.01
        assert RollingSummaryConfig(summarization_timeout_s=120.0).summarization_timeout_s == 120.0


class TestPerAgentConfigOverride:
    def test_agent_config_accepts_memory_as_extra_field(self):
        from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig

        cfg = AgentConfig(
            llm=LLMConfig(api_key="test-key"),
            prompts=PromptsConfig(system_prompt_str="T", initial_user_request_str="T", clarification_response_str="T"),
            execution=ExecutionConfig(),
            memory=MemoryConfig(rolling_summary=RollingSummaryConfig(enabled=True, max_tokens_to_summarize=4000)),
        )
        assert cfg.memory.rolling_summary.enabled is True
        assert cfg.memory.rolling_summary.max_tokens_to_summarize == 4000

    def test_dict_coercion_works(self):
        """Simulate what the config cascade produces (raw dict via model_dump)."""
        raw = {
            "enabled": True,
            "max_tokens_to_summarize": 4000,
            "summarization_model": None,
            "summarization_timeout_s": 10.0,
        }
        cfg = RollingSummaryConfig(**raw)
        assert cfg.enabled is True
        assert cfg.max_tokens_to_summarize == 4000

    def test_agent_config_without_memory_returns_none(self):
        from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig

        cfg = AgentConfig(
            llm=LLMConfig(api_key="test-key"),
            prompts=PromptsConfig(system_prompt_str="T", initial_user_request_str="T", clarification_response_str="T"),
            execution=ExecutionConfig(),
        )
        rs_cfg = getattr(getattr(cfg, "memory", None), "rolling_summary", None)
        assert rs_cfg is None
