"""Tests for tool definitions captured in Langfuse generation input.

Covers ``BaseAgent._build_gen_input`` — the single chokepoint that shapes the
generation ``input`` so Langfuse renders an Available-tools section.
"""

from unittest.mock import Mock

from openai import AsyncOpenAI, pydantic_function_tool
from pydantic import Field

from sgr_agent_core.agent_config import GlobalConfig
from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig
from sgr_agent_core.agents import ToolCallingAgent
from sgr_agent_core.tools import BaseTool


class _SampleTool(BaseTool):
    """Look something up by query."""

    tool_name = "sample_tool"
    query: str = Field(description="the search query")


def _make_agent() -> ToolCallingAgent:
    cfg = AgentConfig(
        llm=LLMConfig(api_key="k", base_url="https://api.openai.com/v1"),
        prompts=PromptsConfig(
            system_prompt_str="p",
            initial_user_request_str="p",
            clarification_response_str="p",
        ),
        execution=ExecutionConfig(),
    )
    return ToolCallingAgent(
        task_messages=[{"role": "user", "content": "hi"}],
        openai_client=Mock(spec=AsyncOpenAI),
        agent_config=cfg,
        toolkit=[_SampleTool],
    )


def _sample_tool_defs(n: int = 1) -> list[dict]:
    one = dict(pydantic_function_tool(_SampleTool, name="sample_tool"))
    return [dict(one) for _ in range(n)]


def test_tools_embedded_as_request_object(monkeypatch):
    """With capture on, input becomes {messages, tools, tool_choice} and tools
    keep the full {"type": "function", "function": {...}} shape Langfuse needs."""
    monkeypatch.setattr(GlobalConfig().observability, "capture_tool_definitions", True)
    agent = _make_agent()
    messages = [{"role": "user", "content": "hi"}]
    tool_defs = _sample_tool_defs()

    result = agent._build_gen_input(messages, tool_defs)

    assert isinstance(result, dict)
    assert result["messages"] is messages
    assert result["tool_choice"] == "required"
    assert "_tools_truncated" not in result

    fn = result["tools"][0]["function"]
    assert fn["name"] == "sample_tool"
    assert "look something up" in fn["description"].lower()
    # full schema preserved so the LLM-visible parameters render too
    assert "query" in fn["parameters"]["properties"]


def test_reasoning_tool_def_is_captured(monkeypatch):
    """The single ReasoningTool def offered in the reasoning seam is captured."""
    from sgr_agent_core.tools import ReasoningTool

    monkeypatch.setattr(GlobalConfig().observability, "capture_tool_definitions", True)
    agent = _make_agent()
    messages = [{"role": "user", "content": "hi"}]
    tool_defs = [dict(pydantic_function_tool(ReasoningTool, name=ReasoningTool.tool_name))]

    result = agent._build_gen_input(messages, tool_defs)

    names = [t["function"]["name"] for t in result["tools"]]
    assert ReasoningTool.tool_name in names


def test_capture_disabled_returns_bare_messages(monkeypatch):
    """With capture off, input is the unchanged bare messages list."""
    monkeypatch.setattr(GlobalConfig().observability, "capture_tool_definitions", False)
    agent = _make_agent()
    messages = [{"role": "user", "content": "hi"}]

    result = agent._build_gen_input(messages, _sample_tool_defs())

    assert result is messages


def test_no_tools_returns_bare_messages(monkeypatch):
    """Empty/None tool defs (e.g. structured-output path) leave input untouched."""
    monkeypatch.setattr(GlobalConfig().observability, "capture_tool_definitions", True)
    agent = _make_agent()
    messages = [{"role": "user", "content": "hi"}]

    assert agent._build_gen_input(messages, []) is messages
    assert agent._build_gen_input(messages, None) is messages


def test_large_toolset_degrades_to_compact(monkeypatch):
    """Beyond the cap, tools degrade to compact name+description and flag it."""
    monkeypatch.setattr(GlobalConfig().observability, "capture_tool_definitions", True)
    agent = _make_agent()
    messages = [{"role": "user", "content": "hi"}]
    tool_defs = _sample_tool_defs(agent._MAX_TRACED_TOOL_DEFS + 1)

    result = agent._build_gen_input(messages, tool_defs)

    assert result["_tools_truncated"] is True
    assert len(result["tools"]) == len(tool_defs)
    first = result["tools"][0]
    assert set(first.keys()) == {"name", "description"}
    assert first["name"] == "sample_tool"
