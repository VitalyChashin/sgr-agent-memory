"""Tests for the `reasoning` tool argument (BaseTool.reasoning).

Elicits CoT from models with no native reasoning channel: the field is advertised
in every tool's schema, captured into the action-selection trace, and stripped
from MCP payloads (it is not a server argument). See notes/tool-argument-reasoning.md.
"""

from types import SimpleNamespace
from typing import ClassVar

import pytest
from jambo import SchemaConverter
from openai import pydantic_function_tool
from pydantic import Field, create_model

from sgr_agent_core.base_tool import BaseTool, MCPBaseTool
from sgr_agent_core.tools import FinalAnswerTool
from tests.test_action_selection_tracing import (
    StubTool,
    _final_completion,
    _make_agent,
    _ReasoningStream,
    _StreamClient,
)


class _NoReasoningTool(BaseTool):
    """Tool that does not redeclare reasoning."""

    tool_name: ClassVar[str] = "no_reasoning_tool"
    city: str = Field(default="Paris")

    async def __call__(self, context, config, **kw) -> str:
        return "ok"


class TestSchemaExposure:
    def test_reasoning_is_first_property(self):
        # Order matters: the model must write its thinking before the arguments
        # that thinking is supposed to justify.
        params = pydantic_function_tool(_NoReasoningTool, name="t")["function"]["parameters"]
        assert list(params["properties"])[0] == "reasoning"

    def test_reasoning_is_required_under_strict(self):
        # Defaulted to "" so code-constructed tools still validate, but OpenAI's
        # strict schema marks every property required, so the model always emits it.
        params = pydantic_function_tool(_NoReasoningTool, name="t")["function"]["parameters"]
        assert "reasoning" in params["required"]

    def test_subclass_description_wins(self):
        assert FinalAnswerTool.model_fields["reasoning"].description == (
            "Why task is now complete and how answer was verified"
        )

    def test_default_allows_construction_without_reasoning(self):
        assert _NoReasoningTool(city="Rome").reasoning == ""


def _mcp_tool(properties: dict, name: str = "search"):
    """Build a tool the way MCP2ToolConverter.build_tools_from_mcp does."""
    schema = {"title": "T", "type": "object", "properties": properties, "required": list(properties)}
    PdModel = SchemaConverter.build(schema)
    ToolCls = create_model(f"MCP{name}", __base__=(PdModel, MCPBaseTool), __doc__="d")
    ToolCls.tool_name = name
    ToolCls._declares_reasoning = "reasoning" in properties
    return ToolCls


class _CapturingClient:
    """Async-context-manager stand-in recording the payload sent to the server."""

    def __init__(self) -> None:
        self.payload = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call_tool(self, name, payload):
        self.payload = payload
        return SimpleNamespace(content=[SimpleNamespace(model_dump_json=lambda: '{"ok":true}')])


class TestMCPPayload:
    @pytest.mark.asyncio
    async def test_reasoning_stripped_from_payload(self):
        ToolCls = _mcp_tool({"query": {"type": "string"}})
        client = _CapturingClient()
        ToolCls._client = client

        await ToolCls(query="q", reasoning="because I need it")(SimpleNamespace(error_tags=set()), None)

        assert client.payload == {"query": "q"}

    @pytest.mark.asyncio
    async def test_server_declared_reasoning_is_preserved(self):
        # A server whose own schema takes a `reasoning` parameter must still receive it.
        ToolCls = _mcp_tool({"reasoning": {"type": "string"}}, name="think")
        client = _CapturingClient()
        ToolCls._client = client

        await ToolCls(reasoning="a real argument")(SimpleNamespace(error_tags=set()), None)

        assert client.payload == {"reasoning": "a real argument"}


class TestTraceFallback:
    @pytest.mark.asyncio
    async def test_tool_reasoning_used_when_provider_sends_none(self):
        # Qwen-on-vLLM-behind-LiteLLM shape: no reasoning deltas, no reasoning on the
        # message — the CoT arrives as a tool argument instead.
        tool = StubTool(query="x", reasoning="I should look this up first")
        stream = _ReasoningStream([], _final_completion(tool, None, content=None))
        agent = _make_agent(_StreamClient(stream))

        await agent._select_action_phase()

        assert agent._last_llm_call["output"]["reasoning"] == "I should look this up first"

    @pytest.mark.asyncio
    async def test_native_reasoning_still_wins(self):
        from tests.test_action_selection_tracing import _reasoning_chunk

        tool = StubTool(query="x", reasoning="argument cot")
        stream = _ReasoningStream([_reasoning_chunk("native cot")], _final_completion(tool, None))
        agent = _make_agent(_StreamClient(stream))

        await agent._select_action_phase()

        assert agent._last_llm_call["output"]["reasoning"] == "native cot"
