"""Tests for MemoryMiddleware preprocess/postprocess."""

from unittest.mock import AsyncMock

import httpx
import pytest

from sgr_agent_core.memory.config import MemoryConfig
from sgr_agent_core.memory.middleware import MemoryMiddleware
from sgr_agent_core.memory.models import ContextMessage, GetContextResponse, StoreMessageResponse

pytestmark = pytest.mark.asyncio


def _make_config(enabled: bool = True) -> MemoryConfig:
    return MemoryConfig(enabled=enabled, service_url="http://localhost:9100", timeout=0.3)


def _make_context_response(**overrides) -> GetContextResponse:
    defaults = {
        "messages": [
            ContextMessage(
                message_id="msg-001",
                role="user",
                content="Tell me about AI",
                topic_id="topic-001",
                timestamp="2026-03-31T10:00:00Z",
            ),
            ContextMessage(
                message_id="msg-002",
                role="assistant",
                content="AI is transforming industries...",
                topic_id="topic-001",
                timestamp="2026-03-31T10:00:05Z",
            ),
        ],
        "current_topic_id": "topic-001",
        "current_topic_label": "AI trends",
        "topic_shift": False,
        "user_message_id": "msg-003",
    }
    defaults.update(overrides)
    return GetContextResponse(**defaults)


# ---------------------------------------------------------------------------
# US1: Preprocess success path
# ---------------------------------------------------------------------------


