"""Unit tests for RedisStorage."""

import pytest

from memory_service.models import Message
from memory_service.storage import RedisStorage

pytestmark = pytest.mark.asyncio


class TestSessionState:
    async def test_init_session(self, storage: RedisStorage):
        state = await storage.init_session("sess-1", "AI trends")
        assert state.current_topic_id == "topic-001"
        assert state.topic_counter == 1
        assert state.topic_labels == {"topic-001": "AI trends"}

    async def test_get_nonexistent_session(self, storage: RedisStorage):
        state = await storage.get_session_state("nonexistent")
        assert state is None

    async def test_advance_topic(self, storage: RedisStorage):
        await storage.init_session("sess-1", "AI trends")
        state = await storage.advance_topic("sess-1", "Climate policy")
        assert state.current_topic_id == "topic-002"
        assert state.topic_counter == 2
        assert state.topic_labels["topic-002"] == "Climate policy"

    async def test_advance_topic_twice(self, storage: RedisStorage):
        await storage.init_session("sess-1", "AI trends")
        await storage.advance_topic("sess-1", "Climate")
        state = await storage.advance_topic("sess-1", "Healthcare")
        assert state.current_topic_id == "topic-003"
        assert state.topic_counter == 3


class TestMessageStorage:
    async def test_store_and_retrieve_message(self, storage: RedisStorage):
        await storage.init_session("sess-1", "AI")
        msg = Message(
            message_id="msg-001",
            session_id="sess-1",
            role="user",
            topic_id="topic-001",
            content="Hello",
            timestamp=1000.0,
        )
        await storage.store_message(msg)
        retrieved = await storage.get_message("msg-001")
        assert retrieved is not None
        assert retrieved.content == "Hello"
        assert retrieved.role == "user"

    async def test_get_topic_messages(self, storage: RedisStorage):
        await storage.init_session("sess-1", "AI")
        for i in range(5):
            msg = Message(
                message_id=f"msg-{i:03d}",
                session_id="sess-1",
                role="user",
                topic_id="topic-001",
                content=f"Message {i}",
                timestamp=1000.0 + i,
            )
            await storage.store_message(msg)

        messages = await storage.get_topic_messages("sess-1", "topic-001")
        assert len(messages) == 5
        assert messages[0].content == "Message 0"
        assert messages[4].content == "Message 4"

    async def test_get_topic_messages_with_limit(self, storage: RedisStorage):
        await storage.init_session("sess-1", "AI")
        for i in range(10):
            msg = Message(
                message_id=f"msg-{i:03d}",
                session_id="sess-1",
                role="user",
                topic_id="topic-001",
                content=f"Message {i}",
                timestamp=1000.0 + i,
            )
            await storage.store_message(msg)

        messages = await storage.get_topic_messages("sess-1", "topic-001", max_messages=3)
        assert len(messages) == 3
        assert messages[0].content == "Message 7"  # last 3

    async def test_messages_isolated_by_topic(self, storage: RedisStorage):
        await storage.init_session("sess-1", "AI")
        msg1 = Message(
            message_id="msg-001",
            session_id="sess-1",
            role="user",
            topic_id="topic-001",
            content="AI msg",
            timestamp=1000.0,
        )
        await storage.store_message(msg1)

        await storage.advance_topic("sess-1", "Climate")
        msg2 = Message(
            message_id="msg-002",
            session_id="sess-1",
            role="user",
            topic_id="topic-002",
            content="Climate msg",
            timestamp=1001.0,
        )
        await storage.store_message(msg2)

        topic1 = await storage.get_topic_messages("sess-1", "topic-001")
        topic2 = await storage.get_topic_messages("sess-1", "topic-002")
        assert len(topic1) == 1
        assert topic1[0].content == "AI msg"
        assert len(topic2) == 1
        assert topic2[0].content == "Climate msg"


class TestHelpers:
    def test_generate_message_id(self):
        mid = RedisStorage.generate_message_id()
        assert mid.startswith("msg-")
        assert len(mid) == 16  # "msg-" + 12 hex chars

    def test_format_timestamp(self):
        ts = RedisStorage.format_timestamp(0.0)
        assert ts == "1970-01-01T00:00:00Z"
