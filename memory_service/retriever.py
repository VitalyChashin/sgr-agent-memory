"""Topic-filtered message retrieval logic."""

from __future__ import annotations

from memory_service.models import ContextMessage
from memory_service.storage import RedisStorage


class Retriever:
    """Retrieves topic-filtered messages from storage."""

    def __init__(self, storage: RedisStorage) -> None:
        self._storage = storage

    async def get_topic_context(
        self,
        session_id: str,
        topic_id: str,
        max_messages: int | None = None,
    ) -> list[ContextMessage]:
        """Retrieve messages for a topic as ContextMessage list."""
        messages = await self._storage.get_topic_messages(session_id, topic_id, max_messages)
        return [
            ContextMessage(
                message_id=msg.message_id,
                role=msg.role,
                content=msg.content,
                topic_id=msg.topic_id,
                timestamp=RedisStorage.format_timestamp(msg.timestamp),
            )
            for msg in messages
        ]
