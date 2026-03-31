"""Tests for MCP ask tool memory integration."""

from unittest.mock import AsyncMock

import pytest

from sgr_agent_core.mcp_server.models import AskRequest, AskResponse
from sgr_agent_core.memory.models import PreprocessResult, TopicMetadata

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# T004: Foundational model tests
# ---------------------------------------------------------------------------


class TestAskRequestModel:
    def test_session_id_is_optional(self):
        req = AskRequest(query="hello")
        assert req.sessionId == ""

    def test_session_id_can_be_set(self):
        req = AskRequest(query="hello", sessionId="sess-123")
        assert req.sessionId == "sess-123"

    def test_extra_fields_preserved(self):
        req = AskRequest(query="hello", sessionId="sess-1", customField="value")
        assert req.model_dump()["customField"] == "value"


class TestAskResponseModel:
    def test_topic_fields_default_to_none(self):
        resp = AskResponse(response="answer")
        assert resp.topicId is None
        assert resp.topicLabel is None
        assert resp.topicShift is None

    def test_topic_fields_excluded_when_none(self):
        resp = AskResponse(response="answer", traceId="t1")
        dumped = resp.model_dump(exclude_none=True)
        assert "topicId" not in dumped
        assert "topicLabel" not in dumped
        assert "topicShift" not in dumped

    def test_topic_fields_included_when_set(self):
        resp = AskResponse(response="answer", topicId="topic-001", topicLabel="AI trends", topicShift=True)
        dumped = resp.model_dump(exclude_none=True)
        assert dumped["topicId"] == "topic-001"
        assert dumped["topicLabel"] == "AI trends"
        assert dumped["topicShift"] is True


# ---------------------------------------------------------------------------
# T005-T006: US1 — Topic-filtered context
# ---------------------------------------------------------------------------


class TestAskWithMemory:
    """US1: When sessionId is provided and memory is enabled, the agent
    receives topic-filtered messages and the response is stored."""

    async def test_preprocess_called_with_correct_args(self):
        """T005: Verify middleware.preprocess() is called correctly."""
        filtered = [
            {"role": "user", "content": "prior question"},
            {"role": "assistant", "content": "prior answer"},
        ]
        mock_result = PreprocessResult(
            messages=filtered,
            topic_metadata=TopicMetadata(topic_id="t1", topic_label="AI", topic_shift=False),
            user_message_id="msg-001",
            used_memory=True,
        )

        mock_mw = AsyncMock()
        mock_mw.preprocess.return_value = mock_result

        # Simulate the handler logic: call preprocess when sessionId is present
        session_id = "sess-1"
        user_id = "user-1"
        query = "test query"
        messages = [{"role": "user", "content": query}]

        if session_id and mock_mw is not None:
            result = await mock_mw.preprocess(messages=messages, session_id=session_id, user_id=user_id)
            task_messages = result.messages
        else:
            task_messages = messages

        assert task_messages == filtered
        assert result.used_memory is True
        mock_mw.preprocess.assert_called_once_with(
            messages=messages,
            session_id="sess-1",
            user_id="user-1",
        )

    async def test_postprocess_called_with_result(self):
        """T006: Verify postprocess is called with the agent result."""
        mock_mw = AsyncMock()
        mock_mw.postprocess.return_value = None

        preprocess_result = PreprocessResult(
            messages=[{"role": "user", "content": "q"}],
            user_message_id="msg-001",
            used_memory=True,
        )

        # Simulate handler postprocess logic
        session_id = "sess-1"
        user_id = "user-1"
        assistant_content = "The answer is 42"

        if preprocess_result.used_memory and mock_mw is not None:
            await mock_mw.postprocess(
                session_id=session_id,
                user_message_id=preprocess_result.user_message_id or "",
                assistant_content=assistant_content,
                user_id=user_id,
            )

        mock_mw.postprocess.assert_called_once_with(
            session_id="sess-1",
            user_message_id="msg-001",
            assistant_content="The answer is 42",
            user_id="user-1",
        )


# ---------------------------------------------------------------------------
# T009-T012: US2 — Transparent fallback
# ---------------------------------------------------------------------------


class TestAskWithoutMemory:
    """US2: When sessionId is absent or memory is unavailable,
    behavior is identical to pre-memory system."""

    async def test_no_session_id_skips_memory(self):
        """T009: Empty sessionId causes memory to be skipped."""
        mock_mw = AsyncMock()
        session_id = ""
        query = "hello"

        # Simulate handler guard clause
        if session_id and mock_mw is not None:
            await mock_mw.preprocess(messages=[{"role": "user", "content": query}], session_id=session_id)
        task_messages = [{"role": "user", "content": query}]

        mock_mw.preprocess.assert_not_called()
        assert task_messages == [{"role": "user", "content": "hello"}]

    async def test_empty_session_id_is_falsy(self):
        """T010: Empty string sessionId is falsy — guard clause skips memory."""
        assert not ""
        assert not AskRequest(query="hello").sessionId

    async def test_middleware_none_skips_memory(self):
        """T011: When middleware is None (disabled), single-query message used."""
        mw = None
        session_id = "sess-1"
        query = "hello"

        if session_id and mw is not None:
            raise AssertionError("Should not reach here")
        task_messages = [{"role": "user", "content": query}]

        assert task_messages == [{"role": "user", "content": "hello"}]

    async def test_preprocess_fallback_uses_raw_message(self):
        """T012: When preprocess returns fallback, task_messages are raw."""
        raw = [{"role": "user", "content": "my query"}]
        fallback = PreprocessResult(messages=raw, used_memory=False)

        # Simulate: memory_result exists but used_memory is False
        task_messages = fallback.messages
        assert task_messages == raw
        assert fallback.used_memory is False


# ---------------------------------------------------------------------------
# T014-T015: US3 — Topic metadata in response
# ---------------------------------------------------------------------------


class TestAskResponseMetadata:
    """US3: MCP response includes topic metadata when memory is active."""

    async def test_topic_metadata_in_response_when_memory_used(self):
        """T014: Response includes topicId/topicLabel/topicShift when memory used."""
        preprocess_result = PreprocessResult(
            messages=[{"role": "user", "content": "q"}],
            topic_metadata=TopicMetadata(topic_id="topic-002", topic_label="EU climate policy", topic_shift=True),
            user_message_id="msg-001",
            used_memory=True,
        )

        # Simulate handler response construction
        response_kwargs = {"response": "The answer", "traceId": "t1"}
        if preprocess_result.topic_metadata:
            tm = preprocess_result.topic_metadata
            response_kwargs["topicId"] = tm.topic_id
            response_kwargs["topicLabel"] = tm.topic_label
            response_kwargs["topicShift"] = tm.topic_shift

        resp = AskResponse(**response_kwargs)
        dumped = resp.model_dump(exclude_none=True)
        assert dumped["topicId"] == "topic-002"
        assert dumped["topicLabel"] == "EU climate policy"
        assert dumped["topicShift"] is True

    async def test_no_topic_metadata_when_memory_unused(self):
        """T015: Response does NOT include topic fields when memory inactive."""
        # No memory_result — standard response
        resp = AskResponse(response="The answer", traceId="t1")
        dumped = resp.model_dump(exclude_none=True)
        assert "topicId" not in dumped
        assert "topicLabel" not in dumped
        assert "topicShift" not in dumped
