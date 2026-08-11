"""Tests for MCP connection retry (sgr_agent_core.services.retry)."""

import httpx
import pytest
from fastmcp.exceptions import ToolError
from mcp import McpError
from mcp.types import ErrorData

from sgr_agent_core.services.retry import (
    RetryPolicy,
    is_retryable_mcp_error,
    with_mcp_retry,
)


def _mcp_error(msg: str = "connection lost") -> McpError:
    return McpError(ErrorData(code=-32000, message=msg))


class TestIsRetryable:
    @pytest.mark.parametrize(
        "exc",
        [
            ConnectionError("refused"),
            TimeoutError("timed out"),
            OSError("broken pipe"),
            httpx.ConnectError("no route"),
            httpx.ReadTimeout("slow"),
            _mcp_error(),
            RuntimeError("Failed to initialize server session"),
            RuntimeError("Server session was closed unexpectedly"),
        ],
    )
    def test_transient_errors_are_retryable(self, exc):
        assert is_retryable_mcp_error(exc) is True

    @pytest.mark.parametrize(
        "exc",
        [
            ToolError("tool blew up"),
            ValueError("bad args"),
            TypeError("wrong type"),
            RuntimeError("some unrelated runtime error"),
            KeyError("missing"),
        ],
    )
    def test_logic_errors_are_not_retryable(self, exc):
        assert is_retryable_mcp_error(exc) is False


class TestWithMcpRetry:
    pytestmark = pytest.mark.asyncio

    async def test_succeeds_on_second_attempt(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _mcp_error()
            return "ok"

        result = await with_mcp_retry(fn, RetryPolicy(attempts=3, base_delay=0), what="test")
        assert result == "ok"
        assert calls["n"] == 2

    async def test_exhausts_and_reraises(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            raise ConnectionError("still down")

        with pytest.raises(ConnectionError):
            await with_mcp_retry(fn, RetryPolicy(attempts=3, base_delay=0), what="test")
        assert calls["n"] == 3

    async def test_does_not_retry_tool_error(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            raise ToolError("deterministic failure")

        with pytest.raises(ToolError):
            await with_mcp_retry(fn, RetryPolicy(attempts=5, base_delay=0), what="test")
        assert calls["n"] == 1  # failed once, never retried

    async def test_attempts_one_disables_retry(self):
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            raise _mcp_error()

        with pytest.raises(McpError):
            await with_mcp_retry(fn, RetryPolicy(attempts=1, base_delay=0), what="test")
        assert calls["n"] == 1

    async def test_backoff_is_exponential_and_capped(self, monkeypatch):
        delays: list[float] = []

        async def fake_sleep(d):
            delays.append(d)

        monkeypatch.setattr("sgr_agent_core.services.retry.asyncio.sleep", fake_sleep)

        async def fn():
            raise ConnectionError("down")

        policy = RetryPolicy(attempts=5, base_delay=1.0, max_delay=4.0, backoff_factor=2.0)
        with pytest.raises(ConnectionError):
            await with_mcp_retry(fn, policy, what="test")

        # 5 attempts → 4 sleeps; 1, 2, 4, then capped at 4
        assert delays == [1.0, 2.0, 4.0, 4.0]


class _FakeContent:
    def model_dump_json(self) -> str:
        return '{"ok": true}'


class _FakeResult:
    content = [_FakeContent()]


class _FakeClient:
    """Async-context-manager stand-in for fastmcp.Client.

    ``fail_times`` transient ConnectionErrors on call_tool, then success.
    """

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_tool(self, name, payload):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("transient")
        return _FakeResult()


class TestMCPBaseToolCallRetry:
    """Call-time wiring: MCPBaseTool.__call__ retries transient errors, swallows final."""

    pytestmark = pytest.mark.asyncio

    def _make_tool(self, client, monkeypatch):
        from pydantic import create_model

        from sgr_agent_core import MCPBaseTool
        from sgr_agent_core.agent_config import GlobalConfig

        # Fast retries: 3 attempts, no real sleeping.
        GlobalConfig().execution.mcp_retry.attempts = 3
        GlobalConfig().execution.mcp_retry.base_delay = 0

        ToolCls = create_model("MCPFakeTool", __base__=MCPBaseTool)
        ToolCls.tool_name = "fake_tool"
        ToolCls._client = client
        ToolCls._processor_chain = None
        return ToolCls()

    async def test_retries_then_succeeds(self, monkeypatch):
        from sgr_agent_core.models import AgentContext
        from sgr_agent_core.observability.context import mcp_call_errored

        mcp_call_errored.set(False)
        client = _FakeClient(fail_times=1)
        tool = self._make_tool(client, monkeypatch)
        ctx = AgentContext()

        result = await tool(ctx, None)

        assert "ok" in result
        assert not result.startswith("Error:")
        assert client.calls == 2
        assert "error:mcp_tool" not in ctx.error_tags

    async def test_swallows_after_exhaustion(self, monkeypatch):
        from sgr_agent_core.models import AgentContext
        from sgr_agent_core.observability.context import mcp_call_errored

        mcp_call_errored.set(False)
        client = _FakeClient(fail_times=99)  # never recovers
        tool = self._make_tool(client, monkeypatch)
        ctx = AgentContext()

        result = await tool(ctx, None)

        assert result.startswith("Error:")
        assert client.calls == 3  # exhausted all attempts
        assert "error:mcp_tool" in ctx.error_tags
        assert mcp_call_errored.get() is True
