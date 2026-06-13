"""Unit tests for RollingSummaryBuffer — split, summarize, compact."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from sgr_agent_core.memory.config import RollingSummaryConfig
from sgr_agent_core.memory.rolling_summary import SUMMARY_MESSAGE_PREFIX, RollingSummaryBuffer


def _make_buffer(max_tokens: int = 2000, model: str | None = None, timeout: float = 10.0):
    cfg = RollingSummaryConfig(
        enabled=True, max_tokens_to_summarize=max_tokens, summarization_model=model, summarization_timeout_s=timeout
    )
    mock_client = AsyncMock()
    buf = RollingSummaryBuffer(cfg, mock_client, agent_model="gpt-4o-mini")
    return buf, mock_client


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


# ---------------------------------------------------------------------------
# T008: split_conversation
# ---------------------------------------------------------------------------


class TestSplitConversation:
    def test_all_within_budget(self):
        buf, _ = _make_buffer(max_tokens=10000)
        msgs = [_msg("system", "Be helpful"), _msg("user", "Hi"), _msg("assistant", "Hello")]
        sys_msgs, older, recent = buf.split_conversation(msgs)
        assert len(sys_msgs) == 1
        assert sys_msgs[0]["role"] == "system"
        assert len(older) == 0
        assert len(recent) == 2  # user + assistant

    def test_split_when_over_budget(self):
        buf, _ = _make_buffer(max_tokens=100)
        msgs = [
            _msg("user", "First message " * 50),
            _msg("assistant", "Second message " * 50),
            _msg("user", "Short recent"),
        ]
        sys_msgs, older, recent = buf.split_conversation(msgs)
        assert len(sys_msgs) == 0
        assert len(older) > 0
        assert len(recent) > 0
        assert any("Short recent" in m["content"] for m in recent)

    def test_system_messages_separated(self):
        buf, _ = _make_buffer(max_tokens=10000)
        msgs = [_msg("system", "Sys1"), _msg("system", "Sys2"), _msg("user", "Hi")]
        sys_msgs, older, recent = buf.split_conversation(msgs)
        assert len(sys_msgs) == 2
        assert all(m["role"] == "system" for m in sys_msgs)
        assert len(recent) == 1

    def test_single_oversized_message_truncated(self):
        buf, _ = _make_buffer(max_tokens=100)
        msgs = [_msg("user", "word " * 500)]
        sys_msgs, older, recent = buf.split_conversation(msgs)
        assert len(recent) == 1
        assert recent[0]["content"].endswith("[truncated]")
        assert len(older) == 0

    def test_empty_messages(self):
        buf, _ = _make_buffer()
        sys_msgs, older, recent = buf.split_conversation([])
        assert sys_msgs == []
        assert older == []
        assert recent == []

    def test_only_system_messages(self):
        buf, _ = _make_buffer()
        msgs = [_msg("system", "Be helpful")]
        sys_msgs, older, recent = buf.split_conversation(msgs)
        assert len(sys_msgs) == 1
        assert older == []
        assert recent == []

    def test_multipart_content(self):
        buf, _ = _make_buffer(max_tokens=10000)
        msgs = [{"role": "user", "content": [{"type": "text", "text": "Hello world"}]}]
        sys_msgs, older, recent = buf.split_conversation(msgs)
        assert len(recent) == 1


# ---------------------------------------------------------------------------
# T009: compact_messages
# ---------------------------------------------------------------------------


class TestCompactMessages:
    def test_output_structure(self):
        sys_msgs = [_msg("system", "Be helpful")]
        recent = [_msg("user", "Recent question")]
        result = RollingSummaryBuffer.compact_messages(sys_msgs, "Summary of older turns.", recent)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "Be helpful"
        assert result[1]["role"] == "system"
        assert result[1]["content"].startswith(SUMMARY_MESSAGE_PREFIX)
        assert "Summary of older turns." in result[1]["content"]
        assert result[2]["role"] == "user"

    def test_summary_has_system_role(self):
        result = RollingSummaryBuffer.compact_messages([], "Test summary", [_msg("user", "Q")])
        summary_msg = result[0]
        assert summary_msg["role"] == "system"

    def test_preserves_multiple_system_messages(self):
        sys_msgs = [_msg("system", "A"), _msg("system", "B")]
        result = RollingSummaryBuffer.compact_messages(sys_msgs, "Sum", [_msg("user", "Q")])
        assert len(result) == 4  # 2 system + 1 summary + 1 user


# ---------------------------------------------------------------------------
# T010: summarize() happy path and fail-open
# ---------------------------------------------------------------------------


class TestSummarize:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        buf, mock_client = _make_buffer(model="gpt-4.1-nano")
        expected = "The user asked about databases."
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = expected
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await buf.summarize([_msg("user", "Compare databases")])
        assert result == expected
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4.1-nano"
        assert call_kwargs["temperature"] == 0.2

    @pytest.mark.asyncio
    async def test_uses_agent_model_when_none(self):
        buf, mock_client = _make_buffer(model=None)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Summary"
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        await buf.summarize([_msg("user", "Hello")])
        assert mock_client.chat.completions.create.call_args.kwargs["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_empty_history_returns_none(self):
        buf, mock_client = _make_buffer()
        result = await buf.summarize([])
        assert result is None
        mock_client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        buf, mock_client = _make_buffer(timeout=0.01)
        mock_client.chat.completions.create = AsyncMock(side_effect=asyncio.TimeoutError)
        result = await buf.summarize([_msg("user", "Hello")])
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_none(self):
        buf, mock_client = _make_buffer()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        result = await buf.summarize([_msg("user", "Hello")])
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_content_returns_none(self):
        buf, mock_client = _make_buffer()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await buf.summarize([_msg("user", "Hello")])
        assert result is None
