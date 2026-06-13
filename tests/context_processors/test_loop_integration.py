"""Loop integration tests: context processors driving a real ToolCallingAgent loop."""

from typing import ClassVar
from unittest.mock import Mock

import pytest
from openai import AsyncOpenAI
from pydantic import Field

from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig
from sgr_agent_core.agents import ToolCallingAgent
from sgr_agent_core.base_tool import BaseTool
from sgr_agent_core.models import AgentStatesEnum
from sgr_agent_core.tools import FinalAnswerTool
from tests.test_agent_e2e import MockStream, _create_tool_call


class LeafSearchTool(BaseTool):
    """Work tool that always fails — emulates a stuck leaf-agent retry loop."""

    tool_name: ClassVar[str] = "leaf_search_tool"
    query: str = Field(default="q")

    async def __call__(self, context, config, **kw) -> str:
        return "Error: backend unavailable"


class DelegateTool(BaseTool):
    """Work tool that delegates to a sub-agent (returns a non-terminal result)."""

    tool_name: ClassVar[str] = "delegate_tool"
    task: str = Field(default="t")

    async def __call__(self, context, config, **kw) -> str:
        return "sub-agent result"


def _agent_config(processors: list[dict]) -> AgentConfig:
    return AgentConfig(
        llm=LLMConfig(api_key="test-key", base_url="https://api.openai.com/v1", model="gpt-4o-mini"),
        prompts=PromptsConfig(system_prompt_str="p", initial_user_request_str="p", clarification_response_str="p"),
        execution=ExecutionConfig(max_iterations=10, max_clarifications=3),
        context_processors=processors,
    )


def _tool_names(kwargs) -> set[str]:
    return {t["function"]["name"] for t in kwargs.get("tools", []) if isinstance(t, dict)}


def _stream_for(tool):
    return MockStream(final_completion_data={"content": None, "tool_calls": [_create_tool_call(tool, "call-id")]})


@pytest.mark.asyncio
async def test_repeated_tool_call_guard_breaks_retry_loop():
    """Leaf agent keeps calling a failing tool; the guard drops it so the agent finishes."""
    failing = LeafSearchTool(query="same")
    final = FinalAnswerTool(
        reasoning="forced to finish", completed_steps=["gave up"], answer="done", status=AgentStatesEnum.COMPLETED
    )

    client = Mock(spec=AsyncOpenAI)

    def mock_stream(**kwargs):
        # While the failing tool is available, the model keeps calling it; once dropped, it finishes.
        return _stream_for(failing if LeafSearchTool.tool_name in _tool_names(kwargs) else final)

    client.chat.completions.stream = Mock(side_effect=mock_stream)

    agent = ToolCallingAgent(
        task_messages=[{"role": "user", "content": "go"}],
        openai_client=client,
        agent_config=_agent_config([{"class": "RepeatedToolCallGuard", "config": {"max_repeats": 3}}]),
        toolkit=[LeafSearchTool, FinalAnswerTool],
    )

    result = await agent.execute()

    assert agent._context.state == AgentStatesEnum.COMPLETED
    assert result == "done"
    # Dropped after exactly 3 calls, then one finishing iteration → well under max_iterations.
    assert agent._context.iteration < 10
    failing_calls = [e for e in agent.log if e.get("tool_name") == LeafSearchTool.tool_name]
    assert len(failing_calls) == 3


@pytest.mark.asyncio
async def test_mandatory_tool_call_vetoes_self_answer():
    """Router answers itself; the processor vetoes once and forces a work call first."""
    delegate = DelegateTool(task="search")
    final = FinalAnswerTool(
        reasoning="done", completed_steps=["answered"], answer="final", status=AgentStatesEnum.COMPLETED
    )

    # Scripted responses: finish immediately → (vetoed) → delegate → finish.
    script = [final, delegate, final]
    idx = {"i": 0}

    client = Mock(spec=AsyncOpenAI)

    def mock_stream(**kwargs):
        tool = script[idx["i"]]
        idx["i"] += 1
        return _stream_for(tool)

    client.chat.completions.stream = Mock(side_effect=mock_stream)

    agent = ToolCallingAgent(
        task_messages=[{"role": "user", "content": "route this"}],
        openai_client=client,
        agent_config=_agent_config(
            [{"class": "MandatoryToolCallProcessor", "config": {"min_tool_calls": 1, "max_retries": 2}}]
        ),
        toolkit=[DelegateTool, FinalAnswerTool],
    )

    result = await agent.execute()

    assert agent._context.state == AgentStatesEnum.COMPLETED
    assert result == "final"
    assert agent._context.iteration == 3
    # The corrective message was injected after the first (vetoed) finish.
    injected = [
        m for m in agent.conversation if m.get("role") == "user" and "must call a tool" in str(m.get("content", ""))
    ]
    assert len(injected) == 1
    # The delegate (work) tool ran exactly once.
    delegate_calls = [e for e in agent.log if e.get("tool_name") == DelegateTool.tool_name]
    assert len(delegate_calls) == 1
