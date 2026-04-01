"""Redis storage layer for session state and messages."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from memory_service.models import Message, SessionTopicState

logger = logging.getLogger(__name__)


class RedisStorage:
    """Async Redis storage for conversation messages and topic state."""

    def __init__(self, redis: aioredis.Redis, session_ttl: int = 86400) -> None:
        self._redis = redis
        self._session_ttl = session_ttl

    # ------------------------------------------------------------------
    # Session topic state
    # ------------------------------------------------------------------

    async def get_session_state(self, session_id: str) -> SessionTopicState | None:
        """Retrieve current topic state for a session, or None if not found."""
        pipe = self._redis.pipeline()
        pipe.get(f"session:{session_id}:topic:current")
        pipe.get(f"session:{session_id}:topic:counter")
        pipe.hgetall(f"session:{session_id}:topic:labels")
        current, counter, labels = await pipe.execute()
        if current is None:
            return None
        return SessionTopicState(
            session_id=session_id,
            current_topic_id=current,
            topic_counter=int(counter or 1),
            topic_labels=labels,
        )

    async def set_session_state(self, state: SessionTopicState) -> None:
        """Store session topic state and reset TTL."""
        sid = state.session_id
        pipe = self._redis.pipeline()
        pipe.set(f"session:{sid}:topic:current", state.current_topic_id)
        pipe.set(f"session:{sid}:topic:counter", state.topic_counter)
        for tid, label in state.topic_labels.items():
            pipe.hset(f"session:{sid}:topic:labels", tid, label)
        await pipe.execute()
        await self._reset_session_ttl(sid)

    async def init_session(self, session_id: str, topic_label: str) -> SessionTopicState:
        """Initialize a new session with topic-001."""
        state = SessionTopicState(
            session_id=session_id,
            current_topic_id="topic-001",
            topic_counter=1,
            topic_labels={"topic-001": topic_label},
        )
        await self.set_session_state(state)
        return state

    async def advance_topic(self, session_id: str, new_label: str) -> SessionTopicState:
        """Create a new topic in the session (topic shift)."""
        state = await self.get_session_state(session_id)
        if state is None:
            return await self.init_session(session_id, new_label)

        new_counter = state.topic_counter + 1
        new_topic_id = f"topic-{new_counter:03d}"
        state.topic_counter = new_counter
        state.current_topic_id = new_topic_id
        state.topic_labels[new_topic_id] = new_label
        await self.set_session_state(state)
        return state

    # ------------------------------------------------------------------
    # Message storage
    # ------------------------------------------------------------------

    async def store_message(self, msg: Message) -> str:
        """Store a message and index it under its topic. Returns message_id."""
        msg_key = f"msg:{msg.message_id}"
        topic_key = f"session:{msg.session_id}:topic:{msg.topic_id}:msgs"
        pipe = self._redis.pipeline()
        pipe.hset(
            msg_key,
            mapping={
                "session_id": msg.session_id,
                "role": msg.role,
                "topic_id": msg.topic_id,
                "content": msg.content,
                "timestamp": str(msg.timestamp),
                "parent_message_id": msg.parent_message_id or "",
                "user_id": msg.user_id or "",
            },
        )
        pipe.rpush(topic_key, msg.message_id)
        # Expire message key with session TTL
        pipe.expire(msg_key, self._session_ttl)
        await pipe.execute()
        await self._reset_session_ttl(msg.session_id)
        return msg.message_id

    async def get_message(self, message_id: str) -> Message | None:
        """Retrieve a single message by ID."""
        data = await self._redis.hgetall(f"msg:{message_id}")
        if not data:
            return None
        return Message(
            message_id=message_id,
            session_id=data["session_id"],
            role=data["role"],
            topic_id=data["topic_id"],
            content=data["content"],
            timestamp=float(data["timestamp"]),
            parent_message_id=data.get("parent_message_id") or None,
            user_id=data.get("user_id") or None,
        )

    async def get_topic_messages(
        self,
        session_id: str,
        topic_id: str,
        max_messages: int | None = None,
    ) -> list[Message]:
        """Retrieve messages for a topic in chronological order."""
        key = f"session:{session_id}:topic:{topic_id}:msgs"
        if max_messages:
            # Get the last N message IDs
            msg_ids = await self._redis.lrange(key, -max_messages, -1)
        else:
            msg_ids = await self._redis.lrange(key, 0, -1)

        if not msg_ids:
            return []

        # Batch fetch all messages in a single pipeline (avoid N+1)
        pipe = self._redis.pipeline()
        for mid in msg_ids:
            pipe.hgetall(f"msg:{mid}")
        results = await pipe.execute()

        messages = []
        for mid, data in zip(msg_ids, results):
            if data:
                messages.append(
                    Message(
                        message_id=mid,
                        session_id=data["session_id"],
                        role=data["role"],
                        topic_id=data["topic_id"],
                        content=data["content"],
                        timestamp=float(data["timestamp"]),
                        parent_message_id=data.get("parent_message_id") or None,
                        user_id=data.get("user_id") or None,
                    )
                )
        return messages

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def generate_message_id() -> str:
        """Generate a unique message ID."""
        return f"msg-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def now_timestamp() -> float:
        """Return current UTC timestamp."""
        return time.time()

    @staticmethod
    def format_timestamp(ts: float) -> str:
        """Format a Unix timestamp as ISO 8601."""
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    async def _reset_session_ttl(self, session_id: str) -> None:
        """Reset TTL on known session keys (deterministic, no SCAN)."""
        sid = session_id
        # Core session keys (always exist)
        keys = [
            f"session:{sid}:topic:current",
            f"session:{sid}:topic:counter",
            f"session:{sid}:topic:labels",
        ]
        # Topic message lists — get all known topic IDs from labels
        labels = await self._redis.hgetall(f"session:{sid}:topic:labels")
        for topic_id in labels:
            keys.append(f"session:{sid}:topic:{topic_id}:msgs")

        pipe = self._redis.pipeline()
        for key in keys:
            pipe.expire(key, self._session_ttl)
        await pipe.execute()
