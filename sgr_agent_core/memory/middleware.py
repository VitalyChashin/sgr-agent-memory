"""Memory middleware — pre/post-processing layer for topic-aware memory."""

from __future__ import annotations

import logging

import httpx
from openai.types.chat import ChatCompletionMessageParam

from sgr_agent_core.memory.client import MemoryServiceClient
from sgr_agent_core.memory.config import MemoryConfig
from sgr_agent_core.memory.models import PreprocessResult, TopicMetadata

logger = logging.getLogger(__name__)


class MemoryMiddleware:
    """Pre/post-processing layer for topic-aware memory.

    Initialized once during server lifespan.  Called per-request
    from the chat completion endpoint.
    """

    def __init__(self, config: MemoryConfig, client: MemoryServiceClient) -> None:
        self._config = config
        self._client = client

    # ------------------------------------------------------------------
    # Preprocess
    # ------------------------------------------------------------------

    async def preprocess(
        self,
        messages: list[ChatCompletionMessageParam],
        session_id: str | None,
        user_id: str | None = None,
    ) -> PreprocessResult:
        """Store user message and retrieve topic-filtered context.

        Returns the original messages untouched when memory is disabled,
        ``session_id`` is absent, or the memory service is unavailable.
        """
        # Skip conditions (US2)
        if not self._config.enabled or not session_id:
            return PreprocessResult(messages=messages)

        # Extract last user message content
        content = self._extract_last_user_content(messages)
        if not content:
            return PreprocessResult(messages=messages)

        try:
            ctx = await self._client.get_context(
                session_id=session_id,
                content=content,
                user_id=user_id,
                max_messages=self._config.max_messages,
            )
        except httpx.TimeoutException:
            logger.warning("Memory service timeout for session=%s — falling back to raw messages", session_id)
            return PreprocessResult(messages=messages)
        except httpx.ConnectError:
            logger.warning("Memory service unreachable for session=%s — falling back to raw messages", session_id)
            return PreprocessResult(messages=messages)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Memory service HTTP %s for session=%s — falling back to raw messages",
                exc.response.status_code,
                session_id,
            )
            return PreprocessResult(messages=messages)
        except Exception:
            logger.exception("Unexpected memory error for session=%s — falling back to raw messages", session_id)
            return PreprocessResult(messages=messages)

        # Convert ContextMessage list → ChatCompletionMessageParam list
        filtered: list[ChatCompletionMessageParam] = [
            {"role": msg.role, "content": msg.content} for msg in ctx.messages
        ]

        topic_metadata = TopicMetadata(
            topic_id=ctx.current_topic_id,
            topic_label=ctx.current_topic_label,
            topic_shift=ctx.topic_shift,
        )

        if ctx.topic_shift:
            logger.info(
                "Topic shift detected: topic_id=%s topic_label=%r session=%s",
                ctx.current_topic_id,
                ctx.current_topic_label,
                session_id,
            )

        return PreprocessResult(
            messages=filtered,
            topic_metadata=topic_metadata,
            user_message_id=ctx.user_message_id,
            used_memory=True,
        )

    # ------------------------------------------------------------------
    # Postprocess
    # ------------------------------------------------------------------

    async def postprocess(
        self,
        session_id: str,
        user_message_id: str,
        assistant_content: str,
        user_id: str | None = None,
    ) -> None:
        """Store assistant response (fire-and-forget).

        Errors are logged but never raised.
        """
        if not user_message_id or not assistant_content:
            return

        try:
            result = await self._client.store_message(
                session_id=session_id,
                role="assistant",
                content=assistant_content,
                parent_message_id=user_message_id,
                user_id=user_id,
            )
            logger.debug(
                "Stored assistant response: message_id=%s topic_id=%s",
                result.message_id,
                result.topic_id,
            )
        except Exception:
            logger.warning(
                "Failed to store assistant response for session=%s — skipping",
                session_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_last_user_content(messages: list[ChatCompletionMessageParam]) -> str | None:
        """Return the text content of the last user message, or None."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content or None
                # Handle list-of-parts format
                if isinstance(content, list):
                    parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    text = " ".join(parts).strip()
                    return text or None
        return None
