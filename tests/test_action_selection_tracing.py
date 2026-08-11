"""Tests for action-selection CoT tracing in ToolCallingAgent.

Covers the reasoning/usage extraction helpers on BaseAgent and the structured
generation output built by ToolCallingAgent._select_action_phase (A+B+C from
plans/action-selection-cot-tracing.md).
"""

from types import SimpleNamespace
from typing import ClassVar

import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk, ChoiceDelta
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from openai.types.completion_usage import CompletionTokensDetails, CompletionUsage, PromptTokensDetails
from pydantic import Field

from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig
from sgr_agent_core.agents import ToolCallingAgent
from sgr_agent_core.base_agent import BaseAgent
from sgr_agent_core.base_tool import BaseTool
from tests.test_agent_e2e import _create_tool_call


class StubTool(BaseTool):
    tool_name: ClassVar[str] = "stub_tool"
    query: str = Field(default="q")

    async def __call__(self, context, config, **kw) -> str:
        return "ok"


# --------------------------------------------------------------------------- #
# _extract_reasoning
# --------------------------------------------------------------------------- #


class TestExtractReasoning:
    def test_reads_reasoning_content_attribute(self):
        obj = SimpleNamespace(reasoning_content="thinking...", reasoning=None)
        assert BaseAgent._extract_reasoning(obj) == "thinking..."

    def test_reads_reasoning_attribute(self):
        obj = SimpleNamespace(reasoning="plan")
        assert BaseAgent._extract_reasoning(obj) == "plan"

    def test_reads_from_model_extra(self):
        # Non-standard field only present in pydantic model_extra (no direct attribute).
        class FakeDelta:
            def __init__(self) -> None:
                self.model_extra = {"reasoning_content": "from-extra"}

        assert BaseAgent._extract_reasoning(FakeDelta()) == "from-extra"

    def test_none_input_returns_none(self):
        assert BaseAgent._extract_reasoning(None) is None

    def test_plain_object_returns_none(self):
        assert BaseAgent._extract_reasoning(SimpleNamespace(content="hi")) is None

    def test_empty_reasoning_is_skipped(self):
        # Empty string must not be returned as a (falsy) reasoning value.
        assert BaseAgent._extract_reasoning(SimpleNamespace(reasoning_content="", reasoning=None)) is None


# --------------------------------------------------------------------------- #
# _extract_usage
# --------------------------------------------------------------------------- #


class TestExtractUsage:
    def test_none_returns_none(self):
        assert BaseAgent._extract_usage(None) is None

    def test_flat_keys_preserved(self):
        usage = CompletionUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        out = BaseAgent._extract_usage(usage)
        assert out["input"] == 100
        assert out["output"] == 50
        assert out["total"] == 150
        # No details objects → no breakdown keys.
        assert "reasoning" not in out
        assert "cached" not in out

    def test_breakdown_captured_when_present(self):
        usage = CompletionUsage(
            prompt_tokens=200,
            completion_tokens=500,
            total_tokens=700,
            completion_tokens_details=CompletionTokensDetails(reasoning_tokens=400),
            prompt_tokens_details=PromptTokensDetails(cached_tokens=80),
        )
        out = BaseAgent._extract_usage(usage)
        assert out["reasoning"] == 400
        assert out["cached"] == 80

    def test_missing_detail_fields_omitted(self):
        # details object present but its token field is None → key omitted, no raise.
        usage = CompletionUsage(
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            completion_tokens_details=CompletionTokensDetails(),
        )
        out = BaseAgent._extract_usage(usage)
        assert "reasoning" not in out


# --------------------------------------------------------------------------- #
# _select_action_phase structured output
# --------------------------------------------------------------------------- #


class _ReasoningStream:
    """Stream mock that yields reasoning-bearing chunks then a final completion."""

    def __init__(self, chunks: list[ChatCompletionChunk], final: ChatCompletion):
        self._events = [SimpleNamespace(type="chunk", chunk=c) for c in chunks]
        self._final = final
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._events):
            raise StopAsyncIteration
        ev = self._events[self._i]
        self._i += 1
        return ev

    async def get_final_completion(self) -> ChatCompletion:
        return self._final


