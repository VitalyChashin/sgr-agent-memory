"""Unit tests for built-in MCP payload processors."""

from unittest.mock import Mock

import pytest

from sgr_agent_core.processors.auth_context import AuthContextProcessor
from sgr_agent_core.processors.trace_context import TraceContextProcessor


@pytest.fixture
def mock_context():
    ctx = Mock()
    ctx.request_metadata = {}
    ctx.iteration = 3
    return ctx


@pytest.fixture
def mock_config():
    return Mock()


class TestTraceContextProcessor:
    """Tests for TraceContextProcessor."""

    @pytest.mark.asyncio
    async def test_injects_from_request_metadata(self, mock_context, mock_config):
        mock_context.request_metadata = {"traceId": "req-trace-123"}
        processor = TraceContextProcessor()
        payload = {"query": "test", "traceId": "llm-generated"}

        result = await processor.pre_call(payload, mock_context, mock_config)
        assert result["traceId"] == "req-trace-123"

    @pytest.mark.asyncio
    async def test_injects_from_config_default(self, mock_context, mock_config):
        processor = TraceContextProcessor({"default_trace_id": "config-trace-456"})
        payload = {"query": "test", "traceId": "llm-generated"}

        result = await processor.pre_call(payload, mock_context, mock_config)
        assert result["traceId"] == "config-trace-456"

    @pytest.mark.asyncio
    async def test_generates_fallback(self, mock_context, mock_config):
        processor = TraceContextProcessor()
        payload = {"query": "test"}

        result = await processor.pre_call(payload, mock_context, mock_config)
        assert result["traceId"].startswith("trace-")

    @pytest.mark.asyncio
    async def test_overwrites_llm_value(self, mock_context, mock_config):
        mock_context.request_metadata = {"traceId": "correct-trace"}
        processor = TraceContextProcessor()
        payload = {"query": "test", "traceId": "wrong-llm-value"}

        result = await processor.pre_call(payload, mock_context, mock_config)
        assert result["traceId"] == "correct-trace"

    @pytest.mark.asyncio
    async def test_metadata_takes_priority_over_config(self, mock_context, mock_config):
        mock_context.request_metadata = {"traceId": "metadata-trace"}
        processor = TraceContextProcessor({"default_trace_id": "config-trace"})
        payload = {}

        result = await processor.pre_call(payload, mock_context, mock_config)
        assert result["traceId"] == "metadata-trace"


class TestAuthContextProcessor:
    """Tests for AuthContextProcessor."""

    @pytest.mark.asyncio
    async def test_injects_from_request_metadata(self, mock_context, mock_config):
        mock_context.request_metadata = {"userId": "user-42"}
        processor = AuthContextProcessor()
        payload = {"query": "test"}

        result = await processor.pre_call(payload, mock_context, mock_config)
        assert result["userId"] == "user-42"

    @pytest.mark.asyncio
    async def test_injects_from_config_default(self, mock_context, mock_config):
        processor = AuthContextProcessor({"default_user_id": "config-user"})
        payload = {"query": "test"}

        result = await processor.pre_call(payload, mock_context, mock_config)
        assert result["userId"] == "config-user"

    @pytest.mark.asyncio
    async def test_no_source_skips_injection(self, mock_context, mock_config):
        processor = AuthContextProcessor()
        payload = {"query": "test", "userId": "llm-value"}

        result = await processor.pre_call(payload, mock_context, mock_config)
        # No metadata or config default — userId left unchanged (LLM value preserved)
        assert result["userId"] == "llm-value"

    @pytest.mark.asyncio
    async def test_overwrites_llm_value(self, mock_context, mock_config):
        mock_context.request_metadata = {"userId": "real-user"}
        processor = AuthContextProcessor()
        payload = {"userId": "llm-guessed-user"}

        result = await processor.pre_call(payload, mock_context, mock_config)
        assert result["userId"] == "real-user"