class TestPreprocessSuccess:
    """T010 — MemoryMiddleware.preprocess() success path."""

    async def test_returns_filtered_messages_on_success(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.get_context.return_value = _make_context_response()

        mw = MemoryMiddleware(config=config, client=mock_client)
        raw_messages = [
            {"role": "user", "content": "Tell me about AI"},
            {"role": "assistant", "content": "old response"},
            {"role": "user", "content": "Now tell me about climate"},
        ]

        result = await mw.preprocess(messages=raw_messages, session_id="sess-1")

        assert result.used_memory is True
        assert result.user_message_id == "msg-003"
        # Should return the filtered messages from memory service, not raw
        assert len(result.messages) == 2
        assert result.messages[0]["content"] == "Tell me about AI"
        assert result.messages[1]["content"] == "AI is transforming industries..."

    async def test_topic_metadata_populated_on_success(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.get_context.return_value = _make_context_response(topic_shift=True)

        mw = MemoryMiddleware(config=config, client=mock_client)
        result = await mw.preprocess(
            messages=[{"role": "user", "content": "climate policy"}],
            session_id="sess-1",
        )

        assert result.topic_metadata is not None
        assert result.topic_metadata.topic_id == "topic-001"
        assert result.topic_metadata.topic_label == "AI trends"
        assert result.topic_metadata.topic_shift is True

    async def test_passes_user_id_to_client(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.get_context.return_value = _make_context_response()

        mw = MemoryMiddleware(config=config, client=mock_client)
        await mw.preprocess(
            messages=[{"role": "user", "content": "hello"}],
            session_id="sess-1",
            user_id="user-42",
        )

        mock_client.get_context.assert_called_once_with(
            session_id="sess-1",
            content="hello",
            user_id="user-42",
            max_messages=config.max_messages,
        )

    async def test_no_user_messages_returns_raw(self):
        """Edge case: all messages are system/assistant — no user content to send."""
        config = _make_config(enabled=True)
        mock_client = AsyncMock()

        mw = MemoryMiddleware(config=config, client=mock_client)
        raw = [{"role": "system", "content": "You are helpful"}]
        result = await mw.preprocess(messages=raw, session_id="sess-1")

        assert result.used_memory is False
        assert result.messages == raw
        mock_client.get_context.assert_not_called()

    async def test_multipart_content_format(self):
        """Edge case: user message with list-of-parts content."""
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.get_context.return_value = _make_context_response()

        mw = MemoryMiddleware(config=config, client=mock_client)
        raw = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this image"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        result = await mw.preprocess(messages=raw, session_id="sess-1")

        assert result.used_memory is True
        mock_client.get_context.assert_called_once()
        # Verify the extracted content was the text part
        call_args = mock_client.get_context.call_args
        assert call_args.kwargs["content"] == "describe this image"


# ---------------------------------------------------------------------------
# US2: Preprocess fallback paths
# ---------------------------------------------------------------------------


class TestPreprocessFallback:
    """T017 — MemoryMiddleware.preprocess() fallback paths."""

    async def test_memory_disabled_returns_raw_messages(self):
        config = _make_config(enabled=False)
        mock_client = AsyncMock()
        raw = [{"role": "user", "content": "hi"}]

        mw = MemoryMiddleware(config=config, client=mock_client)
        result = await mw.preprocess(messages=raw, session_id="sess-1")

        assert result.used_memory is False
        assert result.messages == raw
        mock_client.get_context.assert_not_called()

    async def test_no_session_id_returns_raw_messages(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        raw = [{"role": "user", "content": "hi"}]

        mw = MemoryMiddleware(config=config, client=mock_client)
        result = await mw.preprocess(messages=raw, session_id=None)

        assert result.used_memory is False
        assert result.messages == raw
        mock_client.get_context.assert_not_called()

    async def test_timeout_returns_raw_messages(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.get_context.side_effect = httpx.ReadTimeout("timeout")
        raw = [{"role": "user", "content": "hi"}]

        mw = MemoryMiddleware(config=config, client=mock_client)
        result = await mw.preprocess(messages=raw, session_id="sess-1")

        assert result.used_memory is False
        assert result.messages == raw

    async def test_connect_error_returns_raw_messages(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.get_context.side_effect = httpx.ConnectError("refused")
        raw = [{"role": "user", "content": "hi"}]

        mw = MemoryMiddleware(config=config, client=mock_client)
        result = await mw.preprocess(messages=raw, session_id="sess-1")

        assert result.used_memory is False
        assert result.messages == raw

    async def test_http_error_returns_raw_messages(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        resp = httpx.Response(500, request=httpx.Request("POST", "http://x/context"))
        mock_client.get_context.side_effect = httpx.HTTPStatusError("500", request=resp.request, response=resp)
        raw = [{"role": "user", "content": "hi"}]

        mw = MemoryMiddleware(config=config, client=mock_client)
        result = await mw.preprocess(messages=raw, session_id="sess-1")

        assert result.used_memory is False
        assert result.messages == raw

    async def test_unexpected_error_returns_raw_messages(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.get_context.side_effect = RuntimeError("unexpected")
        raw = [{"role": "user", "content": "hi"}]

        mw = MemoryMiddleware(config=config, client=mock_client)
        result = await mw.preprocess(messages=raw, session_id="sess-1")

        assert result.used_memory is False
        assert result.messages == raw


# ---------------------------------------------------------------------------
# US3: Postprocess
# ---------------------------------------------------------------------------


class TestPostprocess:
    """T024-T026 — MemoryMiddleware.postprocess()."""

    async def test_stores_assistant_response(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.store_message.return_value = StoreMessageResponse(
            message_id="msg-004", topic_id="topic-001", topic_label="AI trends", topic_shift=False
        )

        mw = MemoryMiddleware(config=config, client=mock_client)
        await mw.postprocess(
            session_id="sess-1",
            user_message_id="msg-003",
            assistant_content="Here is the answer.",
            user_id="user-42",
        )

        mock_client.store_message.assert_called_once_with(
            session_id="sess-1",
            role="assistant",
            content="Here is the answer.",
            parent_message_id="msg-003",
            user_id="user-42",
        )

    async def test_skips_when_no_user_message_id(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()

        mw = MemoryMiddleware(config=config, client=mock_client)
        await mw.postprocess(
            session_id="sess-1",
            user_message_id="",
            assistant_content="answer",
        )

        mock_client.store_message.assert_not_called()

    async def test_skips_when_empty_content(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()

        mw = MemoryMiddleware(config=config, client=mock_client)
        await mw.postprocess(
            session_id="sess-1",
            user_message_id="msg-001",
            assistant_content="",
        )

        mock_client.store_message.assert_not_called()

    async def test_error_does_not_propagate(self):
        config = _make_config(enabled=True)
        mock_client = AsyncMock()
        mock_client.store_message.side_effect = RuntimeError("store failed")

        mw = MemoryMiddleware(config=config, client=mock_client)
        # Should NOT raise
        await mw.postprocess(
            session_id="sess-1",
            user_message_id="msg-001",
            assistant_content="answer",
        )