def _reasoning_chunk(text: str) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="c",
        created=0,
        model="m",
        object="chat.completion.chunk",
        choices=[ChunkChoice(index=0, delta=ChoiceDelta(content=None, reasoning_content=text))],
    )


def _final_completion(tool: BaseTool, usage: CompletionUsage | None, content: str | None = None) -> ChatCompletion:
    message = ChatCompletionMessage(
        role="assistant",
        content=content,
        tool_calls=[_create_tool_call(tool, "call-id")],
    )
    return ChatCompletion(
        id="id",
        choices=[Choice(index=0, message=message, finish_reason="stop")],
        created=0,
        model="m",
        object="chat.completion",
        usage=usage,
    )


def _make_agent(client: AsyncOpenAI) -> ToolCallingAgent:
    config = AgentConfig(
        llm=LLMConfig(api_key="k", base_url="https://api.openai.com/v1", model="gpt-4o-mini"),
        prompts=PromptsConfig(system_prompt_str="p", initial_user_request_str="p", clarification_response_str="p"),
        execution=ExecutionConfig(max_iterations=5, max_clarifications=1),
    )
    return ToolCallingAgent(
        task_messages=[{"role": "user", "content": "go"}],
        openai_client=client,
        agent_config=config,
        toolkit=[StubTool],
    )


class _StreamClient:
    """Minimal stand-in exposing chat.completions.stream(...)."""

    def __init__(self, stream):
        self.chat = SimpleNamespace(completions=SimpleNamespace(stream=lambda **kw: stream))


class TestSelectActionTracing:
    @pytest.mark.asyncio
    async def test_structured_output_with_reasoning_and_usage(self):
        tool = StubTool(query="x")
        usage = CompletionUsage(
            prompt_tokens=100,
            completion_tokens=500,
            total_tokens=600,
            completion_tokens_details=CompletionTokensDetails(reasoning_tokens=420),
        )
        stream = _ReasoningStream(
            [_reasoning_chunk("step one "), _reasoning_chunk("step two")],
            _final_completion(tool, usage, content="visible text"),
        )
        agent = _make_agent(_StreamClient(stream))

        selected = await agent._select_action_phase()

        assert selected.tool_name == StubTool.tool_name
        call = agent._last_llm_call
        assert call["name"] == "action-selection"
        # B: reasoning captured from the streamed deltas.
        assert call["output"]["reasoning"] == "step one step two"
        # C: content and the chosen tool recorded separately (not collapsed).
        assert call["output"]["content"] == "visible text"
        assert call["output"]["tool_call"]["name"] == StubTool.tool_name
        # A: usage breakdown surfaced.
        assert call["usage"]["input"] == 100
        assert call["usage"]["output"] == 500
        assert call["usage"]["reasoning"] == 420

    @pytest.mark.asyncio
    async def test_reasoning_truncated_to_cap(self):
        tool = StubTool(query="x")
        long_text = "z" * (BaseAgent._REASONING_TRACE_MAX_LEN + 500)
        stream = _ReasoningStream([_reasoning_chunk(long_text)], _final_completion(tool, None))
        agent = _make_agent(_StreamClient(stream))

        await agent._select_action_phase()

        assert len(agent._last_llm_call["output"]["reasoning"]) == BaseAgent._REASONING_TRACE_MAX_LEN

    @pytest.mark.asyncio
    async def test_no_reasoning_path_omits_key(self):
        # Non-reasoning model: no reasoning deltas, no reasoning on the message.
        tool = StubTool(query="x")
        stream = _ReasoningStream([], _final_completion(tool, None, content="answer"))
        agent = _make_agent(_StreamClient(stream))

        await agent._select_action_phase()

        output = agent._last_llm_call["output"]
        assert "reasoning" not in output
        assert output["content"] == "answer"
        assert output["tool_call"]["name"] == StubTool.tool_name
        # usage absent on the completion → usage is None, handled gracefully.
        assert agent._last_llm_call["usage"] is None
