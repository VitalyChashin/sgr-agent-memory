"""Unit tests for MCP payload processor ABC, chain, and registry."""

from unittest.mock import Mock

import pytest

from sgr_agent_core.mcp_payload_processor import (
    MCPPayloadProcessor,
    MCPPayloadProcessorChain,
    PayloadProcessorDefinition,
    ProcessorRegistry,
)


class AddFieldProcessor(MCPPayloadProcessor):
    """Test processor that adds a field."""

    async def pre_call(self, payload, context, config, **kwargs):
        payload["added"] = self.processor_config.get("value", "default")
        return payload


class UpperCaseProcessor(MCPPayloadProcessor):
    """Test processor that uppercases a field."""

    async def pre_call(self, payload, context, config, **kwargs):
        if "query" in payload:
            payload["query"] = payload["query"].upper()
        return payload

    async def post_call(self, result, payload, context, config, **kwargs):
        return result.upper()


class ErrorProcessor(MCPPayloadProcessor):
    """Test processor that raises an error."""

    async def pre_call(self, payload, context, config, **kwargs):
        raise ValueError("pre_call error")


@pytest.fixture
def mock_context():
    ctx = Mock()
    ctx.request_metadata = {}
    ctx.iteration = 1
    return ctx


@pytest.fixture
def mock_config():
    return Mock()


class TestMCPPayloadProcessorChain:
    """Tests for processor chain execution."""

    @pytest.mark.asyncio
    async def test_pre_call_runs_in_order(self, mock_context, mock_config):
        p1 = AddFieldProcessor({"value": "from_p1"})
        p2 = UpperCaseProcessor()
        chain = MCPPayloadProcessorChain([p1, p2])

        payload = {"query": "hello"}
        result = await chain.run_pre_call(payload, mock_context, mock_config)

        assert result["added"] == "from_p1"
        assert result["query"] == "HELLO"

    @pytest.mark.asyncio
    async def test_post_call_runs_in_reverse_order(self, mock_context, mock_config):
        p1 = UpperCaseProcessor()
        p2 = AddFieldProcessor()  # post_call is default pass-through
        chain = MCPPayloadProcessorChain([p1, p2])

        result = await chain.run_post_call("hello", {}, mock_context, mock_config)
        # p2.post_call (pass-through) runs first, then p1.post_call (UPPER)
        assert result == "HELLO"

    @pytest.mark.asyncio
    async def test_empty_chain_is_passthrough(self, mock_context, mock_config):
        chain = MCPPayloadProcessorChain([])
        payload = {"query": "test"}
        result = await chain.run_pre_call(payload, mock_context, mock_config)
        assert result == {"query": "test"}

        result_str = await chain.run_post_call("result", {}, mock_context, mock_config)
        assert result_str == "result"

    @pytest.mark.asyncio
    async def test_processor_error_propagates(self, mock_context, mock_config):
        chain = MCPPayloadProcessorChain([ErrorProcessor()])
        with pytest.raises(ValueError, match="pre_call error"):
            await chain.run_pre_call({}, mock_context, mock_config)


class TestProcessorRegistry:
    """Tests for processor auto-registration."""

    def test_builtin_processors_registered(self):
        # Import to trigger registration
        from sgr_agent_core.processors import AuthContextProcessor, TraceContextProcessor  # noqa: F401

        assert ProcessorRegistry.get("TraceContextProcessor") is not None
        assert ProcessorRegistry.get("AuthContextProcessor") is not None

    def test_test_processors_registered(self):
        # AddFieldProcessor and UpperCaseProcessor defined above should be registered
        assert ProcessorRegistry.get("AddFieldProcessor") is not None
        assert ProcessorRegistry.get("UpperCaseProcessor") is not None

    def test_get_nonexistent_returns_none(self):
        assert ProcessorRegistry.get("NonexistentProcessor") is None


class TestPayloadProcessorDefinition:
    """Tests for config model."""

    def test_from_dict(self):
        defn = PayloadProcessorDefinition.model_validate(
            {"class": "TraceContextProcessor", "config": {"default_trace_id": "t1"}, "managed_fields": ["traceId"]}
        )
        assert defn.class_name == "TraceContextProcessor"
        assert defn.config == {"default_trace_id": "t1"}
        assert defn.managed_fields == ["traceId"]

    def test_defaults(self):
        defn = PayloadProcessorDefinition.model_validate({"class": "MyProcessor"})
        assert defn.class_name == "MyProcessor"
        assert defn.config == {}
        assert defn.managed_fields == []
