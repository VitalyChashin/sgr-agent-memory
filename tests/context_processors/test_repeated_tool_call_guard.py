"""Unit tests for RepeatedToolCallGuard."""

import pytest

from sgr_agent_core.context_processors.repeated_tool_call_guard import RepeatedToolCallGuard
from tests.context_processors.conftest import StubFailingTool, StubSystemTool, StubWorkTool


async def _feed(guard, tool, context, config, n):
    for _ in range(n):
        await guard.on_tool_end(tool=tool, result=await tool(context, config), context=context, config=config)


class TestCounting:
    @pytest.mark.asyncio
    async def test_exact_args_counts_identical_calls(self, mock_context, mock_config):
        guard = RepeatedToolCallGuard({"max_repeats": 3, "scope": "exact_args"})
        tool = StubWorkTool(query="same")
        await _feed(guard, tool, mock_context, mock_config, 3)
        drop = await guard.on_prepare_tools(toolkit=[StubWorkTool], context=mock_context, config=mock_config)
        assert drop == {StubWorkTool.tool_name}

    @pytest.mark.asyncio
    async def test_exact_args_distinguishes_args(self, mock_context, mock_config):
        guard = RepeatedToolCallGuard({"max_repeats": 3, "scope": "exact_args"})
        for i in range(3):
            tool = StubWorkTool(query=f"q{i}")
            await guard.on_tool_end(tool=tool, result="ok", context=mock_context, config=mock_config)
        # Three distinct arg-sets, each counted once → nothing crosses the threshold.
        drop = await guard.on_prepare_tools(toolkit=[StubWorkTool], context=mock_context, config=mock_config)
        assert drop == set()

    @pytest.mark.asyncio
    async def test_tool_name_scope_counts_regardless_of_args(self, mock_context, mock_config):
        guard = RepeatedToolCallGuard({"max_repeats": 3, "scope": "tool_name"})
        for i in range(3):
            tool = StubWorkTool(query=f"q{i}")
            await guard.on_tool_end(tool=tool, result="ok", context=mock_context, config=mock_config)
        drop = await guard.on_prepare_tools(toolkit=[StubWorkTool], context=mock_context, config=mock_config)
        assert drop == {StubWorkTool.tool_name}

    @pytest.mark.asyncio
    async def test_below_threshold_not_dropped(self, mock_context, mock_config):
        guard = RepeatedToolCallGuard({"max_repeats": 3})
        tool = StubWorkTool(query="x")
        await _feed(guard, tool, mock_context, mock_config, 2)
        drop = await guard.on_prepare_tools(toolkit=[StubWorkTool], context=mock_context, config=mock_config)
        assert drop == set()


class TestFailedOnly:
    @pytest.mark.asyncio
    async def test_failed_only_ignores_successes(self, mock_context, mock_config):
        guard = RepeatedToolCallGuard({"max_repeats": 2, "scope": "tool_name", "failed_only": True})
        tool = StubWorkTool(query="x")
        await guard.on_tool_end(tool=tool, result="ok", context=mock_context, config=mock_config)
        await guard.on_tool_end(tool=tool, result="ok", context=mock_context, config=mock_config)
        drop = await guard.on_prepare_tools(toolkit=[StubWorkTool], context=mock_context, config=mock_config)
        assert drop == set()

    @pytest.mark.asyncio
    async def test_failed_only_counts_errors(self, mock_context, mock_config):
        guard = RepeatedToolCallGuard({"max_repeats": 2, "scope": "tool_name", "failed_only": True})
        tool = StubFailingTool(query="x")
        await _feed(guard, tool, mock_context, mock_config, 2)
        drop = await guard.on_prepare_tools(toolkit=[StubFailingTool], context=mock_context, config=mock_config)
        assert drop == {StubFailingTool.tool_name}


class TestSystemTools:
    @pytest.mark.asyncio
    async def test_system_tools_never_counted(self, mock_context, mock_config):
        guard = RepeatedToolCallGuard({"max_repeats": 1, "scope": "tool_name"})
        tool = StubSystemTool(payload="x")
        await _feed(guard, tool, mock_context, mock_config, 5)
        drop = await guard.on_prepare_tools(toolkit=[StubSystemTool], context=mock_context, config=mock_config)
        assert drop == set()


class TestEmission:
    @pytest.mark.asyncio
    async def test_span_emitted_only_when_dropping(self, mock_context, mock_config, fake_provider):
        guard = RepeatedToolCallGuard({"max_repeats": 2, "scope": "tool_name"})
        tool = StubWorkTool(query="x")

        # Below threshold → no emission.
        await guard.on_tool_end(tool=tool, result="ok", context=mock_context, config=mock_config)
        await guard.on_prepare_tools(
            toolkit=[StubWorkTool], context=mock_context, config=mock_config, provider=fake_provider
        )
        assert fake_provider.spans == []

        # Cross threshold → exactly one span.
        await guard.on_tool_end(tool=tool, result="ok", context=mock_context, config=mock_config)
        await guard.on_prepare_tools(
            toolkit=[StubWorkTool], context=mock_context, config=mock_config, provider=fake_provider
        )
        assert len(fake_provider.spans) == 1
        assert fake_provider.spans[0]["name"] == "ctx-processor.repeated_tool_call_guard.dropped"
        assert fake_provider.spans[0]["metadata"]["tool"] == StubWorkTool.tool_name

    @pytest.mark.asyncio
    async def test_noop_provider_runs_clean(self, mock_context, mock_config):
        from sgr_agent_core.observability.noop import NoOpProvider

        guard = RepeatedToolCallGuard({"max_repeats": 1, "scope": "tool_name"})
        tool = StubWorkTool(query="x")
        await guard.on_tool_end(tool=tool, result="ok", context=mock_context, config=mock_config)
        drop = await guard.on_prepare_tools(
            toolkit=[StubWorkTool], context=mock_context, config=mock_config, provider=NoOpProvider()
        )
        assert drop == {StubWorkTool.tool_name}
