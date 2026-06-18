"""Tests for Deliverable A — filterable error/crash traces.

Covers:
- the ``tags`` parameter added to ``end_trace`` (Langfuse + NoOp),
- the failure classifier in the agent loop,
- MCP tool errors marking the run for filtering (``base_tool`` seam).
"""

from unittest.mock import MagicMock

import pytest

from sgr_agent_core.base_agent import _classify_error_tags
from sgr_agent_core.base_tool import MCPBaseTool
from sgr_agent_core.models import AgentContext
from sgr_agent_core.observability.config import LangfuseConfig
from sgr_agent_core.observability.context import mcp_call_errored
from sgr_agent_core.observability.langfuse_provider import LangfuseProvider, LangfuseTraceHandle
from sgr_agent_core.observability.noop import NoOpProvider


class TestClassifyErrorTags:
    def test_max_iterations_is_max_steps(self):
        tags = _classify_error_tags(RuntimeError("Max iterations reached"))
        assert tags == {"error", "error:max_steps"}

    def test_other_runtime_error_is_llm(self):
        assert _classify_error_tags(RuntimeError("connection reset")) == {"error", "error:llm"}

    def test_arbitrary_exception_is_llm(self):
        assert _classify_error_tags(ValueError("boom")) == {"error", "error:llm"}


class TestEndTraceTags:
    def test_langfuse_end_trace_forwards_tags(self):
        """tags are forwarded to trace.update so Langfuse replaces the tag list."""
        provider = LangfuseProvider.__new__(LangfuseProvider)
        provider._config = LangfuseConfig(public_key="pk", secret_key="sk", base_url="http://localhost:3000")
        mock_trace = MagicMock()

        provider.end_trace(
            LangfuseTraceHandle(mock_trace),
            output={"answer": "x"},
            status="completed",
            tags=["SGRAgent", "error", "error:mcp_tool"],
        )

        mock_trace.update.assert_called_once()
        kwargs = mock_trace.update.call_args.kwargs
        assert kwargs["tags"] == ["SGRAgent", "error", "error:mcp_tool"]
        assert kwargs["status_message"] == "completed"

    def test_langfuse_end_trace_omits_tags_when_none(self):
        """No tags param means trace.update is not given a tags kwarg (preserves start-time tags)."""
        provider = LangfuseProvider.__new__(LangfuseProvider)
        provider._config = LangfuseConfig(public_key="pk", secret_key="sk", base_url="http://localhost:3000")
        mock_trace = MagicMock()

        provider.end_trace(LangfuseTraceHandle(mock_trace), output={}, status="cancelled")

        assert "tags" not in mock_trace.update.call_args.kwargs

    def test_noop_end_trace_accepts_tags(self):
        """NoOp end_trace tolerates the tags kwarg and does nothing."""
        NoOpProvider().end_trace(MagicMock(), output={}, status="completed", tags=["error"])


class _FakeFailingClient:
    """Async-context-manager MCP client whose call_tool raises (simulates HTTP 500)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_tool(self, name, payload):
        raise RuntimeError("MCP server returned 500")


class _BoomMCPTool(MCPBaseTool):
    tool_name = "boom_mcp_tool"


class TestMCPToolErrorMarking:
    @pytest.fixture(autouse=True)
    def _reset_flag(self):
        token = mcp_call_errored.set(False)
        yield
        mcp_call_errored.reset(token)

    @pytest.mark.asyncio
    async def test_mcp_error_sets_tags_and_flag_and_returns_error_string(self):
        _BoomMCPTool._client = _FakeFailingClient()
        _BoomMCPTool._processor_chain = None
        context = AgentContext()
        config = MagicMock()

        result = await _BoomMCPTool()(context, config)

        # Loop continues: error is swallowed into the result string.
        assert result.startswith("Error:")
        # Trace becomes filterable.
        assert context.error_tags == {"error", "error:mcp_tool"}
        # Tool span will be marked ERROR by the loop.
        assert mcp_call_errored.get() is True

    @pytest.mark.asyncio
    async def test_no_mcp_error_leaves_context_clean(self):
        class _OkClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def call_tool(self, name, payload):
                return MagicMock(content=[])

        _BoomMCPTool._client = _OkClient()
        _BoomMCPTool._processor_chain = None
        context = AgentContext()

        await _BoomMCPTool()(context, MagicMock())

        assert context.error_tags == set()
        assert mcp_call_errored.get() is False
