"""Unit tests for MandatoryToolCallProcessor."""

import pytest

from sgr_agent_core.context_processors.mandatory_tool_call import MandatoryToolCallProcessor
from tests.context_processors.conftest import StubSystemTool, StubWorkTool


class TestVeto:
    @pytest.mark.asyncio
    async def test_veto_when_no_work_calls(self, mock_context, mock_config):
        proc = MandatoryToolCallProcessor({"min_tool_calls": 1, "max_retries": 2})
        decision = await proc.on_before_finish(context=mock_context, config=mock_config)
        assert decision is not None
        assert decision.force_continue is True
        assert decision.inject_messages[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_allows_finish_after_work_call(self, mock_context, mock_config):
        proc = MandatoryToolCallProcessor({"min_tool_calls": 1, "max_retries": 2})
        await proc.on_tool_end(tool=StubWorkTool(query="x"), result="ok", context=mock_context, config=mock_config)
        decision = await proc.on_before_finish(context=mock_context, config=mock_config)
        assert decision is None

    @pytest.mark.asyncio
    async def test_system_tools_do_not_satisfy_min(self, mock_context, mock_config):
        proc = MandatoryToolCallProcessor({"min_tool_calls": 1, "max_retries": 2})
        await proc.on_tool_end(tool=StubSystemTool(payload="x"), result="ok", context=mock_context, config=mock_config)
        decision = await proc.on_before_finish(context=mock_context, config=mock_config)
        assert decision is not None
        assert decision.force_continue is True

    @pytest.mark.asyncio
    async def test_safety_valve_allows_finish_after_max_retries(self, mock_context, mock_config):
        proc = MandatoryToolCallProcessor({"min_tool_calls": 1, "max_retries": 2})
        # Two vetoes, then allow finish to avoid an infinite loop.
        assert (await proc.on_before_finish(context=mock_context, config=mock_config)).force_continue is True
        assert (await proc.on_before_finish(context=mock_context, config=mock_config)).force_continue is True
        assert await proc.on_before_finish(context=mock_context, config=mock_config) is None


class TestEmission:
    @pytest.mark.asyncio
    async def test_span_emitted_only_on_veto(self, mock_context, mock_config, fake_provider):
        proc = MandatoryToolCallProcessor({"min_tool_calls": 1, "max_retries": 1})
        # Veto → one span.
        await proc.on_before_finish(context=mock_context, config=mock_config, provider=fake_provider)
        assert len(fake_provider.spans) == 1
        assert fake_provider.spans[0]["name"] == "ctx-processor.mandatory_tool_call.vetoed"
        # Safety valve (no veto) → no new span.
        await proc.on_before_finish(context=mock_context, config=mock_config, provider=fake_provider)
        assert len(fake_provider.spans) == 1
