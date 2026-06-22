"""Tests for Deliverable B — span_mode wrapping of context-processor invocations."""

from typing import Any

import pytest

from sgr_agent_core.context_processors.base import (
    AgentContextProcessor,
    AgentContextProcessorChain,
    ContextProcessorDefinition,
    FinishDecision,
    build_context_processor_chain,
    emit_event_span,
)
from tests.context_processors.conftest import StubWorkTool


class RecordingProvider:
    """Records start_span/end_span so tests can assert wrapping spans and their output."""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.ended: list[dict[str, Any]] = []

    def start_span(self, *, name, span_type="span", input=None, metadata=None, _parent=None):
        handle = {"name": name, "span_type": span_type}
        self.started.append(handle)
        return handle

    def end_span(self, handle, *, output=None, status=None, level="DEFAULT"):
        self.ended.append({"name": handle["name"], "output": output, "level": level})


class DropWorkProcessor(AgentContextProcessor):
    async def on_prepare_tools(self, *, toolkit, context, config, **kw):
        return {StubWorkTool.tool_name}


class MarkerProcessor(AgentContextProcessor):
    """Calls emit_event_span with whatever provider the chain hands it (fired-action marker)."""

    async def on_prepare_tools(self, *, toolkit, context, config, provider=None, parent_span=None, **kw):
        emit_event_span(provider, parent_span, "ctx.marker.fired", {"hit": True})
        return set()


class BoomPrepareProcessor(AgentContextProcessor):
    async def on_prepare_tools(self, *, toolkit, context, config, **kw):
        raise ValueError("boom")


def _set_mode(p: AgentContextProcessor, mode: str) -> AgentContextProcessor:
    p._span_mode = mode
    return p


class TestSpanModeAlways:
    @pytest.mark.asyncio
    async def test_wraps_invocation_with_span_and_output(self, mock_context, mock_config):
        provider = RecordingProvider()
        chain = AgentContextProcessorChain([_set_mode(DropWorkProcessor(), "always")])
        await chain.run_prepare_tools([StubWorkTool], mock_context, mock_config, provider=provider)

        assert [s["name"] for s in provider.started] == ["ctx-processor.DropWorkProcessor.on_prepare_tools"]
        assert provider.ended[0]["output"] == {"dropped": [StubWorkTool.tool_name], "injected": 0}
        assert provider.ended[0]["level"] == "DEFAULT"

    @pytest.mark.asyncio
    async def test_throwing_processor_span_is_error_and_fail_safe(self, mock_context, mock_config):
        provider = RecordingProvider()
        chain = AgentContextProcessorChain([_set_mode(BoomPrepareProcessor(), "always")])
        result = await chain.run_prepare_tools([StubWorkTool], mock_context, mock_config, provider=provider)

        assert result.drop == set()  # fail-safe: no drops contributed
        assert provider.ended[0]["level"] == "ERROR"
        assert "error" in provider.ended[0]["output"]

    @pytest.mark.asyncio
    async def test_before_finish_records_force_continue(self, mock_context, mock_config):
        provider = RecordingProvider()

        class Veto(AgentContextProcessor):
            async def on_before_finish(self, *, context, config, **kw):
                return FinishDecision(force_continue=True)

        chain = AgentContextProcessorChain([_set_mode(Veto(), "always")])
        await chain.run_before_finish(mock_context, mock_config, provider=provider)
        assert provider.ended[0]["output"] == {"force_continue": True}


class TestSpanModeFired:
    @pytest.mark.asyncio
    async def test_default_mode_does_not_wrap(self, mock_context, mock_config):
        provider = RecordingProvider()
        chain = AgentContextProcessorChain([DropWorkProcessor()])  # default "fired"
        await chain.run_prepare_tools([StubWorkTool], mock_context, mock_config, provider=provider)
        assert provider.started == []  # no wrapping span

    @pytest.mark.asyncio
    async def test_fired_marker_still_emitted(self, mock_context, mock_config):
        provider = RecordingProvider()
        chain = AgentContextProcessorChain([MarkerProcessor()])  # default "fired"
        await chain.run_prepare_tools([StubWorkTool], mock_context, mock_config, provider=provider)
        # No wrapping span, but the processor's own fired-action marker fires.
        assert [s["name"] for s in provider.started] == ["ctx.marker.fired"]


class TestSpanModeOff:
    @pytest.mark.asyncio
    async def test_off_suppresses_even_fired_markers(self, mock_context, mock_config):
        provider = RecordingProvider()
        chain = AgentContextProcessorChain([_set_mode(MarkerProcessor(), "off")])
        await chain.run_prepare_tools([StubWorkTool], mock_context, mock_config, provider=provider)
        # provider withheld → marker no-ops, no wrapping span.
        assert provider.started == []
        assert provider.ended == []


class TestBuilderSpanMode:
    def test_definition_default_and_explicit(self):
        assert ContextProcessorDefinition.model_validate({"class": "Foo"}).span_mode == "fired"
        assert ContextProcessorDefinition.model_validate({"class": "Foo", "span_mode": "always"}).span_mode == "always"

    def test_builder_propagates_span_mode_to_processor(self):
        cfg = type(
            "C",
            (),
            {"context_processors": [{"class": "MandatoryToolCallProcessor", "config": {}, "span_mode": "always"}]},
        )()
        chain = build_context_processor_chain(cfg)
        assert chain.processors[0]._span_mode == "always"
