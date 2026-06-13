"""Integration tests for rolling memory — context injection, response fields, fail-open."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sgr_agent_core.memory.config import MemoryConfig, RollingSummaryConfig
from sgr_agent_core.memory.rolling_summary import SUMMARY_MESSAGE_PREFIX
from sgr_agent_core.models import AgentStatesEnum


def _make_mock_openai_response(content: str):
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = content
    return mock


def _make_agent_config(*, rolling_summary_enabled=True, max_tokens=200):
    from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig

    return AgentConfig(
        llm=LLMConfig(api_key="test-key", model="gpt-4o-mini"),
        prompts=PromptsConfig(
            system_prompt_str="Test", initial_user_request_str="Test", clarification_response_str="Test"
        ),
        execution=ExecutionConfig(max_iterations=1, logs_dir=None),
        memory=MemoryConfig(
            rolling_summary=RollingSummaryConfig(enabled=rolling_summary_enabled, max_tokens_to_summarize=max_tokens)
        ),
    )


def _create_instant_agent(agent_config, mock_client, task_messages):
    from sgr_agent_core.base_agent import BaseAgent

    class _InstantAgent(BaseAgent):
        name = "_instant_agent"
        _captured_task_messages = None

        async def _reasoning_phase(self, **kwargs):
            # Capture what the agent actually sees
            _InstantAgent._captured_task_messages = list(self.task_messages)
            return None

        async def _select_action_phase(self, reasoning, **kwargs):
            return None

        async def _action_phase(self, tool, **kwargs):
            self._context.state = AgentStatesEnum.COMPLETED
            self._context.execution_result = "Done"
            return "Done"

    return _InstantAgent(
        task_messages=task_messages,
        openai_client=mock_client,
        agent_config=agent_config,
        toolkit=[],
    )


async def _execute_agent(agent):
    """Execute agent with mocked observability."""
    collected = []
    original_add = agent.streaming_generator.queue.put_nowait

    def _capture(data):
        collected.append(data)
        original_add(data)

    agent.streaming_generator.queue.put_nowait = _capture

    with patch("sgr_agent_core.observability.get_provider") as mock_prov_fn:
        mock_prov = MagicMock()
        mock_prov.start_trace.return_value = MagicMock()
        mock_prov.start_span.return_value = MagicMock()
        mock_prov_fn.return_value = mock_prov
        result = await agent.execute()

    return result, collected


# ---------------------------------------------------------------------------
# T011: Agent receives compacted context
# ---------------------------------------------------------------------------


class TestContextInjection:
    @pytest.mark.asyncio
    async def test_agent_sees_summary_plus_recent_window(self):
        """Long conversation → agent receives [system] + [summary] + [recent] only."""
        mock_client = AsyncMock()
        summary_text = "The user discussed vector databases."
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_openai_response(summary_text))

        # Build a conversation that exceeds budget (budget=200 tokens, ~10 short msgs)
        task_messages = [{"role": "system", "content": "Be helpful"}]
        for i in range(20):
            role = "user" if i % 2 == 0 else "assistant"
            task_messages.append({"role": role, "content": f"Message number {i} with some padding text here"})

        agent_config = _make_agent_config(rolling_summary_enabled=True, max_tokens=200)
        agent = _create_instant_agent(agent_config, mock_client, task_messages)

        result, collected = await _execute_agent(agent)

        # The agent should have seen compacted messages

        captured = agent.__class__._captured_task_messages
        assert captured is not None

        # Should have system msg(s) + summary msg + recent window (fewer than 20 non-system msgs)
        non_system_original = [m for m in task_messages if m["role"] != "system"]
        non_system_captured = [m for m in captured if not m.get("content", "").startswith(SUMMARY_MESSAGE_PREFIX)]
        non_system_captured = [m for m in non_system_captured if m["role"] != "system"]
        assert len(non_system_captured) < len(non_system_original), "Agent should see fewer messages after compaction"

        # Should contain a summary message
        summary_msgs = [m for m in captured if SUMMARY_MESSAGE_PREFIX in m.get("content", "")]
        assert len(summary_msgs) == 1
        assert summary_text in summary_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_short_conversation_no_summarization(self):
        """Conversation within budget → no summarization, all messages passed through."""
        mock_client = AsyncMock()

        task_messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        agent_config = _make_agent_config(rolling_summary_enabled=True, max_tokens=10000)
        agent = _create_instant_agent(agent_config, mock_client, task_messages)

        result, collected = await _execute_agent(agent)

        # No summarization call should have been made
        mock_client.chat.completions.create.assert_not_called()

        # Context should have all original messages
        assert agent._context.conversation_summary is None
        assert agent._context.recent_messages is not None
        assert len(agent._context.recent_messages) == 2  # user + assistant (no system)


# ---------------------------------------------------------------------------
# T014/T015: Response fields
# ---------------------------------------------------------------------------


class TestResponseFields:
    @pytest.mark.asyncio
    async def test_metadata_event_has_both_fields(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_openai_response("Summary of history."))

        task_messages = [{"role": "system", "content": "Sys"}]
        for i in range(20):
            task_messages.append(
                {
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": f"Message number {i} with substantial padding content to ensure token count " * 3,
                }
            )

        agent_config = _make_agent_config(rolling_summary_enabled=True, max_tokens=200)
        agent = _create_instant_agent(agent_config, mock_client, task_messages)

        _, collected = await _execute_agent(agent)

        metadata_events = [e for e in collected if isinstance(e, str) and e.startswith("event: metadata")]
        assert len(metadata_events) == 1

        data_line = metadata_events[0].split("\n")[1]
        payload = json.loads(data_line[6:])
        assert "conversationSummary" in payload
        assert "recentMessages" in payload
        assert isinstance(payload["recentMessages"], list)

    @pytest.mark.asyncio
    async def test_short_conversation_metadata_has_null_summary(self):
        mock_client = AsyncMock()
        task_messages = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "Hello"},
        ]
        agent_config = _make_agent_config(rolling_summary_enabled=True, max_tokens=10000)
        agent = _create_instant_agent(agent_config, mock_client, task_messages)

        _, collected = await _execute_agent(agent)

        # Should have metadata with recentMessages but no conversationSummary (it's None)
        metadata_events = [e for e in collected if isinstance(e, str) and e.startswith("event: metadata")]
        assert len(metadata_events) == 1
        payload = json.loads(metadata_events[0].split("\n")[1][6:])
        assert "conversationSummary" not in payload  # None is excluded
        assert "recentMessages" in payload


# ---------------------------------------------------------------------------
# T018: Disabled agent
# ---------------------------------------------------------------------------


class TestDisabledAgent:
    @pytest.mark.asyncio
    async def test_no_metadata_no_calls_when_disabled(self):
        mock_client = AsyncMock()
        task_messages = [{"role": "user", "content": "Hello"}]
        agent_config = _make_agent_config(rolling_summary_enabled=False)
        agent = _create_instant_agent(agent_config, mock_client, task_messages)

        original_msgs = list(agent.task_messages)
        _, collected = await _execute_agent(agent)

        # task_messages unchanged
        assert agent.task_messages == original_msgs
        # No metadata event
        metadata_events = [e for e in collected if isinstance(e, str) and "event: metadata" in e]
        assert len(metadata_events) == 0
        # No summarization call
        mock_client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# T020: Per-agent config
# ---------------------------------------------------------------------------


class TestPerAgentConfig:
    @pytest.mark.asyncio
    async def test_enabled_agent_compacts_disabled_does_not(self):
        mock_client_a = AsyncMock()
        mock_client_a.chat.completions.create = AsyncMock(return_value=_make_mock_openai_response("Summary"))
        mock_client_b = AsyncMock()

        msgs = [{"role": "user", "content": f"Msg {i} with padding" * 5} for i in range(20)]

        agent_a = _create_instant_agent(
            _make_agent_config(rolling_summary_enabled=True, max_tokens=200), mock_client_a, list(msgs)
        )
        agent_b = _create_instant_agent(_make_agent_config(rolling_summary_enabled=False), mock_client_b, list(msgs))

        await _execute_agent(agent_a)
        await _execute_agent(agent_b)

        assert agent_a._context.conversation_summary is not None
        assert agent_b._context.conversation_summary is None
        mock_client_b.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# T023: Fail-open fallback
# ---------------------------------------------------------------------------


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_falls_back_to_full_messages_on_failure(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=asyncio.TimeoutError)

        msgs = [
            {
                "role": "user",
                "content": f"Message number {i} with substantial padding content to ensure token count " * 3 * 5,
            }
            for i in range(20)
        ]
        agent_config = _make_agent_config(rolling_summary_enabled=True, max_tokens=200)
        agent = _create_instant_agent(agent_config, mock_client, list(msgs))

        result, collected = await _execute_agent(agent)

        # Agent should complete successfully
        assert result == "Done"
        # Context should NOT have summary (failed)
        assert agent._context.conversation_summary is None
        # No metadata event
        metadata_events = [e for e in collected if isinstance(e, str) and "event: metadata" in e]
        assert len(metadata_events) == 0


# ---------------------------------------------------------------------------
# T024: Independence from topic-aware memory
# ---------------------------------------------------------------------------


class TestIndependence:
    @pytest.mark.asyncio
    async def test_works_with_memory_disabled(self):
        """rolling_summary.enabled=true + memory.enabled=false → works."""
        from sgr_agent_core.agent_definition import AgentConfig, ExecutionConfig, LLMConfig, PromptsConfig

        agent_config = AgentConfig(
            llm=LLMConfig(api_key="test-key", model="gpt-4o-mini"),
            prompts=PromptsConfig(system_prompt_str="T", initial_user_request_str="T", clarification_response_str="T"),
            execution=ExecutionConfig(max_iterations=1, logs_dir=None),
            memory=MemoryConfig(
                enabled=False,  # Topic-aware memory OFF
                rolling_summary=RollingSummaryConfig(enabled=True, max_tokens_to_summarize=200),
            ),
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_make_mock_openai_response("Summary text"))

        msgs = [{"role": "user", "content": f"Msg {i} padding" * 5} for i in range(20)]
        agent = _create_instant_agent(agent_config, mock_client, msgs)

        result, _ = await _execute_agent(agent)
        assert result == "Done"
        assert agent._context.conversation_summary is not None
