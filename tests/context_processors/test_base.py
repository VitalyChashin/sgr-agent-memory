"""Unit tests for the Agent Context Processor base: ABC, registry, chain, builder."""

import pytest

from sgr_agent_core.context_processors.base import (
    AgentContextProcessor,
    AgentContextProcessorChain,
    AgentContextProcessorRegistry,
    ContextProcessorDefinition,
    FinishDecision,
    build_context_processor_chain,
    emit_event_span,
)
from tests.context_processors.conftest import StubSystemTool, StubWorkTool


class DropEverythingProcessor(AgentContextProcessor):
    """Returns every toolkit name as a drop candidate."""

    async def on_prepare_tools(self, *, toolkit, context, config, **kw):
        return {t.tool_name for t in toolkit}


class ThrowingProcessor(AgentContextProcessor):
    """Raises in every hook to exercise fail-safety."""

    async def on_prepare_tools(self, *, toolkit, context, config, **kw):
        raise ValueError("boom prepare")

    async def on_before_finish(self, *, context, config, **kw):
        raise ValueError("boom finish")


class VetoOnceProcessor(AgentContextProcessor):
    async def on_before_finish(self, *, context, config, **kw):
        return FinishDecision(force_continue=True, inject_messages=[{"role": "user", "content": "again"}])


class TestRegistry:
    def test_builtins_registered(self):
        assert AgentContextProcessorRegistry.get("RepeatedToolCallGuard") is not None
        assert AgentContextProcessorRegistry.get("MandatoryToolCallProcessor") is not None

    def test_local_processors_registered(self):
        assert AgentContextProcessorRegistry.get("DropEverythingProcessor") is not None

    def test_get_nonexistent_returns_none(self):
        assert AgentContextProcessorRegistry.get("NoSuchProcessor") is None


class TestBuilder:
    def test_empty_returns_none(self):
        cfg = type("C", (), {"context_processors": []})()
        assert build_context_processor_chain(cfg) is None

    def test_missing_attr_returns_none(self):
        assert build_context_processor_chain(object()) is None

    def test_resolve_by_registry_name(self):
        cfg = type("C", (), {"context_processors": [{"class": "MandatoryToolCallProcessor", "config": {}}]})()
        chain = build_context_processor_chain(cfg)
        assert chain is not None
        assert len(chain.processors) == 1
        assert type(chain.processors[0]).__name__ == "MandatoryToolCallProcessor"

    def test_resolve_by_dotted_import(self):
        dotted = "sgr_agent_core.context_processors.repeated_tool_call_guard.RepeatedToolCallGuard"
        cfg = type("C", (), {"context_processors": [{"class": dotted, "config": {"max_repeats": 5}}]})()
        chain = build_context_processor_chain(cfg)
        assert chain is not None
        assert chain.processors[0].max_repeats == 5

    def test_unknown_class_skipped(self):
        cfg = type("C", (), {"context_processors": [{"class": "TotallyUnknownProcessor"}]})()
        assert build_context_processor_chain(cfg) is None


class TestDefinition:
    def test_alias_and_defaults(self):
        defn = ContextProcessorDefinition.model_validate({"class": "Foo"})
        assert defn.class_name == "Foo"
        assert defn.config == {}

    def test_with_config(self):
        defn = ContextProcessorDefinition.model_validate({"class": "Foo", "config": {"a": 1}})
        assert defn.config == {"a": 1}


class TestChainPrepareTools:
    @pytest.mark.asyncio
    async def test_system_tools_never_dropped(self, mock_context, mock_config):
        chain = AgentContextProcessorChain([DropEverythingProcessor()])
        toolkit = [StubWorkTool, StubSystemTool]
        result = await chain.run_prepare_tools(toolkit, mock_context, mock_config)
        assert StubWorkTool.tool_name in result.drop
        assert StubSystemTool.tool_name not in result.drop

    @pytest.mark.asyncio
    async def test_throwing_processor_is_fail_safe(self, mock_context, mock_config):
        chain = AgentContextProcessorChain([ThrowingProcessor(), DropEverythingProcessor()])
        toolkit = [StubWorkTool]
        # ThrowingProcessor contributes nothing; DropEverythingProcessor still drops.
        result = await chain.run_prepare_tools(toolkit, mock_context, mock_config)
        assert result.drop == {StubWorkTool.tool_name}

    @pytest.mark.asyncio
    async def test_legacy_set_return_still_supported(self, mock_context, mock_config):
        # DropEverythingProcessor returns a bare set[str]; the chain coerces it.
        chain = AgentContextProcessorChain([DropEverythingProcessor()])
        result = await chain.run_prepare_tools([StubWorkTool], mock_context, mock_config)
        assert result.drop == {StubWorkTool.tool_name}
        assert result.inject_messages == []

    @pytest.mark.asyncio
    async def test_inject_messages_flow_through(self, mock_context, mock_config):
        from sgr_agent_core.context_processors.base import PrepareToolsResult

        class InjectProcessor(AgentContextProcessor):
            async def on_prepare_tools(self, *, toolkit, context, config, **kw):
                return PrepareToolsResult(
                    drop={StubWorkTool.tool_name},
                    inject_messages=[{"role": "user", "content": "disabled"}],
                )

        chain = AgentContextProcessorChain([InjectProcessor()])
        result = await chain.run_prepare_tools([StubWorkTool], mock_context, mock_config)
        assert result.drop == {StubWorkTool.tool_name}
        assert result.inject_messages == [{"role": "user", "content": "disabled"}]


class TestChainBeforeFinish:
    @pytest.mark.asyncio
    async def test_veto_merges(self, mock_context, mock_config):
        chain = AgentContextProcessorChain([VetoOnceProcessor()])
        decision = await chain.run_before_finish(mock_context, mock_config)
        assert decision.force_continue is True
        assert decision.inject_messages == [{"role": "user", "content": "again"}]

    @pytest.mark.asyncio
    async def test_throwing_finish_allows_finish(self, mock_context, mock_config):
        chain = AgentContextProcessorChain([ThrowingProcessor()])
        decision = await chain.run_before_finish(mock_context, mock_config)
        assert decision.force_continue is False

    @pytest.mark.asyncio
    async def test_no_processors_allows_finish(self, mock_context, mock_config):
        chain = AgentContextProcessorChain([])
        decision = await chain.run_before_finish(mock_context, mock_config)
        assert decision.force_continue is False


class TestEmitEventSpan:
    def test_none_provider_is_noop(self):
        emit_event_span(None, None, "x", {})  # must not raise

    def test_records_span(self, fake_provider):
        emit_event_span(fake_provider, None, "ctx.test", {"k": "v"})
        assert len(fake_provider.spans) == 1
        assert fake_provider.spans[0]["name"] == "ctx.test"
