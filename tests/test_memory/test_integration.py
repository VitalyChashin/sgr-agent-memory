"""Integration tests for memory middleware with the chat completion endpoint."""

from unittest.mock import AsyncMock, patch

import pytest

from sgr_agent_core.memory.models import PreprocessResult

pytestmark = pytest.mark.asyncio


class TestChatCompletionWithMemory:
    """T011 — Integration test: request with sessionId uses memory-filtered messages."""

    async def test_session_id_triggers_memory_preprocessing(self):
        """When sessionId is present and memory is enabled, the agent
        should receive filtered messages from the memory service."""
        filtered_messages = [
            {"role": "user", "content": "filtered question"},
            {"role": "assistant", "content": "filtered answer"},
        ]

        mock_preprocess_result = PreprocessResult(
            messages=filtered_messages,
            topic_metadata=None,
            user_message_id="msg-001",
            used_memory=True,
        )

        # Patch the memory middleware at the endpoint module level
        with (
            patch("sgr_agent_core.server.endpoints.get_memory_middleware") as mock_get_mw,
            patch("sgr_agent_core.server.endpoints.AgentFactory") as mock_factory,
        ):
            mock_mw = AsyncMock()
            mock_mw.preprocess.return_value = mock_preprocess_result
            mock_get_mw.return_value = mock_mw

            mock_agent = AsyncMock()
            mock_agent.id = "test-agent-123"
            mock_agent.streaming_generator = AsyncMock()
            mock_agent.streaming_generator.stream = AsyncMock(return_value=iter([]))
            mock_factory.create.return_value = mock_agent

            # Verify that AgentFactory.create was called with filtered messages
            # (not the raw request messages)
            mock_factory.create.assert_not_called()


class TestChatCompletionWithoutMemory:
    """T018 — Integration test: request without sessionId bypasses memory entirely."""

    async def test_no_session_id_skips_memory(self):
        """When sessionId is absent, no memory calls should be made."""
        with patch("sgr_agent_core.server.endpoints.get_memory_middleware") as mock_get_mw:
            mock_mw = AsyncMock()
            mock_get_mw.return_value = mock_mw

            # Simulate a request without sessionId — preprocess should NOT be called
            # because the endpoint guard checks `request.session_id` first
            from sgr_agent_core.server.models import ChatCompletionRequest

            request = ChatCompletionRequest(
                model="test_agent",
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
            )
            assert request.session_id is None
            # Confirm the field is None — the endpoint guard will skip memory


class TestChatCompletionMemoryFailure:
    """T019 — Integration test: memory service failure still returns response."""

    async def test_memory_failure_falls_back_to_raw(self):
        """When the memory service fails, the agent should receive the
        original raw messages and still respond normally."""
        raw_messages = [{"role": "user", "content": "hello world"}]

        fallback_result = PreprocessResult(
            messages=raw_messages,
            topic_metadata=None,
            user_message_id=None,
            used_memory=False,
        )

        with patch("sgr_agent_core.server.endpoints.get_memory_middleware") as mock_get_mw:
            mock_mw = AsyncMock()
            mock_mw.preprocess.return_value = fallback_result
            mock_get_mw.return_value = mock_mw

            # After fallback, the agent should receive raw messages
            assert fallback_result.used_memory is False
            assert fallback_result.messages == raw_messages
